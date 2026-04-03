from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

import pandas as pd
import streamlit as st

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
    "review corrections in **Pipeline**, **Verify** warnings, then explore the data."
)

if _flash := st.session_state.pop("_flash", None):
    st.success(_flash)

# --------------- sidebar --------------------------------------------
with st.sidebar:
    st.header("Controls")
    if _SCRIPTRAG_DEMO_LAYOUT:
        st.caption(
            "**Demo layout** (`SCRIPTRAG_DEMO_LAYOUT=1`): **Verify → Data out** before **Reconcile** "
            "— for pipeline storytelling."
        )
    if st.button(
        "Reload Neo4j cache",
        help="Clears Streamlit cache after pipeline, load, or external graph edits (Data out, Reconcile).",
        key="sidebar_reload",
    ):
        st.cache_data.clear()
        st.session_state["_flash"] = "Cache cleared — re-querying Neo4j."
        st.rerun()

    with st.expander("Reset graph data", expanded=False):
        st.caption(
            "Clears the **screenplay graph** in Neo4j and removes pipeline JSON on disk. "
            "**:PipelineRun** rows are kept — **Pipeline Efficiency Tracking** history stays."
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

# --------------- section navigation ---------------------------------
# st.tabs() resets the visible tab on every rerun; a keyed radio keeps the user
# on the same section when widgets below change (e.g. Data out recipe query).
_tab_labels: list[str] = []
if _PIPELINE_ENABLED:
    _tab_labels.append("Pipeline")
if _SCRIPTRAG_DEMO_LAYOUT:
    _tab_labels += [
        "Verify",
        "Data out",
        "Reconcile",
        "Pipeline Efficiency Tracking",
    ]
else:
    _tab_labels += [
        "Verify",
        "Reconcile",
        "Data out",
        "Pipeline Efficiency Tracking",
    ]

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
            "Each scene runs **extract → validate ⇄ fix** and, if **Enable LLM auditors** is on, **audit ⇄ audit fixer**. "
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
 │  ┌───────────────┐  If checkbox ON: 3 Claude calls        │
 │  │ LLM AUDITORS  │  (quote fidelity, completeness,         │
 │  │  (3 LLM)      │   attribution). If OFF, skip this box.   │
 │  └───────┬───────┘                                         │
 │          │                                                  │
 │     pass │    fail -> AUDIT FIXER -> AUDIT (up to 2x)      │
 │          ▼                                                  │
 │   validated scene graph                                     │
 └─────────────────────────────────────────────────────────────┘
```

**Neo4j:** this tab only produces JSON + session state. **Verify → Approve & load** loads the graph.

**What "deterministic" means:** the first five error checks are plain Python — no AI.
The hallucinated-quote check substring-matches each `source_quote` against the scene text.

**LLM auditors:** optional — uncheck **Enable LLM auditors** for deterministic-only passes (faster).

**Telemetry $:** **Pipeline** / **Efficiency** show **estimated** USD from token counts × `etl_core/telemetry.py` rate table — not your invoice. Ballpark for ~86 scenes: **~$0.85** without auditors vs **~$2.50** with — depends on length and retries.
""")

        col_opt1, col_opt2 = st.columns(2)
        with col_opt1:
            _scene_limit = st.number_input(
                "Scene limit",
                min_value=1,
                max_value=999,
                value=86,
                help="How many scenes to process. Set low (e.g. 3–5) for a quick pipeline smoke test.",
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

if _active == "Verify":
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
