from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from agent import ask_narrative_mri
from domains.screenplay.adapter import get_bundle as _screenplay_bundle
from etl_core.errors import MaxRetriesError
from etl_core.graph_engine import run_pipeline as _run_etl
from metrics import (
    get_driver,
    get_narrative_momentum_by_scene,
    get_passivity_in_scene_window,
    get_payoff_prop_timelines,
    get_script_act_bounds,
    get_top_characters_by_interaction_count,
)
from pipeline_state import filesystem_snapshot

ROLLING_SCENES = 3
PAYOFF_MIN_SCENE_GAP = 10
TOP_INTERACTION_CHARACTERS = 5
PROTAGONIST_ID = "zev"


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


# Hosted / Docker: set DISABLE_PIPELINE_ENGINE=1 — subprocess pipeline + disk writes are unsafe on typical PaaS.
_PIPELINE_ENGINE_ENABLED = not _env_truthy("DISABLE_PIPELINE_ENGINE")

_PROJECT_ROOT = Path(__file__).resolve().parent
_PIPELINE_JSON_NAMES = (
    "raw_scenes.json",
    "master_lexicon.json",
    "validated_graph.json",
    "pipeline_state.json",
)
_TARGET_FDX = _PROJECT_ROOT / "target_script.fdx"
_AUDIT_LOG = _PROJECT_ROOT / "extraction_audit.jsonl"


def _audit_log_mtime() -> float:
    try:
        return _AUDIT_LOG.stat().st_mtime
    except OSError:
        return 0.0


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


def _run_uv_pipeline_stage(
    args: list[str],
    *,
    log_chunks: list[str],
    log_placeholder: Any,
    stage_banner: str,
) -> int:
    log_chunks.append(stage_banner)
    log_placeholder.code("".join(log_chunks), language="text")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    cmd = ["uv", "run", "python", *args]
    proc = subprocess.Popen(
        cmd,
        cwd=str(_PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    if proc.stdout is not None:
        for line in proc.stdout:
            log_chunks.append(line)
            log_placeholder.code("".join(log_chunks), language="text")
    return int(proc.wait())


st.set_page_config(
    page_title="Narrative Timeline Analyzer — Narrative MRI",
    layout="wide",
    initial_sidebar_state="expanded",
)


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


@st.cache_data(ttl=120, show_spinner="Loading AI audit log…")
def _cached_audit_rows(_audit_stamp: float) -> list[dict[str, Any]]:
    del _audit_stamp
    if not _AUDIT_LOG.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        text = _AUDIT_LOG.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows[-800:]


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
            "⚠️ **FATAL ARC:** The protagonist is regressing — **Act 3 passivity exceeds Act 1** "
            f"({float(p3):.3f} vs {float(p1):.3f} for **{zev_key}**)."
        )


_DASH_STAMP = _neo4j_dashboard_cache_stamp()

momentum_rows = _cached_momentum_rows(_DASH_STAMP)
payoff_rows = _cached_payoff_props(_DASH_STAMP)
top_chars = _cached_top_characters(_DASH_STAMP)
_act_bounds = _cached_act_bounds(_DASH_STAMP)
_act_bounds_key = _act_bounds_six(_act_bounds) if _act_bounds else None
_ids_tuple = tuple(str(c["id"]) for c in top_chars)
_extra = tuple(dict.fromkeys(list(_ids_tuple) + [PROTAGONIST_ID]))
_act_matrix = _cached_act_passivity_matrix(_DASH_STAMP, _extra, _act_bounds_key)

_AUDIT_STAMP = _audit_log_mtime()
_audit_rows = _cached_audit_rows(_AUDIT_STAMP)

st.title("Narrative Timeline Analyzer")
st.caption("Temporal readouts over your Neo4j screenplay graph — momentum, long-horizon props, and act-bucketed agency.")
if _act_bounds:
    st.caption(
        f"**Script span (from graph):** scenes **{_act_bounds['min_scene']}–{_act_bounds['max_scene']}** "
        f"({_act_bounds['scene_count']} :Event nodes). Act windows = equal thirds of that range."
    )

if _flash := st.session_state.pop("_engine_room_flash", None):
    st.success(_flash)

with st.sidebar:
    st.header("Controls")
    if st.button(
        "Reload metrics from Neo4j",
        help="Clears Streamlit cache after pipeline or external graph edits.",
        key="sidebar_reload_neo4j",
    ):
        st.cache_data.clear()
        st.session_state["_engine_room_flash"] = "Cache cleared — re-querying Neo4j."
        st.rerun()
    if _PIPELINE_ENGINE_ENABLED:
        st.caption("Charts read **Neo4j** only. Run **neo4j_loader** after ingest.")
    else:
        st.caption(
            "Charts read **Neo4j** only. **Pipeline Engine** is disabled on this host — "
            "run parser / ingest / **neo4j_loader** locally (or on a VM), then open this app."
        )

_tab_labels = [
    "🛠️ Engine Room",
    "Narrative Timeline",
    "Ask the graph",
    "AI Audit Log",
]
if _PIPELINE_ENGINE_ENABLED:
    _tab_labels.append("⚙️ Pipeline Engine")

_tabs = st.tabs(_tab_labels)
tab_engine_room = _tabs[0]
tab_timeline = _tabs[1]
tab_chat = _tabs[2]
tab_audit = _tabs[3]
if _PIPELINE_ENGINE_ENABLED:
    tab_engine = _tabs[4]

with tab_engine_room:
    st.header("🛠️ Engine Room")
    st.caption(
        "Run the self-healing ETL pipeline on raw text. The LangGraph engine does "
        "**extract → validate → fix** (up to 3 retries) with full token/cost telemetry."
    )

    _er_text = st.text_area(
        "Paste raw scene text",
        height=200,
        placeholder="--- Scene 42 ---\nINT. COURTHOUSE - DAY\nZev throws the court letter at Alan...",
        key="engine_room_text",
    )

    _er_prompt = st.text_input(
        "System prompt override (leave blank for default screenplay prompt)",
        key="engine_room_prompt",
    )

    if st.button("Run ETL Pipeline", type="primary", key="engine_room_run", disabled=not _er_text.strip()):
        bundle = _screenplay_bundle()
        sys_prompt = _er_prompt.strip() or (
            "You are a Narrative Graph Architect. Extract Character, Location, and Prop nodes "
            "and their Relationships from the provided scene. Every relationship MUST include "
            "a source_quote which is the exact, verbatim text from the script."
        )
        with st.status("ETL Pipeline running…", expanded=True) as _er_status:
            try:
                state = _run_etl(
                    bundle,
                    raw_text=_er_text.strip(),
                    system_prompt=sys_prompt,
                    doc_id="engine_room",
                )
                _er_status.update(label="Pipeline complete", state="complete")
            except MaxRetriesError as exc:
                _er_status.update(label="Validation failed after max retries", state="error")
                st.error(str(exc))
                state = None
            except Exception as exc:
                _er_status.update(label="Pipeline error", state="error")
                st.error(f"{type(exc).__name__}: {exc}")
                state = None

        if state is not None:
            c1, c2 = st.columns(2)
            with c1:
                st.metric("Total tokens", f"{state.get('total_tokens', 0):,}")
            with c2:
                st.metric("Estimated cost", f"${state.get('total_cost', 0.0):.4f}")

            st.subheader("Extracted graph")
            st.json(state.get("current_json", {}))

            trail = state.get("audit_trail") or []
            if trail:
                with st.expander(f"Hallucination Audit Log ({len(trail)} steps)", expanded=True):
                    for entry in trail:
                        node = entry.get("node", "?")
                        detail = entry.get("detail", "")
                        if node == "fixer":
                            st.markdown(f"**{node}** — attempt {entry.get('attempt', '?')}")
                            col_before, col_after = st.columns(2)
                            with col_before:
                                st.caption("Before (broken)")
                                st.json(entry.get("before", {}))
                            with col_after:
                                st.caption("After (fixed)")
                                st.json(entry.get("after", {}))
                            if entry.get("reason"):
                                st.caption(f"Reason: {entry['reason']}")
                        elif entry.get("error"):
                            st.markdown(f"**{node}** — {detail}")
                            st.code(entry["error"], language="text")
                        else:
                            st.markdown(f"**{node}** — {detail}")
                        st.divider()

with tab_timeline:
    _render_momentum_chart(momentum_rows, _act_bounds)
    st.divider()
    _render_payoff_matrix(payoff_rows)
    st.divider()
    _render_power_shift(top_chars, _act_matrix, _act_bounds)
    _protagonist_regression_warning(_act_matrix)

with tab_chat:
    st.subheader("Ask about the script’s structure")
    st.caption("Narrative QA chain over your Neo4j graph.")

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

with tab_audit:
    st.header("AI Audit Log")
    st.caption(
        "LangGraph extraction pipeline: **extract** → **validator** (deterministic rules + Pydantic) → "
        "**fixer** (LLM repair) when validation fails. One JSON line per step in `extraction_audit.jsonl` "
        "(written by `ingest.py`)."
    )
    if not _audit_rows:
        st.info("No audit entries yet. Run **`uv run python ingest.py`** to populate.")
    else:
        st.metric("Logged steps (last 800)", len(_audit_rows))
        df_audit = pd.DataFrame(_audit_rows)
        st.dataframe(df_audit, use_container_width=True, height=420)
        with st.expander("Raw JSONL path"):
            st.code(str(_AUDIT_LOG), language="text")

if _PIPELINE_ENGINE_ENABLED:
    with tab_engine:
        st.header("⚙️ Pipeline Engine")
        st.caption(
            "Linear four-stage chain only (repo root as cwd). **Launch** runs, in order:\n\n"
            "1. `uv run python parser.py target_script.fdx`\n"
            "2. `uv run python lexicon.py raw_scenes.json`\n"
            "3. `uv run python ingest.py`\n"
            "4. `uv run python neo4j_loader.py`\n\n"
            "**Timeline charts read Neo4j**, not `validated_graph.json` directly. "
            "After ingest, complete **stage 4** (or use **Reload metrics from Neo4j** in the sidebar)."
        )

        st.subheader("Clean slate")
        st.markdown(
            '<p style="color:#dc2626;font-weight:700;font-size:1.05rem;margin-bottom:0.35rem;">'
            "⚠️ NUKE DATABASE & CACHE"
            "</p>",
            unsafe_allow_html=True,
        )
        st.caption(
            "Runs `MATCH (n) DETACH DELETE n` on Neo4j and removes pipeline JSON outputs from disk."
        )
        if st.button(
            "⚠️ NUKE DATABASE & CACHE",
            key="pipeline_nuke",
            help="Irreversible: empties Neo4j and deletes raw_scenes.json, master_lexicon.json, validated_graph.json, pipeline_state.json.",
        ):
            try:
                _nuke_neo4j_all_nodes()
                _delete_pipeline_json_files()
            except Exception as exc:
                st.error(f"Wipe failed: {exc}")
            else:
                st.session_state["_engine_room_flash"] = "Slate wiped — Neo4j and pipeline JSON cleared."
                st.cache_data.clear()
                st.rerun()

        st.divider()
        st.subheader("Uploader")
        _up = st.file_uploader(
            "Final Draft screenplay",
            type=["fdx"],
            help="Stored in the project directory as target_script.fdx (overwrites any previous file).",
            key="pipeline_fdx_upload",
        )
        if _up is not None:
            _TARGET_FDX.write_bytes(_up.getvalue())
            st.success(
                f"Saved **{_TARGET_FDX.name}** ({len(_up.getvalue()):,} bytes). "
                "Use **Launch Extraction Pipeline** below."
            )

        st.divider()
        st.subheader("Pipeline status")
        _snap = filesystem_snapshot(_PROJECT_ROOT)
        with st.expander("What’s on disk (artifacts + last known ingest/loader)", expanded=False):
            st.caption(
                "**Ingest progress** = rows in `validated_graph.json` vs scenes in `raw_scenes.json`. "
                "`ingest.py` checkpoints after **each** successful scene unless you pass `--no-checkpoint`."
            )
            st.json(_snap)

        _raw_ok = bool(_snap.get("parser") and _snap["parser"].get("ok"))
        _lex_ok = bool(_snap.get("lexicon") and _snap["lexicon"].get("ok"))
        _ing = _snap.get("ingest") or {}
        _ing_ok = bool(_ing.get("ok"))
        _missing_ingest = int(_ing.get("missing_count") or 0) if _ing_ok else 0
        if _raw_ok and _lex_ok and _ing_ok and _missing_ingest > 0:
            st.warning(
                f"Ingest is **partial**: **{_ing.get('entries_in_file', 0)}** scene graph(s) on disk, "
                f"**{_missing_ingest}** scene number(s) still missing (see `failed_scenes.log`). "
                "Use **Resume ingest** below to continue without re-parsing."
            )

        st.divider()
        st.subheader("Execution chain")

        if not _TARGET_FDX.is_file():
            st.info("Upload a **.fdx** file above so **target_script.fdx** exists before launching.")

        _stages: list[tuple[str, list[str]]] = [
            ("Stage 1 — Parser (`raw_scenes.json`)", ["parser.py", "target_script.fdx"]),
            ("Stage 2 — Lexicon (`master_lexicon.json`)", ["lexicon.py", "raw_scenes.json"]),
            ("Stage 3 — Ingest (`validated_graph.json`)", ["ingest.py"]),
            ("Stage 4 — Neo4j loader", ["neo4j_loader.py"]),
        ]

        if st.button(
            "Resume ingest only (`ingest.py --resume`)",
            key="pipeline_resume_ingest",
            help="Keeps existing `validated_graph.json` rows (by scene_number) and only calls the LLM for missing scenes. Requires raw_scenes.json + master_lexicon.json.",
            disabled=not (_raw_ok and _lex_ok),
        ):
            _log_r: list[str] = []
            _ph_r = st.empty()
            _banner_r = (
                "\n" + "=" * 72 + "\nResume — Stage 3 Ingest (partial)\n" + "=" * 72 + "\n"
                "$ uv run python ingest.py --resume\n\n"
            )
            _rc_r = _run_uv_pipeline_stage(
                ["ingest.py", "--resume"],
                log_chunks=_log_r,
                log_placeholder=_ph_r,
                stage_banner=_banner_r,
            )
            if _rc_r != 0:
                st.error(f"Resume ingest exited with code **{_rc_r}**.")
                _ph_r.code("".join(_log_r), language="text")
            else:
                st.session_state["_engine_room_flash"] = "Ingest updated — refreshing dashboard cache."
                st.cache_data.clear()
                st.rerun()

        if st.button(
            "Launch Extraction Pipeline",
            type="primary",
            key="pipeline_launch",
            disabled=not _TARGET_FDX.is_file(),
        ):
            _log_chunks: list[str] = []
            _log_ph = st.empty()
            _failed = False
            _fail_rc = 0
            _fail_label = ""

            with st.status("Extraction pipeline", expanded=True) as _pipe_status:
                _prog = st.progress(0, text="Starting…")
                for _i, (_label, _args) in enumerate(_stages):
                    _pipe_status.update(label=f"{_label}…", state="running")
                    _prog.progress(_i / len(_stages), text=_label)
                    _banner = f"\n{'=' * 72}\n{_label}\n{'=' * 72}\n$ uv run python {' '.join(_args)}\n\n"
                    _rc = _run_uv_pipeline_stage(
                        _args,
                        log_chunks=_log_chunks,
                        log_placeholder=_log_ph,
                        stage_banner=_banner,
                    )
                    if _rc != 0:
                        _failed = True
                        _fail_rc = _rc
                        _fail_label = _label
                        _pipe_status.update(label=f"Failed: {_label}", state="error")
                        _prog.progress(1.0, text="Failed")
                        break

                if not _failed:
                    _pipe_status.update(label="Pipeline complete", state="complete")
                    _prog.progress(1.0, text="Done")

            if _failed:
                st.error(
                    f"Pipeline halted — **{_fail_label}** exited with code **{_fail_rc}**. "
                    "See the log above."
                )
                _log_ph.code("".join(_log_chunks), language="text")
            else:
                st.session_state["_engine_room_flash"] = (
                    "Pipeline finished — dashboard reloaded from Neo4j (cache cleared)."
                )
                st.cache_data.clear()
                st.rerun()
