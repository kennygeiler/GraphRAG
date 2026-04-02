from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from agent import ask_narrative_mri
from ingest import SceneResult, build_system_prompt, extract_scenes
from lexicon import build_master_lexicon
from metrics import (
    get_driver,
    get_narrative_momentum_by_scene,
    get_passivity_in_scene_window,
    get_payoff_prop_timelines,
    get_script_act_bounds,
    get_top_characters_by_interaction_count,
)
from neo4j_loader import load_entries
from parser import parse_fdx_to_raw_scenes, write_raw_scenes_json

ROLLING_SCENES = 3
PAYOFF_MIN_SCENE_GAP = 10
TOP_INTERACTION_CHARACTERS = 5
PROTAGONIST_ID = "zev"

_PROJECT_ROOT = Path(__file__).resolve().parent
_TARGET_FDX = _PROJECT_ROOT / "target_script.fdx"
_PIPELINE_JSON_NAMES = (
    "raw_scenes.json",
    "master_lexicon.json",
    "validated_graph.json",
    "pipeline_state.json",
)


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


_PIPELINE_ENABLED = not _env_truthy("DISABLE_PIPELINE")

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


def _nuke_neo4j_all_nodes() -> None:
    drv = get_driver()
    try:
        with drv.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
    finally:
        drv.close()


def _delete_pipeline_json_files() -> None:
    for name in _PIPELINE_JSON_NAMES:
        p = _PROJECT_ROOT / name
        if p.is_file():
            p.unlink()


# ---------------------------------------------------------------------------
# Neo4j cached queries
# ---------------------------------------------------------------------------

@st.cache_data(ttl=120, show_spinner="Loading narrative momentum…")
def _cached_momentum_rows(_artifact_stamp: tuple[float, float]) -> list[dict[str, Any]]:
    del _artifact_stamp
    drv = get_driver()
    try:
        return get_narrative_momentum_by_scene(driver=drv)
    finally:
        drv.close()


@st.cache_data(ttl=120, show_spinner="Loading payoff prop arcs…")
def _cached_payoff_props(_artifact_stamp: tuple[float, float]) -> list[dict[str, Any]]:
    del _artifact_stamp
    drv = get_driver()
    try:
        return get_payoff_prop_timelines(min_scene_gap=PAYOFF_MIN_SCENE_GAP, driver=drv)
    finally:
        drv.close()


@st.cache_data(ttl=120, show_spinner="Loading character interaction ranks…")
def _cached_top_characters(_artifact_stamp: tuple[float, float]) -> list[dict[str, Any]]:
    del _artifact_stamp
    drv = get_driver()
    try:
        return get_top_characters_by_interaction_count(TOP_INTERACTION_CHARACTERS, driver=drv)
    finally:
        drv.close()


@st.cache_data(ttl=120, show_spinner="Loading script act bounds…")
def _cached_act_bounds(_artifact_stamp: tuple[float, float]) -> dict[str, Any] | None:
    del _artifact_stamp
    drv = get_driver()
    try:
        return get_script_act_bounds(driver=drv)
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
    finally:
        drv.close()


@st.cache_data(ttl=120, show_spinner="Counting Neo4j events…")
def _cached_event_count(_artifact_stamp: tuple[float, float]) -> int:
    del _artifact_stamp
    drv = get_driver()
    try:
        with drv.session() as session:
            rec = session.run("MATCH (e:Event) RETURN count(e) AS c").single()
            return int(rec["c"]) if rec else 0
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
) -> None:
    st.subheader("Power shift — agency by act")
    cap = (
        f"Passivity index (in-degree / total degree on `CONFLICTS_WITH` + `USES`, same as MRI metrics) "
        f"for the **{TOP_INTERACTION_CHARACTERS}** characters with the most interaction edges. "
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
    for i, c in enumerate(top_chars):
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


def _protagonist_regression_warning(matrix: dict[str, list[float | None]]) -> None:
    zev_key = next((k for k in matrix if k.lower() == PROTAGONIST_ID.lower()), PROTAGONIST_ID)
    row = matrix.get(zev_key) or matrix.get(PROTAGONIST_ID)
    if not row or len(row) < 3:
        return
    p1, _, p3 = row[0], row[1], row[2]
    if p1 is None or p3 is None:
        return
    if float(p3) > float(p1):
        st.warning(
            "**FATAL ARC:** The protagonist is regressing — **Act 3 passivity exceeds Act 1** "
            f"({float(p3):.3f} vs {float(p1):.3f} for **{zev_key}**)."
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
momentum_rows = _cached_momentum_rows(_DASH_STAMP)
payoff_rows = _cached_payoff_props(_DASH_STAMP)
top_chars = _cached_top_characters(_DASH_STAMP)
_act_bounds = _cached_act_bounds(_DASH_STAMP)
_act_bounds_key = _act_bounds_six(_act_bounds) if _act_bounds else None
_ids_tuple = tuple(str(c["id"]) for c in top_chars)
_extra = tuple(dict.fromkeys(list(_ids_tuple) + [PROTAGONIST_ID]))
_act_matrix = _cached_act_passivity_matrix(_DASH_STAMP, _extra, _act_bounds_key)
_event_count = _cached_event_count(_DASH_STAMP)

# --------------- header ---------------------------------------------
st.title("ScriptRAG")
st.caption(
    "Upload a screenplay, extract a knowledge graph with a self-healing AI pipeline, "
    "review the Editor Agent's corrections, then explore the data."
)

if _flash := st.session_state.pop("_flash", None):
    st.success(_flash)

# --------------- sidebar --------------------------------------------
with st.sidebar:
    st.header("Controls")
    if st.button(
        "Reload metrics from Neo4j",
        help="Clears Streamlit cache after pipeline or external graph edits.",
        key="sidebar_reload",
    ):
        st.cache_data.clear()
        st.session_state["_flash"] = "Cache cleared — re-querying Neo4j."
        st.rerun()

    with st.expander("Reset database", expanded=False):
        st.caption("Wipe **all** Neo4j nodes and delete pipeline JSON artifacts from disk.")
        if st.button("Nuke database & cache", key="sidebar_nuke"):
            try:
                _nuke_neo4j_all_nodes()
                _delete_pipeline_json_files()
            except Exception as exc:
                st.error(f"Wipe failed: {exc}")
            else:
                st.session_state.pop("pipeline_results", None)
                st.cache_data.clear()
                st.session_state["_flash"] = "Slate wiped — Neo4j and pipeline JSON cleared."
                st.rerun()

# --------------- tabs -----------------------------------------------
_tab_labels: list[str] = []
if _PIPELINE_ENABLED:
    _tab_labels.append("Pipeline")
_tab_labels += ["Editor Agent", "Dashboard", "Investigate"]

_tabs = st.tabs(_tab_labels)
_ti = 0

if _PIPELINE_ENABLED:
    tab_pipeline = _tabs[_ti]; _ti += 1
tab_editor = _tabs[_ti]; _ti += 1
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
            "The Editor Agent monitors every scene: **extract → validate → fix → audit**."
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
                        "tokens": cum_tokens,
                        "cost": cum_cost,
                    }

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
                    st.metric("Total tokens", f"{pr['tokens']:,}")
                with c5:
                    st.metric("Estimated cost", f"${pr['cost']:.4f}")

                if pr["corrections"] or pr.get("warnings"):
                    parts = []
                    if pr["corrections"]:
                        parts.append(f"**{len(pr['corrections'])}** correction(s)")
                    if pr.get("warnings"):
                        parts.append(f"**{len(pr['warnings'])}** warning(s)")
                    st.info(
                        f"The Editor Agent flagged {' and '.join(parts)}. "
                        "Review them in the **Editor Agent** tab, then approve to load into Neo4j."
                    )
                elif pr["extracted"] > 0:
                    st.success(
                        "All scenes passed validation on the first try. "
                        "Head to the **Editor Agent** tab to approve and load into Neo4j."
                    )

        elif not _TARGET_FDX.is_file():
            st.info("Upload a **.fdx** file above to get started.")


# ===================================================================
# TAB: Editor Agent
# ===================================================================

with tab_editor:
    st.header("Editor Agent")
    st.caption(
        "Review the AI's self-corrections and warnings before loading data into Neo4j."
    )

    pr = st.session_state.get("pipeline_results")
    if not pr:
        st.info("No pipeline results yet. Run the pipeline first.")
    else:
        n_corrections = len(pr["corrections"])
        n_warnings = len(pr.get("warnings", []))
        st.markdown(
            f"**{pr['extracted']}** scenes extracted — "
            f"**{n_corrections}** error(s) auto-fixed, "
            f"**{n_warnings}** warning(s) for review, "
            f"**{pr['failed']}** failed."
        )

        if pr["corrections"]:
            st.subheader(f"Corrections ({n_corrections})")
            for corr in pr["corrections"]:
                sn = corr["scene_number"]
                heading = corr["heading"] or "untitled"
                with st.expander(f"Scene {sn} — {heading}", expanded=True):
                    for entry in corr["audit_entries"]:
                        node = entry.get("node", "?")
                        detail = entry.get("detail", "")

                        if node in ("fixer", "audit_fixer"):
                            label = "Audit Fixer" if node == "audit_fixer" else "Fixer"
                            st.markdown(f"**{label}** — attempt {entry.get('attempt', '?')}")
                            col_before, col_after = st.columns(2)
                            with col_before:
                                st.caption("Before (broken)")
                                st.json(entry.get("before", {}))
                            with col_after:
                                st.caption("After (fixed)")
                                st.json(entry.get("after", {}))
                            if entry.get("reason"):
                                st.caption(f"Reason: {entry['reason']}")
                        elif node == "audit" and entry.get("findings"):
                            st.markdown(f"**Audit** — {entry.get('error_count', 0)} error(s), {entry.get('warning_count', 0)} warning(s)")
                            for f in entry["findings"]:
                                icon = "🔴" if f.get("severity") == "error" else "🟡"
                                st.markdown(f"{icon} **{f.get('check', '?')}** — {f.get('detail', '')}")
                                if f.get("suggestion"):
                                    st.caption(f"Suggestion: {f['suggestion']}")
                        elif entry.get("error"):
                            st.markdown(f"**{node}** — {detail}")
                            st.code(entry["error"], language="text")
                        else:
                            st.markdown(f"**{node}** — {detail}")
                        st.divider()
        else:
            st.success("No corrections needed — all scenes passed validation cleanly.")

        warnings_list = pr.get("warnings", [])
        if warnings_list:
            st.subheader(f"Warnings ({n_warnings})")
            st.caption("These items did not block the pipeline but may need human review.")
            for w in warnings_list:
                sn_label = f"Scene {w['scene_number']}" if w.get("scene_number") else ""
                check = w.get("check", "unknown")
                detail = w.get("detail", "")
                st.warning(f"**{check}** {f'({sn_label})' if sn_label else ''} — {detail}")

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
                with st.spinner("Loading into Neo4j…"):
                    try:
                        loaded = load_entries(entries_to_load)
                    except Exception as exc:
                        st.error(f"Load failed: {exc}")
                    else:
                        st.cache_data.clear()
                        st.session_state["_flash"] = (
                            f"Loaded **{loaded}** scenes into Neo4j. Dashboard refreshed."
                        )
                        st.rerun()


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
            "No scene data in Neo4j yet. Run the **Pipeline**, review in the **Editor Agent**, "
            "then **Approve & Load** to populate the dashboard."
        )

    _render_momentum_chart(momentum_rows, _act_bounds)
    st.divider()
    _render_payoff_matrix(payoff_rows)
    st.divider()
    _render_power_shift(top_chars, _act_matrix, _act_bounds)
    _protagonist_regression_warning(_act_matrix)


# ===================================================================
# TAB: Investigate
# ===================================================================

with tab_investigate:
    st.header("Investigate")
    st.caption("Ask questions about the script's structure. Answers come from your Neo4j graph.")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if user_input := st.chat_input("Ask about the script's structure..."):
        with st.chat_message("user"):
            st.markdown(user_input)
        st.session_state.messages.append({"role": "user", "content": user_input})

        response = ask_narrative_mri(user_input)
        with st.chat_message("assistant"):
            st.markdown(response)
        st.session_state.messages.append({"role": "assistant", "content": response})
