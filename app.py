from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from neo4j.exceptions import AuthError, Neo4jError, ServiceUnavailable

load_dotenv()

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from agent import ask_narrative_mri
from cleanup_review import (
    apply_approved_warning_edits,
    cleanup_warning_widget_id,
    plain_english_fix_reason,
    summarize_graph_delta,
    warning_check_title,
    warning_json_location,
    warning_verify_guidance,
)
from ingest import build_system_prompt, extract_scenes
from lead_resolution import resolve_primary_character_id, top_characters_k
from lexicon import build_master_lexicon
from metrics import (
    get_driver,
    get_narrative_momentum_by_scene,
    get_passivity_in_scene_window,
    get_payoff_prop_timelines,
    get_script_act_bounds,
    get_structural_load_snapshot,
    get_top_characters_by_interaction_count,
)
from neo4j_loader import load_entries, wipe_screenplay_graph_keep_pipeline_runs
from parser import parse_fdx_to_raw_scenes, write_raw_scenes_json
from pipeline_runs import list_pipeline_runs, save_pipeline_run
from reconcile import (
    ReconciliationScan,
    merge_characters,
    merge_entities,
    run_reconciliation_scan,
)
from data_out import (
    DEMO_QUERY_SPECS,
    graph_schema_card_markdown,
    get_label_counts,
    get_rel_type_counts,
    rows_characters,
    rows_events,
    rows_narrative_edges,
    run_demo_query,
)

_log = logging.getLogger(__name__)

ROLLING_SCENES = 3
PAYOFF_MIN_SCENE_GAP = 10

_PROJECT_ROOT = Path(__file__).resolve().parent
_TARGET_FDX = _PROJECT_ROOT / "target_script.fdx"
# Bump when you ship pipeline/agent optimizations (tracked in efficiency tab).
AGENT_OPTIMIZATION_VERSION = 0

_PIPELINE_JSON_NAMES = (
    "raw_scenes.json",
    "master_lexicon.json",
    "validated_graph.json",
    "pipeline_state.json",
)


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


_PIPELINE_ENABLED = not _env_truthy("DISABLE_PIPELINE")
# CEO / technical-demo tab order: Verify → Data out → Reconcile → … (see README / .env.example).
_SCRIPTRAG_DEMO_LAYOUT = _env_truthy("SCRIPTRAG_DEMO_LAYOUT")


def _persist_pipeline_run(
    *,
    scenes_extracted: int,
    total_scenes: int,
    corrections_count: int,
    warnings_count: int,
    telemetry_tokens: int,
    telemetry_cost_usd: float,
    failed_scenes: int,
    llm_auditors_enabled: bool,
) -> bool:
    drv = None
    try:
        drv = get_driver()
        save_pipeline_run(
            drv,
            scenes_extracted=scenes_extracted,
            total_scenes=total_scenes,
            corrections_count=corrections_count,
            warnings_count=warnings_count,
            telemetry_tokens=telemetry_tokens,
            telemetry_cost_usd=telemetry_cost_usd,
            agent_optimization_version=AGENT_OPTIMIZATION_VERSION,
            failed_scenes=failed_scenes,
            llm_auditors_enabled=llm_auditors_enabled,
        )
        return True
    except Exception:
        return False
    finally:
        if drv is not None:
            drv.close()


# ---------------------------------------------------------------------------
# Neo4j dashboard caching
# ---------------------------------------------------------------------------

def _neo4j_dashboard_cache_stamp() -> tuple[float, float]:
    vg = _PROJECT_ROOT / "validated_graph.json"
    ps = _PROJECT_ROOT / "pipeline_state.json"

    def _mt(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    return (_mt(vg), _mt(ps))


def _act_bounds_six(b: dict[str, Any]) -> tuple[int, int, int, int, int, int]:
    (a1l, a1h), (a2l, a2h), (a3l, a3h) = b["act1"], b["act2"], b["act3"]
    return (int(a1l), int(a1h), int(a2l), int(a2h), int(a3l), int(a3h))


def _wipe_dashboard_neo4j_keep_pipeline_runs() -> None:
    """Clear screenplay graph only; **:PipelineRun** efficiency rows stay."""
    drv = get_driver()
    try:
        wipe_screenplay_graph_keep_pipeline_runs(drv)
    finally:
        drv.close()


def _delete_pipeline_json_files() -> None:
    for name in _PIPELINE_JSON_NAMES:
        p = _PROJECT_ROOT / name
        if p.is_file():
            p.unlink()


def _render_pipeline_corrections(corrections: list[dict[str, Any]]) -> None:
    """Show fixer/audit repair trail where extraction happened (Pipeline tab)."""
    st.subheader("Self-healing corrections")
    st.caption(
        "During **Stage 3**, some scenes needed a **rewrite** after rules or auditors reported errors. "
        "This is the same graph the pipeline kept — nothing to approve here. Use **Verify** for **warnings** only."
    )
    for corr in corrections:
        if not isinstance(corr, dict):
            continue
        sn = corr.get("scene_number", "?")
        heading = corr.get("heading") or "untitled"
        audit_entries = corr.get("audit_entries")
        if not isinstance(audit_entries, list):
            continue
        with st.expander(f"Scene {sn} — {heading}", expanded=len(corrections) <= 3):
            for entry in audit_entries:
                node = entry.get("node", "?")
                detail = entry.get("detail", "")

                if node in ("fixer", "audit_fixer"):
                    label = "Audit fixer" if node == "audit_fixer" else "Rules / schema fixer"
                    reason = str(entry.get("reason") or "")
                    st.markdown(f"**{label}** · attempt {entry.get('attempt', '?')}")
                    st.markdown("**Why it failed validation**")
                    st.write(plain_english_fix_reason(reason))
                    if reason and len(reason) < 600:
                        with st.expander("Raw validator message"):
                            st.code(reason, language="text")
                    before_g = entry.get("before") or {}
                    after_g = entry.get("after") or {}
                    if isinstance(before_g, dict) and isinstance(after_g, dict):
                        bsum, asum = summarize_graph_delta(before_g, after_g)
                        c1, c2 = st.columns(2)
                        with c1:
                            st.markdown("**Before — extractor output**")
                            st.markdown(bsum)
                        with c2:
                            st.markdown("**After — self-healed graph**")
                            st.markdown(asum)
                elif node == "audit" and entry.get("findings"):
                    st.markdown(
                        f"**Auditor pass** — {entry.get('error_count', 0)} error(s), "
                        f"{entry.get('warning_count', 0)} warning(s) recorded"
                    )
                    for f in entry["findings"]:
                        icon = "Error" if f.get("severity") == "error" else "Warning"
                        st.markdown(f"*{icon}* · **{f.get('check', '?')}** — {f.get('detail', '')}")
                        if f.get("suggestion"):
                            st.caption(f"Suggestion: {f['suggestion']}")
                elif entry.get("error"):
                    st.markdown(f"**{node}** — {detail}")
                    st.code(entry["error"], language="text")
                else:
                    st.markdown(f"**{node}** — {detail}")
                st.divider()


# ---------------------------------------------------------------------------
# Neo4j cached queries
# ---------------------------------------------------------------------------

@st.cache_data(ttl=120, show_spinner="Loading narrative momentum…")
def _cached_momentum_rows(_artifact_stamp: tuple[float, float]) -> list[dict[str, Any]]:
    del _artifact_stamp
    drv = get_driver()
    try:
        try:
            return get_narrative_momentum_by_scene(driver=drv)
        except (Neo4jError, ServiceUnavailable, AuthError, OSError):
            _log.exception("Cached narrative momentum load failed")
            return []
        except Exception:
            _log.exception("Cached narrative momentum load failed")
            return []
    finally:
        drv.close()


@st.cache_data(ttl=120, show_spinner="Loading payoff prop arcs…")
def _cached_payoff_props(_artifact_stamp: tuple[float, float]) -> list[dict[str, Any]]:
    del _artifact_stamp
    drv = get_driver()
    try:
        try:
            return get_payoff_prop_timelines(min_scene_gap=PAYOFF_MIN_SCENE_GAP, driver=drv)
        except (Neo4jError, ServiceUnavailable, AuthError, OSError):
            _log.exception("Cached payoff prop timelines load failed")
            return []
        except Exception:
            _log.exception("Cached payoff prop timelines load failed")
            return []
    finally:
        drv.close()


@st.cache_data(ttl=120, show_spinner="Loading character interaction ranks…")
def _cached_top_characters(_artifact_stamp: tuple[float, float], top_k: int) -> list[dict[str, Any]]:
    del _artifact_stamp
    drv = get_driver()
    try:
        try:
            return get_top_characters_by_interaction_count(top_k, driver=drv)
        except (Neo4jError, ServiceUnavailable, AuthError, OSError):
            _log.exception("Cached top characters load failed")
            return []
        except Exception:
            _log.exception("Cached top characters load failed")
            return []
    finally:
        drv.close()


@st.cache_data(ttl=120, show_spinner="Resolving primary lead…")
def _cached_primary_lead(_artifact_stamp: tuple[float, float]) -> tuple[str | None, bool]:
    del _artifact_stamp
    drv = get_driver()
    try:
        try:
            override = bool(os.environ.get("SCRIPTRAG_PRIMARY_LEAD_ID", "").strip())
            pid = resolve_primary_character_id(driver=drv)
            return (pid, override)
        except (Neo4jError, ServiceUnavailable, AuthError, OSError):
            _log.exception("Cached primary lead resolution failed")
            return (None, False)
        except Exception:
            _log.exception("Cached primary lead resolution failed")
            return (None, False)
    finally:
        drv.close()


@st.cache_data(ttl=120, show_spinner="Loading script act bounds…")
def _cached_act_bounds(_artifact_stamp: tuple[float, float]) -> dict[str, Any] | None:
    del _artifact_stamp
    drv = get_driver()
    try:
        try:
            return get_script_act_bounds(driver=drv)
        except (Neo4jError, ServiceUnavailable, AuthError, OSError):
            _log.exception("Cached script act bounds load failed")
            return None
        except Exception:
            _log.exception("Cached script act bounds load failed")
            return None
    finally:
        drv.close()


@st.cache_data(ttl=120, show_spinner="Computing act passivity…")
def _cached_act_passivity_matrix(
    _artifact_stamp: tuple[float, float],
    char_ids: tuple[str, ...],
    act_bounds_key: tuple[int, int, int, int, int, int] | None,
) -> dict[str, list[float | None]]:
    del _artifact_stamp
    if act_bounds_key is None:
        return {}
    act1_lo, act1_hi, act2_lo, act2_hi, act3_lo, act3_hi = act_bounds_key
    drv = get_driver()
    try:
        try:
            out: dict[str, list[float | None]] = {}
            for cid in char_ids:
                a1 = get_passivity_in_scene_window(cid, act1_lo, act1_hi, driver=drv)
                a2 = get_passivity_in_scene_window(cid, act2_lo, act2_hi, driver=drv)
                a3 = get_passivity_in_scene_window(cid, act3_lo, act3_hi, driver=drv)
                out[cid] = [
                    a1.get("passivity"),
                    a2.get("passivity"),
                    a3.get("passivity"),
                ]
            return out
        except (Neo4jError, ServiceUnavailable, AuthError, OSError):
            _log.exception("Cached act passivity matrix failed")
            return {}
        except Exception:
            _log.exception("Cached act passivity matrix failed")
            return {}
    finally:
        drv.close()


@st.cache_data(ttl=120, show_spinner="Counting Neo4j events…")
def _cached_event_count(_artifact_stamp: tuple[float, float]) -> int:
    del _artifact_stamp
    drv = get_driver()
    try:
        try:
            with drv.session() as session:
                rec = session.run("MATCH (e:Event) RETURN count(e) AS c").single()
                if rec is None:
                    return 0
                raw = rec.get("c", 0)
                return int(raw) if raw is not None else 0
        except (Neo4jError, ServiceUnavailable, AuthError, OSError):
            _log.exception("Cached event count failed")
            return 0
        except Exception:
            _log.exception("Cached event count failed")
            return 0
    finally:
        drv.close()


@st.cache_data(ttl=120, show_spinner="Loading structural load snapshot…")
def _cached_structural_load(_artifact_stamp: tuple[float, float]) -> dict[str, Any]:
    del _artifact_stamp
    drv = get_driver()
    try:
        try:
            return get_structural_load_snapshot(driver=drv)
        except Exception:
            _log.exception("Cached structural load snapshot failed")
            return {
                "scene_count": 0,
                "character_count": 0,
                "location_count": 0,
                "prop_count": 0,
                "narrative_edge_count": 0,
                "structural_load_index": 0.0,
            }
    finally:
        drv.close()


@st.cache_data(ttl=120, show_spinner="Scanning graph for reconciliation…")
def _cached_reconciliation_scan(
    _artifact_stamp: tuple[float, float],
    min_similarity: float,
) -> ReconciliationScan:
    del _artifact_stamp
    drv = get_driver()
    try:
        try:
            return run_reconciliation_scan(drv, min_similarity=min_similarity)
        except Exception:
            _log.exception("Cached reconciliation scan failed")
            return ReconciliationScan(
                ghost_characters=[],
                fuzzy_character_pairs=[],
                fuzzy_location_pairs=[],
            )
    finally:
        drv.close()


@st.cache_data(ttl=None)
def _cached_label_counts(_artifact_stamp: tuple[float, float]) -> list[dict[str, Any]]:
    del _artifact_stamp
    drv = get_driver()
    try:
        with drv.session() as s:
            return get_label_counts(s)
    except Exception:
        _log.exception("Data out: label counts failed")
        return []
    finally:
        drv.close()


@st.cache_data(ttl=None)
def _cached_rel_type_counts(_artifact_stamp: tuple[float, float]) -> list[dict[str, Any]]:
    del _artifact_stamp
    drv = get_driver()
    try:
        with drv.session() as s:
            return get_rel_type_counts(s)
    except Exception:
        _log.exception("Data out: relationship type counts failed")
        return []
    finally:
        drv.close()


@st.cache_data(ttl=None)
def _cached_demo_query(
    _artifact_stamp: tuple[float, float], query_key: str
) -> list[dict[str, Any]]:
    del _artifact_stamp
    drv = get_driver()
    try:
        with drv.session() as s:
            return run_demo_query(s, query_key)
    except Exception:
        _log.exception("Data out: demo query failed")
        return []
    finally:
        drv.close()


@st.cache_data(ttl=None)
def _cached_export_edges(
    _artifact_stamp: tuple[float, float], limit: int
) -> list[dict[str, Any]]:
    del _artifact_stamp
    drv = get_driver()
    try:
        with drv.session() as s:
            return rows_narrative_edges(s, limit=int(limit))
    except Exception:
        _log.exception("Data out: narrative edge export failed")
        return []
    finally:
        drv.close()


@st.cache_data(ttl=None)
def _cached_export_characters(_artifact_stamp: tuple[float, float]) -> list[dict[str, Any]]:
    del _artifact_stamp
    drv = get_driver()
    try:
        with drv.session() as s:
            return rows_characters(s)
    except Exception:
        _log.exception("Data out: character export failed")
        return []
    finally:
        drv.close()


@st.cache_data(ttl=None)
def _cached_export_events(_artifact_stamp: tuple[float, float]) -> list[dict[str, Any]]:
    del _artifact_stamp
    drv = get_driver()
    try:
        with drv.session() as s:
            return rows_events(s)
    except Exception:
        _log.exception("Data out: event export failed")
        return []
    finally:
        drv.close()


# ---------------------------------------------------------------------------
# Chart rendering (unchanged logic)
# ---------------------------------------------------------------------------

def _render_momentum_chart(
    rows: list[dict[str, Any]],
    act_bounds: dict[str, Any] | None,
) -> None:
    st.subheader("Narrative Momentum (rolling pacing)")
    cap = (
        "Per-scene **heat** = `CONFLICTS_WITH / (INTERACTS_WITH + CONFLICTS_WITH)` among entities "
        "co-present in the scene. **Momentum** = trailing **3-scene** mean of that heat (smoothed trend)."
    )
    if act_bounds:
        a1, a2, a3 = act_bounds["act1"], act_bounds["act2"], act_bounds["act3"]
        b1, b2 = act_bounds["break_after_act1_scene"], act_bounds["break_after_act2_scene"]
        cap += (
            f" **Scene span** from Neo4j: **{act_bounds['min_scene']}–{act_bounds['max_scene']}** "
            f"({act_bounds['scene_count']} scenes). Act buckets = equal thirds of that span "
            f"(Act 1 **{a1[0]}–{a1[1]}**, Act 2 **{a2[0]}–{a2[1]}**, Act 3 **{a3[0]}–{a3[1]}**). "
        )
        if a2[0] > a1[1]:
            cap += f" Dashed lines: first scene of Act 2 (**{b1}**)"
            if a3[0] > a2[1]:
                cap += f", first scene of Act 3 (**{b2}**)."
            else:
                cap += "."
        else:
            cap += " (Single-scene script — no act dividers.)"
    else:
        cap += " No :Event nodes in Neo4j — act dividers omitted."
    st.caption(cap)
    if not rows:
        st.info("No :Event data — run the pipeline and load Neo4j.")
        return

    df = pd.DataFrame(rows)
    if "scene_number" not in df.columns:
        st.warning("Momentum query returned no scene numbers.")
        return
    if "heat" not in df.columns or df["heat"].isna().all():
        st.warning("Momentum data missing heat values.")
        return

    df = df.sort_values("scene_number")
    df["heat_num"] = pd.to_numeric(df["heat"], errors="coerce")
    df["momentum"] = df["heat_num"].rolling(window=ROLLING_SCENES, min_periods=1).mean()

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["scene_number"],
            y=df["momentum"],
            mode="lines",
            name=f"Momentum ({ROLLING_SCENES}-scene avg)",
            line=dict(color="#2563eb", width=2.5),
            fill="tozeroy",
            fillcolor="rgba(37, 99, 235, 0.18)",
            hovertemplate="Scene %{x}<br>momentum=%{y:.4f}<extra></extra>",
        )
    )
    if act_bounds:
        a1, a2, a3 = act_bounds["act1"], act_bounds["act2"], act_bounds["act3"]
        b1, b2 = act_bounds["break_after_act1_scene"], act_bounds["break_after_act2_scene"]
        if a2[0] > a1[1]:
            fig.add_vline(
                x=b1,
                line_width=2,
                line_dash="dash",
                line_color="#64748b",
                annotation_text="Act 2 begins",
                annotation_position="top",
            )
        if a3[0] > a2[1] and b2 != b1:
            fig.add_vline(
                x=b2,
                line_width=2,
                line_dash="dash",
                line_color="#64748b",
                annotation_text="Act 3 begins",
                annotation_position="top",
            )
    fig.update_layout(
        template="plotly_white",
        height=420,
        xaxis_title="Scene number",
        yaxis_title="Momentum (smoothed heat)",
        yaxis_range=[0, max(0.55, float(df["momentum"].max()) * 1.15) if df["momentum"].notna().any() else 1],
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=50),
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_payoff_matrix(props: list[dict[str, Any]]) -> None:
    st.subheader("The Payoff Matrix (Long-Term Plot Devices)")
    st.caption(
        f"Props with **first on-screen intro** (earliest `IN_SCENE` or co-scene `POSSESSES`) and **last narrative use** "
        f"(`USES` / `CONFLICTS_WITH` in-scene) separated by **>{PAYOFF_MIN_SCENE_GAP}** scenes — filters short-loop noise."
    )
    if not props:
        st.info("No long-arc props match this filter (or graph is empty).")
        return

    df = pd.DataFrame(props)
    _payoff_cols = {"id", "first_scene", "last_scene", "gap"}
    if not _payoff_cols.issubset(df.columns):
        st.warning("Payoff data missing expected columns.")
        return
    df["label"] = df.apply(
        lambda r: f"{r.get('name') or r['id']} ({r['id']})" if r.get("name") != r.get("id") else str(r["id"]),
        axis=1,
    )
    span = (df["last_scene"] - df["first_scene"]).clip(lower=0.01)

    _cd = list(zip(df["last_scene"].tolist(), df["gap"].tolist()))
    fig = go.Figure(
        go.Bar(
            y=df["label"],
            x=span,
            base=df["first_scene"],
            orientation="h",
            marker_color="#0d9488",
            text=df.apply(lambda r: f"{int(r['first_scene'])}→{int(r['last_scene'])}", axis=1),
            textposition="outside",
            hovertemplate="%{y}<br>scenes %{base} → %{customdata[0]}<br>gap %{customdata[1]}<extra></extra>",
            customdata=_cd,
        )
    )
    fig.update_layout(
        template="plotly_white",
        height=max(360, min(900, 28 * len(df))),
        xaxis_title="Scene number (bar spans first → last use)",
        yaxis_title="",
        margin=dict(l=24, r=80, t=40, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_power_shift(
    top_chars: list[dict[str, Any]],
    matrix: dict[str, list[float | None]],
    act_bounds: dict[str, Any] | None,
    top_k: int,
) -> None:
    st.subheader("Power shift — agency by act")
    cap = (
        f"Passivity index (in-degree / total degree on `CONFLICTS_WITH` + `USES`, same as MRI metrics) "
        f"for the **{top_k}** characters with the most interaction edges. "
    )
    if act_bounds:
        a1, a2, a3 = act_bounds["act1"], act_bounds["act2"], act_bounds["act3"]
        cap += (
            f"Act ranges follow **equal thirds** of Neo4j scene span **{act_bounds['min_scene']}–{act_bounds['max_scene']}**: "
            f"**Act 1** {a1[0]}–{a1[1]}, **Act 2** {a2[0]}–{a2[1]}, **Act 3** {a3[0]}–{a3[1]}."
        )
    else:
        cap += "No :Event nodes — cannot bucket by act."
    st.caption(cap)
    if not top_chars:
        st.info("No characters with interaction edges found.")
        return
    valid_chars = [c for c in top_chars if isinstance(c, dict) and c.get("id") is not None]
    if not valid_chars:
        st.warning("Character rank data is missing ids — cannot chart power shift.")
        return
    if not act_bounds or not matrix:
        st.info("No :Event scene span in Neo4j — load events to chart act passivity.")
        return

    act_labels = [
        f"Act 1 ({act_bounds['act1'][0]}–{act_bounds['act1'][1]})",
        f"Act 2 ({act_bounds['act2'][0]}–{act_bounds['act2'][1]})",
        f"Act 3 ({act_bounds['act3'][0]}–{act_bounds['act3'][1]})",
    ]
    fig = go.Figure()
    palette = ["#2563eb", "#dc2626", "#ca8a04", "#7c3aed", "#059669"]
    for i, c in enumerate(valid_chars):
        cid = str(c["id"])
        series = matrix.get(cid, [None, None, None])
        fig.add_trace(
            go.Scatter(
                x=act_labels,
                y=series,
                mode="lines+markers",
                name=f"{c.get('name') or cid} ({cid})",
                line=dict(width=2, color=palette[i % len(palette)]),
                marker=dict(size=10),
                connectgaps=False,
            )
        )
    fig.update_layout(
        template="plotly_white",
        height=440,
        yaxis_title="Passivity (higher = more reactive)",
        yaxis_range=[0, 1],
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=60),
    )
    st.plotly_chart(fig, use_container_width=True)


def _primary_lead_regression_warning(
    matrix: dict[str, list[float | None]],
    primary_id: str | None,
    is_override: bool,
) -> None:
    if primary_id is None:
        st.info(
            "No primary lead resolved — no characters with interaction edges in Neo4j. "
            "Arc regression check skipped."
        )
        return
    primary_key = next((k for k in matrix if k.lower() == primary_id.lower()), None)
    if primary_key is None:
        src = "configured" if is_override else "analysis"
        st.info(
            f"Primary lead id `{primary_id}` ({src}) is not in the act passivity matrix — "
            "regression check skipped."
        )
        return
    row = matrix.get(primary_key)
    if not row or len(row) < 3:
        return
    p1, _, p3 = row[0], row[1], row[2]
    if p1 is None or p3 is None:
        return
    if float(p3) > float(p1):
        st.warning(
            "**FATAL ARC:** The primary lead is regressing — **Act 3 passivity exceeds Act 1** "
            f"({float(p3):.3f} vs {float(p1):.3f} for **{primary_key}**)."
        )


# ===================================================================
# Page config & layout
# ===================================================================

st.set_page_config(
    page_title="ScriptRAG",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --------------- data loads (cached) --------------------------------
_DASH_STAMP = _neo4j_dashboard_cache_stamp()
_TOP_K = top_characters_k()
momentum_rows = _cached_momentum_rows(_DASH_STAMP)
payoff_rows = _cached_payoff_props(_DASH_STAMP)
top_chars = _cached_top_characters(_DASH_STAMP, _TOP_K)
_act_bounds = _cached_act_bounds(_DASH_STAMP)
_act_bounds_key = _act_bounds_six(_act_bounds) if _act_bounds else None
_primary_id, _primary_override = _cached_primary_lead(_DASH_STAMP)
_ids_tuple = tuple(
    str(c["id"])
    for c in top_chars
    if isinstance(c, dict) and c.get("id") is not None
)
_extra_ids = list(_ids_tuple)
if _primary_id:
    _extra_ids.append(_primary_id)
_extra = tuple(dict.fromkeys(_extra_ids))
_act_matrix = _cached_act_passivity_matrix(_DASH_STAMP, _extra, _act_bounds_key)
_event_count = _cached_event_count(_DASH_STAMP)
_structural_load = _cached_structural_load(_DASH_STAMP)

# --------------- header ---------------------------------------------
st.title("ScriptRAG")
st.caption(
    "Upload a screenplay, extract a knowledge graph with a self-healing AI pipeline, "
    "review corrections in **Pipeline**, **Verify** warnings, then explore the data."
)

if _flash := st.session_state.pop("_flash", None):
    st.success(_flash)

# --------------- sidebar --------------------------------------------
with st.sidebar:
    st.header("Controls")
    if _SCRIPTRAG_DEMO_LAYOUT:
        st.caption(
            "**Demo layout** (`SCRIPTRAG_DEMO_LAYOUT=1`): tabs emphasize **Verify → Data out** before reconcile "
            "and analytics — for pipeline storytelling."
        )
    with st.expander("Primary lead", expanded=False):
        if _primary_id:
            _src = (
                "`SCRIPTRAG_PRIMARY_LEAD_ID` override"
                if _primary_override
                else "Analysis: rank #1 by interaction edge count"
            )
            st.caption(f"**{_primary_id}** — {_src}")
        else:
            st.caption("None resolved (empty graph or no interaction edges).")
    if st.button(
        "Reload metrics from Neo4j",
        help="Clears Streamlit cache after pipeline or external graph edits.",
        key="sidebar_reload",
    ):
        st.cache_data.clear()
        st.session_state["_flash"] = "Cache cleared — re-querying Neo4j."
        st.rerun()

    with st.expander("Reset dashboard data", expanded=False):
        st.caption(
            "Clears the **screenplay graph** in Neo4j (what **Dashboard** / **Investigate** read) and removes "
            "pipeline JSON on disk. **:PipelineRun** rows are kept — **Pipeline Efficiency Tracking** history stays."
        )
        if st.button("Clear graph & pipeline files", key="sidebar_nuke"):
            try:
                _wipe_dashboard_neo4j_keep_pipeline_runs()
                _delete_pipeline_json_files()
            except Exception as exc:
                st.error(f"Reset failed: {exc}")
            else:
                st.session_state.pop("pipeline_results", None)
                st.cache_data.clear()
                st.session_state["_flash"] = (
                    "Neo4j screenplay graph cleared (PipelineRun history kept); pipeline JSON removed."
                )
                st.rerun()

# --------------- tabs -----------------------------------------------
_tab_labels: list[str] = []
if _PIPELINE_ENABLED:
    _tab_labels.append("Pipeline")
if _SCRIPTRAG_DEMO_LAYOUT:
    _tab_labels += [
        "Verify",
        "Data out",
        "Reconcile",
        "Pipeline Efficiency Tracking",
        "Dashboard",
        "Investigate",
    ]
else:
    _tab_labels += [
        "Verify",
        "Reconcile",
        "Data out",
        "Pipeline Efficiency Tracking",
        "Dashboard",
        "Investigate",
    ]

_tabs = st.tabs(_tab_labels)
_ti = 0

if _PIPELINE_ENABLED:
    tab_pipeline = _tabs[_ti]; _ti += 1
tab_editor = _tabs[_ti]; _ti += 1
if _SCRIPTRAG_DEMO_LAYOUT:
    tab_data_out = _tabs[_ti]; _ti += 1
    tab_reconcile = _tabs[_ti]; _ti += 1
else:
    tab_reconcile = _tabs[_ti]; _ti += 1
    tab_data_out = _tabs[_ti]; _ti += 1
tab_efficiency = _tabs[_ti]; _ti += 1
tab_dashboard = _tabs[_ti]; _ti += 1
tab_investigate = _tabs[_ti]; _ti += 1


# ===================================================================
# TAB: Pipeline
# ===================================================================

if _PIPELINE_ENABLED:
    with tab_pipeline:
        st.header("Pipeline")
        st.caption(
            "Upload a Final Draft (.fdx) screenplay then run the full extraction pipeline. "
            "Each scene runs **extract → validate → fix → optional LLM audit**; metrics are saved to Neo4j after each run."
        )

        _up = st.file_uploader(
            "Upload .fdx screenplay",
            type=["fdx"],
            help="Parsed into scenes, then each scene is sent through the LangGraph extraction pipeline.",
            key="pipeline_fdx_upload",
        )
        if _up is not None:
            _TARGET_FDX.write_bytes(_up.getvalue())
            st.success(f"Saved **{_TARGET_FDX.name}** ({len(_up.getvalue()):,} bytes)")

        with st.expander("How does this pipeline thing even work?"):
            st.markdown("""
**For each scene in your screenplay, the pipeline runs this loop:**

```
 ┌─────────────────────────────────────────────────────────────┐
 │                    PER-SCENE PIPELINE                       │
 │                                                             │
 │  ┌───────────┐     Claude + Instructor                     │
 │  │  EXTRACT  │───▶ reads scene text, returns structured    │
 │  │  (1 LLM)  │     JSON: nodes + relationships + quotes   │
 │  └─────┬─────┘                                             │
 │        ▼                                                    │
 │  ┌───────────┐     Pure Python, zero cost, instant         │
 │  │ VALIDATE  │     7 deterministic checks:                 │
 │  │ (0 LLM)   │       - fabricated quote? (substring match) │
 │  │           │       - dangling node refs?                 │
 │  │           │       - self-referencing edges?             │
 │  │           │       - wrong node types on edges?          │
 │  │           │       - duplicate LOCATED_IN?               │
 │  │           │       + lexicon drift (warn only)           │
 │  │           │       + duplicate relationships (warn only) │
 │  └─────┬─────┘                                             │
 │        │                                                    │
 │   pass │    fail                                            │
 │        │  ┌──────────┐                                      │
 │        │  │  FIXER   │  Claude rewrites the broken graph   │
 │        │  │  (1 LLM) │  with the error message as context  │
 │        │  └────┬─────┘                                      │
 │        │       └──────▶ back to VALIDATE (up to 3x)        │
 │        ▼                                                    │
 │  ┌───────────────┐  Optional: 3 specialized Claude calls   │
 │  │ LLM AUDITORS  │  that review the extraction:            │
 │  │  (3 LLM)      │    - Quote Fidelity: does quote prove  │
 │  │               │      the relationship type?             │
 │  │               │    - Completeness: any interactions     │
 │  │               │      missing from the graph?            │
 │  │               │    - Attribution: source/target right?  │
 │  └───────┬───────┘                                         │
 │          │                                                  │
 │     pass │    fail -> FIXER again (up to 2x)               │
 │          ▼                                                  │
 │   validated scene graph                                     │
 └─────────────────────────────────────────────────────────────┘
```

**What "deterministic" means:** checks 1-5 are plain Python -- no AI, no randomness.
They compare strings and sets. The hallucinated-quote check is the most powerful:
it does an exact substring search of each `source_quote` against the raw scene text.
If Claude made up a quote, this catches it every time, for free.

**What the LLM auditors add:** three separate Claude calls that catch *semantic*
errors the deterministic layer can't (e.g. a quote exists in the text but doesn't
actually prove the tagged relationship type). These are optional -- uncheck the
checkbox below to skip them for faster runs.

**Cost:** ~$0.01/scene without auditors, ~$0.03/scene with auditors.
For an 86-scene script: **~$0.85 fast** or **~$2.50 full audit**.
""")

        col_opt1, col_opt2 = st.columns(2)
        with col_opt1:
            _scene_limit = st.number_input(
                "Scene limit",
                min_value=1,
                max_value=999,
                value=86,
                help="How many scenes to process. Set low (e.g. 3–5) to test the agent on a small slice.",
                key="pipeline_scene_limit",
            )
        with col_opt2:
            _enable_audit = st.checkbox(
                "Enable LLM auditors",
                value=True,
                help="Adds 3 AI auditor calls per scene (~30 s extra). Uncheck for a faster run with deterministic checks only.",
                key="pipeline_enable_audit",
            )

        if st.button(
            "Run Pipeline",
            type="primary",
            key="pipeline_run",
            disabled=not _TARGET_FDX.is_file(),
        ):
            st.session_state["cleanup_warning_decisions"] = {}
            with st.status("Pipeline running…", expanded=True) as pipe_status:
                progress = st.progress(0, text="Starting…")
                scene_log = st.container()

                # Stage 1: Parse FDX
                pipe_status.update(label="Stage 1 — Parsing FDX…", state="running")
                progress.progress(0.05, text="Parsing FDX…")
                try:
                    raw_scenes_path = write_raw_scenes_json(_TARGET_FDX)
                    raw_scenes: list[dict[str, Any]] = json.loads(
                        raw_scenes_path.read_text(encoding="utf-8")
                    )
                    scene_log.write(f"Parsed **{len(raw_scenes)}** scenes from FDX.")
                except Exception as exc:
                    pipe_status.update(label="Parser failed", state="error")
                    st.error(f"Parser error: {exc}")
                    raw_scenes = []

                if raw_scenes:
                    # Stage 2: Build lexicon
                    pipe_status.update(label="Stage 2 — Building lexicon…", state="running")
                    progress.progress(0.10, text="Building lexicon (LLM call)…")
                    lexicon_ids: set[str] = set()
                    try:
                        master = build_master_lexicon()
                        lexicon_ids = {e.id for e in master.characters} | {e.id for e in master.locations}
                        scene_log.write(
                            f"Lexicon: **{len(master.characters)}** characters, "
                            f"**{len(master.locations)}** locations."
                        )
                        lex_obj = json.loads(
                            (_PROJECT_ROOT / "master_lexicon.json").read_text(encoding="utf-8")
                        )
                        lex_content = json.dumps(lex_obj, ensure_ascii=False, indent=2)
                        system_prompt = build_system_prompt(lex_content)
                    except Exception as exc:
                        pipe_status.update(label="Lexicon failed", state="error")
                        st.error(f"Lexicon error: {exc}")
                        raw_scenes = []

                if raw_scenes:
                    # Stage 3: Extract scenes (the big loop)
                    if _scene_limit and _scene_limit < len(raw_scenes):
                        raw_scenes = raw_scenes[:int(_scene_limit)]
                        scene_log.write(f"Scene limit: processing first **{len(raw_scenes)}** scenes.")
                    total = len(raw_scenes)
                    pipe_status.update(
                        label=f"Stage 3 — Extracting 0/{total} scenes (each takes ~30-60 s with auditors)…",
                        state="running",
                    )
                    all_entries: list[dict[str, Any]] = []
                    all_audit: list[dict[str, Any]] = []
                    all_warnings: list[dict[str, Any]] = []
                    corrections: list[dict[str, Any]] = []
                    cum_tokens = 0
                    cum_cost = 0.0
                    failed_count = 0
                    done_count = 0

                    for result in extract_scenes(
                        raw_scenes, system_prompt,
                        lexicon_ids=lexicon_ids,
                        enable_audit=_enable_audit,
                    ):
                        done_count += 1
                        frac = 0.10 + 0.85 * (result.index / total)
                        status_icon = {
                            "skip": "⏭️", "empty": "⬜", "ok": "✅",
                            "fixed": "🔧", "failed": "❌",
                        }.get(result.status, "?")
                        progress.progress(
                            frac,
                            text=f"Scene {result.index}/{total} — {result.status}",
                        )
                        pipe_status.update(
                            label=f"Stage 3 — Extracted {done_count}/{total} scenes…",
                            state="running",
                        )
                        msg = (
                            f"{status_icon} Scene **{result.scene_number}** "
                            f"({result.heading or 'untitled'}) — {result.status}"
                        )
                        if result.status == "failed" and result.error:
                            msg += f"\n\n`{result.error}`"
                        scene_log.write(msg)

                        if result.graph_entry:
                            all_entries.append(result.graph_entry)
                        all_audit.extend(result.audit_entries)
                        cum_tokens += result.tokens
                        cum_cost += result.cost

                        if result.warnings:
                            for w in result.warnings:
                                w.setdefault("scene_number", result.scene_number)
                            all_warnings.extend(result.warnings)

                        if result.status == "failed":
                            failed_count += 1
                        if result.status == "fixed":
                            corrections.append({
                                "scene_number": result.scene_number,
                                "heading": result.heading,
                                "audit_entries": result.audit_entries,
                            })

                    progress.progress(1.0, text="Done")
                    pipe_status.update(label="Pipeline complete", state="complete")

                    st.session_state["pipeline_results"] = {
                        "entries": all_entries,
                        "audit_trail": all_audit,
                        "warnings": all_warnings,
                        "corrections": corrections,
                        "total_scenes": total,
                        "extracted": len(all_entries),
                        "failed": failed_count,
                        "tokens": int(cum_tokens or 0),
                        "cost": float(cum_cost or 0.0),
                    }
                    _saved = _persist_pipeline_run(
                        scenes_extracted=len(all_entries),
                        total_scenes=total,
                        corrections_count=len(corrections),
                        warnings_count=len(all_warnings),
                        telemetry_tokens=int(cum_tokens or 0),
                        telemetry_cost_usd=float(cum_cost or 0.0),
                        failed_scenes=failed_count,
                        llm_auditors_enabled=bool(_enable_audit),
                    )
                    if not _saved:
                        st.warning(
                            "Pipeline finished but **efficiency metrics were not saved** to Neo4j "
                            "(check connection, credentials, and that the DB allows new labels)."
                        )

            pr = st.session_state.get("pipeline_results")
            if pr:
                c1, c2, c3, c4, c5 = st.columns(5)
                with c1:
                    st.metric("Scenes extracted", f"{pr['extracted']}/{pr['total_scenes']}")
                with c2:
                    st.metric("Corrections", len(pr["corrections"]))
                with c3:
                    st.metric("Warnings", len(pr.get("warnings", [])))
                with c4:
                    st.metric(
                        "Telemetry tokens",
                        f"{int(pr.get('tokens', 0) or 0):,}",
                    )
                with c5:
                    st.metric(
                        "Telemetry cost",
                        f"${float(pr.get('cost', 0.0) or 0.0):.4f}",
                    )

                if pr.get("corrections"):
                    _render_pipeline_corrections(pr["corrections"])

                if pr["corrections"] or pr.get("warnings"):
                    parts = []
                    if pr["corrections"]:
                        parts.append(f"**{len(pr['corrections'])}** scene(s) with self-healing corrections (above)")
                    if pr.get("warnings"):
                        parts.append(f"**{len(pr['warnings'])}** warning(s) to review")
                    st.info(
                        f"{' · '.join(parts)}. Open **Verify** to decide each warning, then **Approve & Load** into Neo4j."
                    )
                elif pr["extracted"] > 0:
                    st.success(
                        "All scenes passed validation on the first try. "
                        "Open **Verify** to approve and load into Neo4j."
                    )

        elif not _TARGET_FDX.is_file():
            st.info("Upload a **.fdx** file above to get started.")


# ===================================================================
# TAB: Verify (warnings + load)
# ===================================================================

with tab_editor:
    st.header("Verify")
    st.caption(
        "Review **warnings** from rules and LLM auditors. **Approve** applies the listed edit before Neo4j load "
        "(where supported); **Decline** skips it. Self-healing **corrections** are summarized in the **Pipeline** tab."
    )
    st.info(
        "**Human-in-the-loop gate.** Deterministic checks and optional auditors already ran in **Pipeline**. "
        "Here you judge each **warning** — then **Approve & load to Neo4j** commits the graph. "
        "Next: optional **Reconcile**, then **Data out**."
    )

    pr = st.session_state.get("pipeline_results")
    if not pr:
        st.info("No pipeline results yet. Run the pipeline first.")
    else:
        if "cleanup_warning_decisions" not in st.session_state:
            st.session_state["cleanup_warning_decisions"] = {}
        wd = st.session_state["cleanup_warning_decisions"]

        n_corrections = len(pr.get("corrections") or [])
        n_warnings = len(pr.get("warnings", []))
        st.markdown(
            f"**{pr['extracted']}** scenes extracted — "
            f"**{n_corrections}** with self-healing corrections (see **Pipeline**) · "
            f"**{n_warnings}** warning(s) below · "
            f"**{pr['failed']}** failed."
        )

        warnings_list = list(pr.get("warnings", []))
        entries = pr.get("entries", [])
        if warnings_list:
            st.subheader(f"Warnings to verify ({n_warnings})")
            st.caption(
                "Each card explains **what the check means** and what **Approve** does. "
                "Use **Decline** when the graph is correct as-is."
            )
            for wi, w in enumerate(warnings_list):
                if not isinstance(w, dict):
                    continue
                wid = cleanup_warning_widget_id(w, wi)
                loc = warning_json_location(w, entries)
                check_raw = str(w.get("check", "unknown"))
                title = warning_check_title(check_raw)
                detail = str(w.get("detail", "") or "")
                sev = str(w.get("severity", "") or "").strip()
                with st.container(border=True):
                    st.markdown(f"**{title}** (`{check_raw}`)")
                    if sev:
                        st.caption(f"Severity from pipeline: **{sev}**")
                    st.markdown(warning_verify_guidance(check_raw))
                    st.markdown("**What the pipeline reported**")
                    st.write(detail if detail else "_(no detail text)_")
                    st.caption("Location in extracted JSON")
                    st.code(loc, language="text")
                    current = wd.get(wid, "unset")
                    r1, r2 = st.columns(2)
                    with r1:
                        if st.button(
                            "Approve — apply fix",
                            key=f"cw_ok_{wid}",
                            type="primary" if current == "approved" else "secondary",
                        ):
                            wd[wid] = "approved"
                            st.rerun()
                    with r2:
                        if st.button(
                            "Decline — keep graph",
                            key=f"cw_no_{wid}",
                            type="primary" if current == "declined" else "secondary",
                        ):
                            wd[wid] = "declined"
                            st.rerun()
                    if current == "approved":
                        st.success("**Approved** — this edit will run on **Approve & Load**.")
                    elif current == "declined":
                        st.info("**Declined** — treated as false positive; no edit from this warning.")
        else:
            st.success("No warnings — nothing to verify. You can load below.")

        st.divider()

        entries_to_load = pr.get("entries", [])
        if not entries_to_load:
            st.warning("No scene entries to load (all scenes may have failed).")
        else:
            if st.button(
                f"Approve & Load {len(entries_to_load)} scenes into Neo4j",
                type="primary",
                key="editor_approve_load",
            ):
                with st.spinner("Applying approved warning edits & loading into Neo4j…"):
                    try:
                        to_load, edit_log = apply_approved_warning_edits(
                            entries_to_load,
                            pr.get("warnings", []),
                            wd,
                        )
                        if edit_log:
                            with st.expander("Verify edits applied before load", expanded=True):
                                for line in edit_log:
                                    st.markdown(f"- {line}")
                        loaded = load_entries(to_load)
                    except Exception as exc:
                        st.error(f"Load failed: {exc}")
                    else:
                        pr["entries"] = to_load
                        pr["warnings"] = [
                            w
                            for wi, w in enumerate(pr.get("warnings", []))
                            if wd.get(cleanup_warning_widget_id(w, wi)) != "approved"
                        ]
                        for wi, w in enumerate(warnings_list):
                            wid = cleanup_warning_widget_id(w, wi)
                            if wd.get(wid) == "approved":
                                wd.pop(wid, None)
                        st.cache_data.clear()
                        st.session_state["_flash"] = (
                            f"Loaded **{loaded}** scenes into Neo4j. Dashboard refreshed."
                        )
                        st.rerun()


# ===================================================================
# TAB: Reconcile
# ===================================================================

with tab_reconcile:
    st.header("Reconcile")
    st.caption(
        "**Optional — post-load entity hygiene.** Scan Neo4j for fuzzy duplicate **Character** / **Location** "
        "names and **ghost** characters (single scene, no conflicts). Merges **rewrite the graph** — use "
        "**dry-run** on the CLI when unsure."
    )
    with st.expander("About reconciliation", expanded=False):
        st.markdown(
            """
- **Fuzzy pairs:** Names are normalized (case, punctuation, word↔digit variants) and compared with
  **token sort ratio**. Pairs above your similarity threshold are candidates—not proof they are the same role.
- **Ghost characters:** Characters with exactly **one** `IN_SCENE` event and **no** `CONFLICTS_WITH`
  (either direction) — often under-connected extras; listed for review only (no auto-delete).
- **Merge:** One node **id** is kept; the other is removed. Relationships are moved onto the survivor via
  **APOC `mergeNodes`** when available, otherwise **manual rewire** (`reconcile.py`), matching loader-style safety
  (allowed rel types only).
- **CLI:** `uv run python reconcile.py --dry-run` lists the same classes of issues without prompts or writes.
            """
        )

    _rec_min_sim = st.slider(
        "Minimum name similarity (fuzzy pairs)",
        min_value=0.5,
        max_value=1.0,
        value=0.78,
        step=0.01,
        key="reconcile_min_sim",
        help="Higher = fewer, stricter duplicate suggestions.",
    )
    _rec_scan = _cached_reconciliation_scan(_neo4j_dashboard_cache_stamp(), _rec_min_sim)

    st.subheader("Ghost characters")
    if not _rec_scan.ghost_characters:
        st.info("None found.")
    else:
        st.dataframe(
            pd.DataFrame(_rec_scan.ghost_characters),
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("Fuzzy Character pairs")
    if not _rec_scan.fuzzy_character_pairs:
        st.info("None above threshold.")
    else:
        _cp_rows: list[dict[str, Any]] = []
        for a, b, s in _rec_scan.fuzzy_character_pairs:
            _cp_rows.append(
                {
                    "id_a": a.get("id"),
                    "name_a": a.get("name"),
                    "id_b": b.get("id"),
                    "name_b": b.get("name"),
                    "similarity": round(s, 4),
                }
            )
        st.dataframe(pd.DataFrame(_cp_rows), use_container_width=True, hide_index=True)

    st.subheader("Fuzzy Location pairs")
    if not _rec_scan.fuzzy_location_pairs:
        st.info("None above threshold.")
    else:
        _lp_rows: list[dict[str, Any]] = []
        for a, b, s in _rec_scan.fuzzy_location_pairs:
            _lp_rows.append(
                {
                    "id_a": a.get("id"),
                    "name_a": a.get("name"),
                    "id_b": b.get("id"),
                    "name_b": b.get("name"),
                    "similarity": round(s, 4),
                }
            )
        st.dataframe(pd.DataFrame(_lp_rows), use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Apply merge (writes to Neo4j)")
    st.warning(
        "Merges are **not** reversible from this app. Prefer `reconcile.py --dry-run`, backups, or Aura snapshots "
        "before merging."
    )
    _rec_ack = st.checkbox(
        "I understand this will write merges to Neo4j.",
        key="reconcile_ack_write",
    )
    _rec_kind = st.radio(
        "Pair type",
        ["Character pair", "Location pair"],
        horizontal=True,
        key="reconcile_pair_kind",
    )
    _rec_pairs = (
        _rec_scan.fuzzy_character_pairs
        if _rec_kind == "Character pair"
        else _rec_scan.fuzzy_location_pairs
    )
    if not _rec_pairs:
        st.info("No pairs of this type — lower the similarity threshold or load more graph data.")
    else:
        _rec_labels = [
            f"{a.get('name') or a.get('id')} ↔ {b.get('name') or b.get('id')}  ({s:.3f})"
            for a, b, s in _rec_pairs
        ]
        _rec_ix = st.selectbox(
            "Select pair",
            list(range(len(_rec_pairs))),
            format_func=lambda i: _rec_labels[i],
            key="reconcile_pair_index",
        )
        _rec_a, _rec_b, _ = _rec_pairs[_rec_ix]
        _rec_id_a = str(_rec_a.get("id", ""))
        _rec_id_b = str(_rec_b.get("id", ""))
        _rec_keep = st.radio(
            "Keep node id (survivor)",
            [_rec_id_a, _rec_id_b],
            horizontal=True,
            key="reconcile_keep_id",
        )
        if st.button(
            "Merge selected pair",
            type="primary",
            disabled=not _rec_ack,
            key="reconcile_merge_btn",
        ):
            _rec_drop = _rec_id_b if _rec_keep == _rec_id_a else _rec_id_a
            _rec_drv = get_driver()
            try:
                if _rec_kind == "Character pair":
                    merge_characters(_rec_drv, _rec_keep, _rec_drop)
                else:
                    merge_entities(_rec_drv, _rec_keep, _rec_drop, "Location")
            except Exception:
                _log.exception("Reconcile merge failed")
                st.error("Merge failed. Check server logs and that both ids exist in Neo4j.")
            else:
                st.cache_data.clear()
                st.session_state["_flash"] = (
                    f"Merged into **{_rec_keep}** (removed {_rec_drop}). Cache cleared."
                )
                st.rerun()


# ===================================================================
# TAB: Data out
# ===================================================================

with tab_data_out:
    st.header("Data out")
    st.caption(
        "After **Verify** (HITL) and **Approve & Load**, the screenplay lives as **structured graph data** "
        "in Neo4j. Use this tab to **inspect the schema**, run **recipe Cypher** (read-only), and **download CSV** "
        "for spreadsheets, warehouses, or demos."
    )
    st.markdown(graph_schema_card_markdown())

    _stamp_out = _neo4j_dashboard_cache_stamp()
    _lc = _cached_label_counts(_stamp_out)
    _rc = _cached_rel_type_counts(_stamp_out)
    if not _lc and not _rc:
        st.info(
            "No graph statistics yet — connect Neo4j, load from **Verify**, or check credentials. "
            "Counts refresh with the same cache as the Dashboard (**Reload metrics** in the sidebar)."
        )
    else:
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Node labels (live)")
            st.dataframe(
                pd.DataFrame(_lc) if _lc else pd.DataFrame(columns=["label", "cnt"]),
                use_container_width=True,
                hide_index=True,
            )
        with c2:
            st.subheader("Relationship types (live)")
            st.dataframe(
                pd.DataFrame(_rc) if _rc else pd.DataFrame(columns=["rel_type", "cnt"]),
                use_container_width=True,
                hide_index=True,
            )

    st.subheader("Recipe queries")
    st.caption("Fixed, parameterized Cypher — proof that the graph is queryable without the chat layer.")
    _dq_labels = {s["key"]: s["title"] for s in DEMO_QUERY_SPECS}
    _dq_desc = {s["key"]: s["description"] for s in DEMO_QUERY_SPECS}
    _dq_pick = st.selectbox(
        "Query",
        [s["key"] for s in DEMO_QUERY_SPECS],
        format_func=lambda k: _dq_labels[k],
        key="data_out_demo_query",
    )
    st.caption(_dq_desc.get(_dq_pick, ""))
    _dq_rows = _cached_demo_query(_stamp_out, _dq_pick)
    if not _dq_rows:
        st.warning("Query returned no rows or Neo4j is unreachable.")
    else:
        st.dataframe(pd.DataFrame(_dq_rows), use_container_width=True, hide_index=True)

    st.subheader("CSV downloads")
    _edge_limit = st.number_input(
        "Max rows for narrative edges export",
        min_value=100,
        max_value=50_000,
        value=5_000,
        step=100,
        key="data_out_edge_limit",
        help="Caps edge rows for browser download; raise for full scripts if needed.",
    )
    _edges = _cached_export_edges(_stamp_out, int(_edge_limit))
    _chars = _cached_export_characters(_stamp_out)
    _evs = _cached_export_events(_stamp_out)

    ec1, ec2, ec3 = st.columns(3)
    with ec1:
        st.download_button(
            "narrative_edges.csv",
            data=pd.DataFrame(_edges).to_csv(index=False).encode("utf-8"),
            file_name="scriptrag_narrative_edges.csv",
            mime="text/csv",
            disabled=not _edges,
            key="dl_edges",
        )
    with ec2:
        st.download_button(
            "characters.csv",
            data=pd.DataFrame(_chars).to_csv(index=False).encode("utf-8"),
            file_name="scriptrag_characters.csv",
            mime="text/csv",
            disabled=not _chars,
            key="dl_chars",
        )
    with ec3:
        st.download_button(
            "events.csv",
            data=pd.DataFrame(_evs).to_csv(index=False).encode("utf-8"),
            file_name="scriptrag_events.csv",
            mime="text/csv",
            disabled=not _evs,
            key="dl_events",
        )
    if not _chars and not _evs and not _edges:
        st.caption("Load a graph to enable downloads.")


# ===================================================================
# TAB: Pipeline Efficiency Tracking
# ===================================================================

with tab_efficiency:
    st.header("Pipeline Efficiency Tracking")
    st.caption(
        "**Agentic pipeline observability:** each finished run is a **:PipelineRun** row in Neo4j (survives screenplay reloads). "
        "Use it like production extractor metrics — **tokens**, **estimated cost**, correction/warning counts, scenes processed. "
        f"Bump **`AGENT_OPTIMIZATION_VERSION`** in `app.py` when you ship pipeline improvements (current: **{AGENT_OPTIMIZATION_VERSION}**)."
    )
    try:
        _drv_eff = get_driver()
        try:
            rows = list_pipeline_runs(_drv_eff, limit=500)
        finally:
            _drv_eff.close()
    except Exception as exc:
        rows = []
        st.error(f"Could not read pipeline runs from Neo4j: {exc}")

    if not rows:
        st.info(
            "No runs logged yet. Complete a pipeline run in the **Pipeline** tab (Neo4j must be reachable)."
        )
    else:
        display: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            ext = int(r.get("scenes_extracted", 0) or 0)
            tot = int(r.get("total_scenes", 0) or 0)
            tel_tok = int(r.get("telemetry_tokens", 0) or 0)
            tel_cost = float(r.get("telemetry_cost_usd", 0) or 0)
            display.append({
                "Run (UTC)": str(r.get("ts", ""))[:19].replace("T", " "),
                "Scenes extracted": f"{ext} / {tot}" if tot else str(ext),
                "Corrections": int(r.get("corrections_count", 0) or 0),
                "Warnings": int(r.get("warnings_count", 0) or 0),
                "Telemetry tokens": tel_tok,
                "Telemetry cost ($)": round(tel_cost, 4),
                "Agent opt. ver.": int(r.get("agent_optimization_version", 0) or 0),
                "Auditors": "yes" if r.get("llm_auditors_enabled") else "no",
                "Failed scenes": int(r.get("failed_scenes", 0) or 0),
            })
        df = pd.DataFrame(display)
        st.dataframe(df, use_container_width=True, hide_index=True)


# ===================================================================
# TAB: Dashboard
# ===================================================================

with tab_dashboard:
    st.header("Dashboard")

    if _event_count > 0:
        total_scenes_hint = ""
        pr = st.session_state.get("pipeline_results")
        if pr and pr.get("total_scenes"):
            total_scenes_hint = f"/{pr['total_scenes']}"
        st.caption(
            f"**{_event_count}{total_scenes_hint}** scenes loaded in Neo4j."
        )
        if _act_bounds:
            st.caption(
                f"**Script span:** scenes **{_act_bounds['min_scene']}–{_act_bounds['max_scene']}** "
                f"({_act_bounds['scene_count']} :Event nodes). Act windows = equal thirds."
            )
    else:
        st.info(
            "No scene data in Neo4j yet. Run the **Pipeline**, use **Verify**, "
            "then **Approve & Load** to populate the dashboard."
        )

    st.subheader("Structural load (production signal)")
    st.caption(
        "**MET-01 — additive** proxy: average count of **narrative** relationship instances "
        "(`INTERACTS_WITH`, `CONFLICTS_WITH`, `USES`, `LOCATED_IN`, `POSSESSES`) per **:Event**. "
        "Higher usually means more graph “physics” to produce per scene — **not** a quality or story score."
    )
    if _structural_load.get("scene_count", 0) == 0:
        st.info("No :Event nodes — structural load appears after you load the graph.")
    else:
        sl = _structural_load
        m1, m2, m3, m4, m5 = st.columns(5)
        with m1:
            st.metric("Load index (edges / scene)", f"{sl['structural_load_index']:.2f}")
        with m2:
            st.metric("Narrative edges", f"{sl['narrative_edge_count']:,}")
        with m3:
            st.metric("Characters", sl["character_count"])
        with m4:
            st.metric("Locations", sl["location_count"])
        with m5:
            st.metric("Props", sl["prop_count"])

    st.divider()
    _render_momentum_chart(momentum_rows, _act_bounds)
    st.divider()
    _render_payoff_matrix(payoff_rows)
    st.divider()
    _render_power_shift(top_chars, _act_matrix, _act_bounds, _TOP_K)
    _primary_lead_regression_warning(_act_matrix, _primary_id, _primary_override)


# ===================================================================
# TAB: Investigate
# ===================================================================

with tab_investigate:
    st.header("Investigate")
    _inv_cap = (
        "Ask questions about the script's structure. Answers come from your Neo4j graph."
    )
    if _SCRIPTRAG_DEMO_LAYOUT:
        _inv_cap += (
            " For demos, prefer **Data out** (recipe Cypher + CSV) first — this tab is **optional NL exploration**."
        )
    st.caption(_inv_cap)

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if user_input := st.chat_input("Ask about the script's structure..."):
        with st.chat_message("user"):
            st.markdown(user_input)
        st.session_state.messages.append({"role": "user", "content": user_input})

        exc_name: str | None = None
        try:
            response = ask_narrative_mri(user_input)
        except Exception as exc:
            _log.exception("Investigate chat failed")
            response = (
                "Something went wrong running the graph query. Check Neo4j and try again."
            )
            exc_name = type(exc).__name__
        with st.chat_message("assistant"):
            st.markdown(response)
            if exc_name:
                with st.expander("Technical detail"):
                    st.code(exc_name, language="text")
        st.session_state.messages.append({"role": "assistant", "content": response})
