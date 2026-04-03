from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

import pandas as pd
import streamlit as st

from cleanup_review import (
    apply_approved_warning_edits,
    build_verify_audit_payload,
    cleanup_warning_widget_id,
    plain_english_fix_reason,
    summarize_graph_delta,
    verify_audit_to_csv,
    verify_audit_to_json,
    warning_check_title,
    warning_hitl_approve_preview,
    warning_hitl_evidence_markdown,
    warning_json_location,
    warning_verify_guidance,
)
from ingest import _scene_number_key, build_system_prompt, extract_scenes
from lexicon import build_master_lexicon
from metrics import get_driver
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

_PROJECT_ROOT = Path(__file__).resolve().parent
_TARGET_FDX = _PROJECT_ROOT / "target_script.fdx"
# Pipeline "Smoke test" mode: first N scenes in screenplay order (reproducible quick run).
PIPELINE_SMOKE_FIRST_SCENES = 5

_PIPELINE_JSON_NAMES = (
    "raw_scenes.json",
    "master_lexicon.json",
    "validated_graph.json",
    "pipeline_state.json",
)


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


_PIPELINE_ENABLED = not _env_truthy("DISABLE_PIPELINE")
# CEO / technical-demo tab order: Audit & Verify → Data out → Reconcile → … (see README / .env.example).
_SCRIPTRAG_DEMO_LAYOUT = _env_truthy("SCRIPTRAG_DEMO_LAYOUT")
_VERIFY_TAB_LABEL = "Audit & Verify"


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
    fdx_filename: str = "",
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
            failed_scenes=failed_scenes,
            llm_auditors_enabled=llm_auditors_enabled,
            fdx_filename=fdx_filename,
        )
        return True
    except Exception:
        return False
    finally:
        if drv is not None:
            drv.close()


# ---------------------------------------------------------------------------
# Neo4j cache stamp (pipeline JSON mtimes — invalidates cached graph reads)
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
    """Show fixer / follow-up repair trail where extraction happened (Pipeline tab)."""
    st.subheader("Self-healing corrections")
    st.caption(
        "During **Stage 3**, some scenes needed a **rewrite** after validation reported errors. "
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
                    label = "Follow-up repair" if node == "audit_fixer" else "Rules / schema fixer"
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
                elif node == "auditor_auto_apply":
                    st.markdown("**Semantic audit (auto-applied)**")
                    reason = str(entry.get("reason") or entry.get("detail") or "")
                    if reason:
                        st.caption(reason)
                    before_g = entry.get("before") or {}
                    after_g = entry.get("after") or {}
                    if isinstance(before_g, dict) and isinstance(after_g, dict):
                        bsum, asum = summarize_graph_delta(before_g, after_g)
                        c1, c2 = st.columns(2)
                        with c1:
                            st.markdown("**Before**")
                            st.markdown(bsum)
                        with c2:
                            st.markdown("**After**")
                            st.markdown(asum)
                elif node == "audit" and entry.get("findings"):
                    st.markdown(
                        f"**Semantic check** — {entry.get('error_count', 0)} error(s), "
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
# FDX scene stats (Pipeline — count + number span for range UI)
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner=False)
def _cached_fdx_scene_stats(path_str: str, mtime: float) -> tuple[int, int, int]:
    """Return (scene_count, min_fdx_scene_number, max_fdx_scene_number). ``mtime`` busts cache on new upload."""
    del mtime
    p = Path(path_str)
    if not p.is_file():
        return (0, 1, 1)
    try:
        scenes = parse_fdx_to_raw_scenes(p)
    except Exception:
        return (0, 1, 1)
    if not scenes:
        return (0, 1, 1)
    keys = [_scene_number_key(s, i + 1) for i, s in enumerate(scenes)]
    return (len(scenes), min(keys), max(keys))


# ---------------------------------------------------------------------------
# Neo4j cached queries
# ---------------------------------------------------------------------------

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


# ===================================================================
# Page config & layout
# ===================================================================

st.set_page_config(
    page_title="ScriptRAG",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --------------- header ---------------------------------------------
st.title("ScriptRAG")
st.caption(
    "Upload a screenplay, extract a knowledge graph with a self-healing AI pipeline, "
    f"review corrections in **Pipeline**, **{_VERIFY_TAB_LABEL}** warnings, then explore the data."
)

if _flash := st.session_state.pop("_flash", None):
    st.success(_flash)

# --------------- sidebar --------------------------------------------
with st.sidebar:
    st.header("Controls")
    if _SCRIPTRAG_DEMO_LAYOUT:
        st.caption(
            f"**Demo layout** (`SCRIPTRAG_DEMO_LAYOUT=1`): **{_VERIFY_TAB_LABEL} → Data out** before **Reconcile** "
            "— for pipeline storytelling."
        )

    with st.expander("Cache vs clear — quick guide", expanded=False):
        st.markdown(
            "- **Reload Neo4j cache** — Refreshes Streamlit’s cached Neo4j reads (e.g. after **Approve & load**, "
            "**Reconcile** merges, or edits in Neo4j Browser). Does **not** delete data.\n"
            "- **Clear screenplay & pipeline files** — Removes the screenplay graph in Neo4j and deletes local pipeline "
            r"JSON (`raw_scenes.json`, `master_lexicon.json`, `validated_graph.json`, `pipeline_state.json`). "
            "Clears Pipeline / Verify session state. Use when **switching scripts** or you need disk and DB aligned. "
            "**Pipeline Efficiency Tracking** history is **not** removed.\n"
            f"- **Typical flow:** upload `.fdx` → run **Pipeline** → **{_VERIFY_TAB_LABEL}** → **Approve & load** → "
            "**Reload Neo4j cache** only if a tab still looks stale."
        )

    if st.button(
        "Reload Neo4j cache",
        help=(
            "Clears Streamlit @st.cache_data (Neo4j queries, reconciliation scan, FDX stats). "
            "Use after Approve & load, merges, or edits outside the app if counts look stale. Does not wipe Neo4j."
        ),
        key="sidebar_reload",
        use_container_width=True,
    ):
        st.cache_data.clear()
        st.session_state["_flash"] = "Cache cleared — re-querying Neo4j."
        st.rerun()

    st.divider()
    st.markdown("**Clear screenplay**")
    st.caption(
        "Neo4j screenplay graph + local pipeline JSON. Keeps **Pipeline Efficiency Tracking** history."
    )
    if st.button(
        "Clear screenplay & pipeline files",
        help=(
            "Wipes screenplay data in Neo4j (efficiency run rows stay) and deletes pipeline JSON on disk. "
            "Resets Pipeline / Verify state in this session."
        ),
        key="sidebar_nuke",
        type="secondary",
        use_container_width=True,
    ):
        try:
            _wipe_dashboard_neo4j_keep_pipeline_runs()
            _delete_pipeline_json_files()
        except Exception as exc:
            st.error(f"Clear failed: {exc}")
        else:
            st.session_state.pop("pipeline_results", None)
            st.session_state.pop("verify_hitl_neo4j_load_at", None)
            st.session_state.pop("verify_hitl_load_audit_payload", None)
            st.cache_data.clear()
            st.session_state["_flash"] = (
                "Screenplay cleared in Neo4j; pipeline JSON removed. Efficiency history unchanged."
            )
            st.rerun()

# --------------- section navigation ---------------------------------
# st.tabs() resets the visible tab on every rerun; a keyed radio keeps the user
# on the same section when widgets below change (e.g. Data out recipe query).
_tab_labels: list[str] = []
if _PIPELINE_ENABLED:
    _tab_labels.append("Pipeline")
if _SCRIPTRAG_DEMO_LAYOUT:
    _tab_labels += [
        _VERIFY_TAB_LABEL,
        "Data out",
        "Reconcile",
        "Pipeline Efficiency Tracking",
    ]
else:
    _tab_labels += [
        _VERIFY_TAB_LABEL,
        "Reconcile",
        "Data out",
        "Pipeline Efficiency Tracking",
    ]

if st.session_state.get("scriptrag_section") == "Verify":
    st.session_state.scriptrag_section = _VERIFY_TAB_LABEL

_cur = st.session_state.get("scriptrag_section")
if _cur not in _tab_labels:
    st.session_state.scriptrag_section = _tab_labels[0]

st.radio(
    "Section",
    options=_tab_labels,
    horizontal=True,
    label_visibility="collapsed",
    key="scriptrag_section",
)
_active: str = st.session_state["scriptrag_section"]
st.divider()


# ===================================================================
# TAB: Pipeline
# ===================================================================

if _PIPELINE_ENABLED and _active == "Pipeline":
        st.header("Pipeline")
        st.caption(
            "Upload a Final Draft (.fdx) screenplay then run the full extraction pipeline. "
            "Each scene runs **extract → validate ⇄ fix**, then follow-up checks and repair as needed. "
            "Each finished run writes a **:PipelineRun** row to Neo4j (telemetry tokens and **estimated** USD from `etl_core/telemetry.py`)."
        )

        _up = st.file_uploader(
            "Upload .fdx screenplay",
            type=["fdx"],
            help="Parsed into scenes, then each scene is sent through the LangGraph extraction pipeline.",
            key="pipeline_fdx_upload",
        )
        if _up is not None:
            _TARGET_FDX.write_bytes(_up.getvalue())
            st.session_state["pipeline_source_fdx_name"] = _up.name
            st.success(f"Saved **{_TARGET_FDX.name}** ({len(_up.getvalue()):,} bytes)")

        with st.expander("How does this pipeline thing even work?"):
            st.markdown("""
**For each scene in your screenplay, the LangGraph engine runs:**

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
 │  ┌───────────────┐  Extra model passes + repair if        │
 │  │ FOLLOW-UP     │  needed (quote/structure checks;        │
 │  │ CHECKS        │  up to 2 repair cycles)                 │
 │  └───────┬───────┘                                         │
 │          ▼                                                  │
 │   validated scene graph                                     │
 └─────────────────────────────────────────────────────────────┘
```

**Neo4j:** this tab only produces JSON + session state. **Verify → Approve & load** loads the graph.

**What "deterministic" means:** the first five error checks are plain Python — no AI.
The hallucinated-quote check substring-matches each `source_quote` against the scene text.

**Telemetry $:** **Pipeline** / **Efficiency** show **estimated** USD from token counts × `etl_core/telemetry.py` rate table — not your invoice. Cost varies with script length and how often scenes need repair.
""")

        _fdx_n = 0
        _fdx_min = 1
        _fdx_max = 1
        _fdx_loaded = bool(str(st.session_state.get("pipeline_source_fdx_name") or "").strip())
        if _TARGET_FDX.is_file():
            try:
                _fdx_mtime = _TARGET_FDX.stat().st_mtime
            except OSError:
                _fdx_mtime = 0.0
            _fdx_n, _fdx_min, _fdx_max = _cached_fdx_scene_stats(
                str(_TARGET_FDX.resolve()),
                _fdx_mtime,
            )
            # Scene stats are for range inputs; banners only after an upload in this session.
            if _fdx_loaded:
                if _fdx_n > 0:
                    st.info(
                        f"This FDX has **{_fdx_n}** scene heading(s). "
                        f"**Scene range** uses FDX scene numbers **{_fdx_min}–{_fdx_max}** (script order)."
                    )
                else:
                    st.warning(
                        "Could not read scenes from the current FDX (empty or parse error). "
                        "**Run Pipeline** will still attempt a full parse."
                    )
        else:
            st.caption("Upload a **.fdx** file to see how many scenes it contains.")

        _MODE_FULL = "Full script (100% of scenes)"
        _MODE_SMOKE = f"Smoke test (first {PIPELINE_SMOKE_FIRST_SCENES} scenes)"
        _MODE_RANGE = "Scene range (FDX scene numbers)"
        _LEGACY_MODE_SMOKE = "Smoke test (~5% sample)"
        _scene_mode = st.radio(
            "How much to process",
            options=[_MODE_FULL, _MODE_SMOKE, _MODE_RANGE],
            key="pipeline_scene_mode",
            help=f"**Smoke test** runs only the first **{PIPELINE_SMOKE_FIRST_SCENES}** scene headings in screenplay order "
            f"(or all scenes if the file has fewer). **Scene range** filters by FDX scene number (From–To, inclusive).",
        )
        if _scene_mode == _MODE_RANGE:
            rc1, rc2 = st.columns(2)
            with rc1:
                st.number_input(
                    "From scene number",
                    min_value=int(_fdx_min),
                    max_value=int(_fdx_max),
                    value=int(_fdx_min),
                    step=1,
                    key="pipeline_scene_from",
                    help="Inclusive lower bound (FDX scene number).",
                )
            with rc2:
                st.number_input(
                    "To scene number",
                    min_value=int(_fdx_min),
                    max_value=int(_fdx_max),
                    value=int(_fdx_max),
                    step=1,
                    key="pipeline_scene_to",
                    help="Inclusive upper bound (FDX scene number).",
                )

        if st.button(
            "Run Pipeline",
            type="primary",
            key="pipeline_run",
            disabled=not _TARGET_FDX.is_file(),
        ):
            st.session_state["cleanup_warning_decisions"] = {}
            st.session_state.pop("pipeline_results", None)
            st.session_state.pop("pipeline_chunk", None)
            with st.status("Pipeline running…", expanded=True) as pipe_status:
                progress = st.progress(0, text="Starting…")
                scene_log = st.container()

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

                lexicon_ids: set[str] = set()
                system_prompt = ""
                if raw_scenes:
                    pipe_status.update(label="Stage 2 — Building lexicon…", state="running")
                    progress.progress(0.10, text="Building lexicon (LLM call)…")
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
                    _n_file = len(raw_scenes)
                    _m = str(st.session_state.get("pipeline_scene_mode", _MODE_FULL))
                    if _m == _MODE_SMOKE or _m == _LEGACY_MODE_SMOKE:
                        _k = min(PIPELINE_SMOKE_FIRST_SCENES, _n_file)
                        raw_scenes = raw_scenes[:_k]
                        scene_log.write(
                            f"**Smoke test:** first **{len(raw_scenes)}** scene(s) in screenplay order "
                            f"(of **{_n_file}** in file; cap **{PIPELINE_SMOKE_FIRST_SCENES}**)."
                        )
                    elif _m == _MODE_RANGE:
                        _lo = int(st.session_state.get("pipeline_scene_from", _fdx_min))
                        _hi = int(st.session_state.get("pipeline_scene_to", _fdx_max))
                        if _lo > _hi:
                            _lo, _hi = _hi, _lo
                        _filtered = [
                            s
                            for i, s in enumerate(raw_scenes)
                            if _lo <= _scene_number_key(s, i + 1) <= _hi
                        ]
                        if not _filtered:
                            pipe_status.update(label="No scenes in range", state="error")
                            st.error(
                                f"No scenes with FDX numbers between **{_lo}** and **{_hi}** "
                                f"(file has **{_n_file}** scene heading(s))."
                            )
                            raw_scenes = []
                        else:
                            raw_scenes = _filtered
                            scene_log.write(
                                f"**Scene range** **{_lo}–{_hi}**: **{len(raw_scenes)}** scene(s) "
                                f"(of **{_n_file}** in file)."
                            )
                    else:
                        scene_log.write(f"**Full script:** **{_n_file}** scene(s).")

                    if not raw_scenes:
                        pipe_status.update(label="Nothing to extract", state="error")
                    else:
                        total = len(raw_scenes)
                        pipe_status.update(
                            label=f"Stage 3 — Extracting 0/{total} scenes (each may take ~1–2 min)…",
                            state="running",
                        )
                        all_entries: list[dict[str, Any]] = []
                        all_audit: list[dict[str, Any]] = []
                        all_warnings: list[dict[str, Any]] = []
                        all_audit_decisions: list[dict[str, Any]] = []
                        corrections: list[dict[str, Any]] = []
                        cum_tokens = 0
                        cum_cost = 0.0
                        failed_count = 0
                        done_count = 0

                        for result in extract_scenes(
                            raw_scenes,
                            system_prompt,
                            lexicon_ids=lexicon_ids,
                            enable_audit=True,
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
                            if result.audit_decisions:
                                all_audit_decisions.extend(result.audit_decisions)

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
                            "audit_decisions": all_audit_decisions,
                            "corrections": corrections,
                            "total_scenes": total,
                            "extracted": len(all_entries),
                            "failed": failed_count,
                            "tokens": int(cum_tokens or 0),
                            "cost": float(cum_cost or 0.0),
                        }
                        st.session_state.pop("verify_hitl_neo4j_load_at", None)
                        st.session_state.pop("verify_hitl_load_audit_payload", None)
                        if _up is not None:
                            st.session_state["pipeline_source_fdx_name"] = _up.name
                        _script_name = str(
                            st.session_state.get("pipeline_source_fdx_name") or ""
                        ).strip()
                        if not _script_name and _TARGET_FDX.is_file():
                            _script_name = _TARGET_FDX.name
                        _saved = _persist_pipeline_run(
                            scenes_extracted=len(all_entries),
                            total_scenes=total,
                            corrections_count=len(corrections),
                            warnings_count=len(all_warnings),
                            telemetry_tokens=int(cum_tokens or 0),
                            telemetry_cost_usd=float(cum_cost or 0.0),
                            failed_scenes=failed_count,
                            llm_auditors_enabled=True,
                            fdx_filename=_script_name,
                        )
                        if not _saved:
                            st.warning(
                                "Pipeline finished but **efficiency metrics were not saved** to Neo4j "
                                "(check connection, credentials, and that the DB allows new labels)."
                            )

        elif not _TARGET_FDX.is_file():
            st.info("Upload a **.fdx** file above to get started.")

        _pr = st.session_state.get("pipeline_results")
        if _pr is not None:
            c1, c2, c3, c4, c5 = st.columns(5)
            with c1:
                st.metric("Scenes extracted", f"{_pr['extracted']}/{_pr['total_scenes']}")
            with c2:
                st.metric("Corrections", len(_pr.get("corrections") or []))
            with c3:
                st.metric("Warnings", len(_pr.get("warnings", [])))
            with c4:
                st.metric(
                    "Telemetry tokens",
                    f"{int(_pr.get('tokens', 0) or 0):,}",
                )
            with c5:
                st.metric(
                    "Telemetry cost",
                    f"${float(_pr.get('cost', 0.0) or 0.0):.4f}",
                )

            _ads = _pr.get("audit_decisions") or []
            if _ads:
                with st.expander(f"Semantic audit decisions ({len(_ads)} rows, also appended to **audit_decisions.jsonl**)", expanded=False):
                    st.dataframe(pd.DataFrame(_ads), use_container_width=True)

            if _pr.get("corrections"):
                _render_pipeline_corrections(_pr["corrections"])

            if _pr.get("cancelled"):
                st.warning("Last run was **cancelled**; counts above reflect partial extraction.")

            if _pr.get("corrections") or _pr.get("warnings"):
                parts = []
                if _pr.get("corrections"):
                    parts.append(
                        f"**{len(_pr['corrections'])}** scene(s) with self-healing corrections (above)"
                    )
                if _pr.get("warnings"):
                    parts.append(f"**{len(_pr['warnings'])}** warning(s) to review")
                st.info(
                    f"{' · '.join(parts)}. Open **{_VERIFY_TAB_LABEL}** to decide each warning, "
                    "then **Approve & Load** into Neo4j."
                )
            elif _pr.get("extracted", 0) > 0:
                st.success(
                    "All scenes passed validation on the first try. "
                    f"Open **{_VERIFY_TAB_LABEL}** to approve and load into Neo4j."
                )


# ===================================================================
# TAB: Audit & Verify (warnings + load)
# ===================================================================

if _active == _VERIFY_TAB_LABEL:
    st.header(_VERIFY_TAB_LABEL)
    st.caption(
        "Review **warnings** from rules and semantic checks. **Approve** applies the listed edit before Neo4j load "
        "(where supported); **Decline** skips it. Self-healing **corrections** are summarized in the **Pipeline** tab."
    )
    st.info(
        "**Human-in-the-loop gate.** The **Pipeline** tab already ran validation and follow-up checks. "
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
            _checks = sorted(
                {str(w.get("check", "unknown")) for w in warnings_list if isinstance(w, dict)}
            )
            fcol1, fcol2, fcol3 = st.columns(3)
            with fcol1:
                _filter_checks = st.multiselect(
                    "Filter by check type",
                    options=_checks,
                    default=_checks,
                    help="Empty selection shows all types.",
                    key="verify_hitl_filter_checks",
                )
            with fcol2:
                _scene_order_mode = st.selectbox(
                    "Order scenes",
                    options=[
                        "Scene number (ascending)",
                        "Scene number (descending)",
                        "Fewest warnings first",
                        "Most warnings first",
                    ],
                    key="verify_hitl_scene_order",
                )
            with fcol3:
                _within_scene_sort = st.selectbox(
                    "Sort within each scene",
                    options=[
                        "Pipeline order",
                        "Check type (A–Z)",
                        "Severity (errors first)",
                    ],
                    key="verify_hitl_within_sort",
                )

            _sel = set(_filter_checks) if _filter_checks else set(_checks)
            _visible: list[tuple[int, dict[str, Any]]] = []
            for wi, w in enumerate(warnings_list):
                if not isinstance(w, dict):
                    continue
                if str(w.get("check", "unknown")) not in _sel:
                    continue
                _visible.append((wi, w))

            _n_vis = len(_visible)
            st.subheader(f"Warnings to verify ({_n_vis} of {n_warnings})")
            st.caption(
                "Cards are **grouped by scene**. Each card has an **Approve preview**, pipeline text, "
                "and optional **evidence** from the extracted graph. Use **Decline** when the graph is correct as-is. "
                "**Bulk** actions only affect **`duplicate_relationship`** warnings that pass the filter above."
            )

            by_scene: dict[int, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
            for wi, w in _visible:
                try:
                    sn_key = int(w.get("scene_number"))
                except (TypeError, ValueError):
                    sn_key = -1
                by_scene[sn_key].append((wi, w))

            def _sev_rank(wd: dict[str, Any]) -> int:
                s = str(wd.get("severity", "")).lower()
                if s == "error":
                    return 0
                if s == "warning":
                    return 1
                return 2

            for _sk in list(by_scene.keys()):
                pairs = by_scene[_sk]
                if _within_scene_sort == "Check type (A–Z)":
                    pairs = sorted(pairs, key=lambda x: (str(x[1].get("check", "")), x[0]))
                elif _within_scene_sort == "Severity (errors first)":
                    pairs = sorted(pairs, key=lambda x: (_sev_rank(x[1]), x[0]))
                by_scene[_sk] = pairs

            _scene_keys = [k for k in by_scene if k >= 0]
            if _scene_order_mode == "Scene number (ascending)":
                scene_order = sorted(_scene_keys)
            elif _scene_order_mode == "Scene number (descending)":
                scene_order = sorted(_scene_keys, reverse=True)
            elif _scene_order_mode == "Fewest warnings first":
                scene_order = sorted(_scene_keys, key=lambda k: len(by_scene[k]))
            else:
                scene_order = sorted(_scene_keys, key=lambda k: -len(by_scene[k]))
            if -1 in by_scene:
                scene_order.append(-1)

            def _scene_heading(sn_i: int) -> str:
                if sn_i < 0:
                    return "Warnings (scene unknown)"
                for e in entries:
                    if not isinstance(e, dict):
                        continue
                    try:
                        if int(e.get("scene_number") or -1) != sn_i:
                            continue
                    except (TypeError, ValueError):
                        continue
                    h = str(e.get("heading") or "").strip()
                    return f"Scene {sn_i} — {h}" if h else f"Scene {sn_i}"
                return f"Scene {sn_i}"

            _all_dup = [
                (wi, w)
                for wi, w in _visible
                if str(w.get("check", "")) == "duplicate_relationship"
            ]
            if len(_all_dup) >= 2:
                with st.expander(
                    f"Bulk approve all **`duplicate_relationship`** in view ({len(_all_dup)} warning(s), "
                    f"{len({int(w.get('scene_number') or -1) for _, w in _all_dup})} scene(s))",
                    expanded=False,
                ):
                    st.caption(
                        "Sets **Approve** on every visible duplicate-relationship card. "
                        "Re-runs merge logic once per warning at load — safe if the same tuple appears twice in the list."
                    )
                    _bd_all = st.checkbox(
                        "I confirm: approve all duplicate-relationship warnings currently shown.",
                        key="verify_bulk_dup_all_confirm",
                    )
                    if st.button(
                        "Approve all visible duplicate warnings",
                        type="secondary",
                        disabled=not _bd_all,
                        key="verify_bulk_dup_all_go",
                    ):
                        for wi, w in _all_dup:
                            wd[cleanup_warning_widget_id(w, wi)] = "approved"
                        st.rerun()

            for sn_key in scene_order:
                st.markdown(f"##### {_scene_heading(sn_key)}")
                _dup_here = [
                    (wi, w)
                    for wi, w in by_scene[sn_key]
                    if str(w.get("check", "")) == "duplicate_relationship"
                ]
                if len(_dup_here) >= 2:
                    _sid = f"s{sn_key}" if sn_key >= 0 else "s_unknown"
                    with st.expander(
                        f"Bulk approve duplicates in this scene ({len(_dup_here)} warning(s))",
                        expanded=False,
                    ):
                        st.caption("Sets **Approve** on each `duplicate_relationship` card in this scene (filtered view).")
                        _bd_sc = st.checkbox(
                            f"Confirm bulk approve for {_scene_heading(sn_key)}",
                            key=f"verify_bulk_dup_scene_{_sid}",
                        )
                        if st.button(
                            "Approve duplicate warnings in this scene",
                            type="secondary",
                            disabled=not _bd_sc,
                            key=f"verify_bulk_dup_scene_go_{_sid}",
                        ):
                            for wi, w in _dup_here:
                                wd[cleanup_warning_widget_id(w, wi)] = "approved"
                            st.rerun()
                for wi, w in by_scene[sn_key]:
                    wid = cleanup_warning_widget_id(w, wi)
                    loc = warning_json_location(w, entries)
                    check_raw = str(w.get("check", "unknown"))
                    title = warning_check_title(check_raw)
                    detail = str(w.get("detail", "") or "")
                    sev = str(w.get("severity", "") or "").strip()
                    no_auto = check_raw in ("completeness", "audit_skipped", "audit_errors_unresolved")
                    with st.container(border=True):
                        if no_auto:
                            st.warning(
                                "**No automatic graph edit** for this check — **Approve** is acknowledgment "
                                "only before load (see preview below)."
                            )
                        st.markdown(f"**{title}** (`{check_raw}`)")
                        if sev:
                            st.caption(f"Severity from pipeline: **{sev}**")
                        st.info(f"**Approve preview:** {warning_hitl_approve_preview(w, entries)}")
                        st.markdown(warning_verify_guidance(check_raw))
                        st.markdown("**What the pipeline reported**")
                        st.write(detail if detail else "_(no detail text)_")
                        st.caption("Location in extracted JSON")
                        st.code(loc, language="text")
                        with st.expander("Evidence from extracted graph", expanded=False):
                            st.markdown(warning_hitl_evidence_markdown(w, entries))
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
                        with st.expander("Optional note (audit export)", expanded=False):
                            st.caption("Included in **Decision log** CSV/JSON below. Not sent to Neo4j.")
                            st.text_input(
                                "Reviewer note",
                                key=f"verify_hl_note_{wid}",
                                placeholder="e.g. ticket id, reason for decline…",
                            )
        else:
            st.success("No warnings — nothing to verify. You can load below.")

        st.divider()

        _audit_warnings = list(pr.get("warnings", []))
        _note_map: dict[str, str] = {}
        for _wi, _w in enumerate(_audit_warnings):
            if not isinstance(_w, dict):
                continue
            _awid = cleanup_warning_widget_id(_w, _wi)
            _note_map[_awid] = str(st.session_state.get(f"verify_hl_note_{_awid}", "") or "")
        _audit_payload = build_verify_audit_payload(
            _audit_warnings,
            wd,
            _note_map,
            neo4j_loaded_at_iso=st.session_state.get("verify_hitl_neo4j_load_at"),
            pipeline_meta={
                "pipeline_extracted_scenes": pr.get("extracted"),
                "pipeline_failed_scenes": pr.get("failed"),
                "pipeline_total_scenes": pr.get("total_scenes"),
            },
        )
        _dl_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        with st.expander("Decision log (audit export)", expanded=False):
            st.caption(
                "Download **CSV** or **JSON** of every warning in this pipeline result: decision "
                "(approve / decline / unset), optional notes, detail text, and timestamps. "
                "**neo4j_load_completed_at** is set after a successful **Approve & Load** in this session; "
                "re-download to capture it."
            )
            if st.session_state.get("verify_hitl_neo4j_load_at"):
                st.success(
                    f"Last **Approve & Load** completed (UTC): "
                    f"`{st.session_state['verify_hitl_neo4j_load_at'][:19].replace('T', ' ')}`"
                )
            d1, d2 = st.columns(2)
            with d1:
                st.download_button(
                    label="Download CSV",
                    data=verify_audit_to_csv(_audit_payload).encode("utf-8"),
                    file_name=f"scriptrag_verify_audit_{_dl_stamp}.csv",
                    mime="text/csv; charset=utf-8",
                    key="verify_audit_dl_csv",
                )
            with d2:
                st.download_button(
                    label="Download JSON",
                    data=verify_audit_to_json(_audit_payload).encode("utf-8"),
                    file_name=f"scriptrag_verify_audit_{_dl_stamp}.json",
                    mime="application/json; charset=utf-8",
                    key="verify_audit_dl_json",
                )
            _last_load = st.session_state.get("verify_hitl_load_audit_payload")
            if isinstance(_last_load, dict) and (_last_load.get("decisions") or []):
                st.markdown("**Last Approve & Load** — snapshot of **all** warnings before approved rows were dropped:")
                _ls = _last_load.get("meta", {}).get("exported_at", _dl_stamp)[:19].replace(":", "")
                ll1, ll2 = st.columns(2)
                with ll1:
                    st.download_button(
                        label="Download last-load CSV",
                        data=verify_audit_to_csv(_last_load).encode("utf-8"),
                        file_name=f"scriptrag_verify_audit_last_load_{_ls}.csv",
                        mime="text/csv; charset=utf-8",
                        key="verify_audit_dl_csv_lastload",
                    )
                with ll2:
                    st.download_button(
                        label="Download last-load JSON",
                        data=verify_audit_to_json(_last_load).encode("utf-8"),
                        file_name=f"scriptrag_verify_audit_last_load_{_ls}.json",
                        mime="application/json; charset=utf-8",
                        key="verify_audit_dl_json_lastload",
                    )

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
                    _snap_warnings = list(pr.get("warnings", []))
                    _snap_decisions = dict(wd)
                    _snap_notes: dict[str, str] = {}
                    for _si, _sw in enumerate(_snap_warnings):
                        if not isinstance(_sw, dict):
                            continue
                        _swid = cleanup_warning_widget_id(_sw, _si)
                        _snap_notes[_swid] = str(
                            st.session_state.get(f"verify_hl_note_{_swid}", "") or ""
                        )
                    try:
                        to_load, edit_log = apply_approved_warning_edits(
                            entries_to_load,
                            _snap_warnings,
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
                        _neo_ts = datetime.now(timezone.utc).isoformat()
                        st.session_state["verify_hitl_neo4j_load_at"] = _neo_ts
                        st.session_state["verify_hitl_load_audit_payload"] = (
                            build_verify_audit_payload(
                                _snap_warnings,
                                _snap_decisions,
                                _snap_notes,
                                neo4j_loaded_at_iso=_neo_ts,
                                pipeline_meta={
                                    "pipeline_extracted_scenes": pr.get("extracted"),
                                    "pipeline_failed_scenes": pr.get("failed"),
                                    "pipeline_total_scenes": pr.get("total_scenes"),
                                    "neo4j_scenes_loaded": loaded,
                                },
                            )
                        )
                        pr["entries"] = to_load
                        pr["warnings"] = [
                            w
                            for wi, w in enumerate(_snap_warnings)
                            if _snap_decisions.get(cleanup_warning_widget_id(w, wi)) != "approved"
                        ]
                        for wi, w in enumerate(_snap_warnings):
                            wid = cleanup_warning_widget_id(w, wi)
                            if _snap_decisions.get(wid) == "approved":
                                wd.pop(wid, None)
                        st.cache_data.clear()
                        st.session_state["_flash"] = (
                            f"Loaded **{loaded}** scenes into Neo4j. Use **Reload Neo4j cache** if counts look stale."
                        )
                        st.rerun()


# ===================================================================
# TAB: Reconcile
# ===================================================================

if _active == "Reconcile":
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

if _active == "Data out":
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
            "Use **Reload Neo4j cache** in the sidebar after loads or external edits."
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

if _active == "Pipeline Efficiency Tracking":
    st.header("Pipeline Efficiency Tracking")
    st.caption(
        "**Agentic pipeline observability:** each finished run is a **:PipelineRun** row in Neo4j (survives screenplay reloads). "
        "Use it like production extractor metrics — **tokens**, **estimated cost**, correction/warning counts, scenes processed. "
        "**Script Name** is the uploaded screenplay’s original filename when you used the Pipeline uploader, otherwise the on-disk pipeline target (**target_script.fdx**)."
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
            _fn = r.get("fdx_filename")
            display.append({
                "Run (UTC)": str(r.get("ts", ""))[:19].replace("T", " "),
                "Script Name": str(_fn).strip() if _fn else "—",
                "Scenes extracted": f"{ext} / {tot}" if tot else str(ext),
                "Corrections": int(r.get("corrections_count", 0) or 0),
                "Warnings": int(r.get("warnings_count", 0) or 0),
                "Telemetry tokens": tel_tok,
                "Telemetry cost ($)": round(tel_cost, 4),
                "Failed scenes": int(r.get("failed_scenes", 0) or 0),
            })
        df = pd.DataFrame(display)
        st.dataframe(df, use_container_width=True, hide_index=True)
