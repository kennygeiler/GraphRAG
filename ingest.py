from __future__ import annotations

from etl_core.config import enable_langsmith, load_env

load_env()
enable_langsmith()

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from anthropic import APIStatusError
from pydantic import ValidationError

from extraction_graph import run_extraction_pipeline
from extraction_llm import _ensure_clients
from schema import SceneGraph

from pipeline_state import update_ingest_progress

_ROOT = Path(__file__).resolve().parent
DEFAULT_RAW_SCENES = _ROOT / "raw_scenes.json"
DEFAULT_MASTER_LEXICON = _ROOT / "master_lexicon.json"
DEFAULT_OUTPUT = _ROOT / "validated_graph.json"
DEFAULT_FAILED_LOG = _ROOT / "failed_scenes.log"
DEFAULT_AUDIT_LOG = _ROOT / "extraction_audit.jsonl"


@dataclass
class SceneResult:
    """Result of extracting a single scene through the LangGraph pipeline."""

    index: int
    total: int
    scene_number: int
    heading: str
    status: Literal["skip", "ok", "fixed", "failed", "empty"]
    graph_entry: dict[str, Any] | None = None
    audit_entries: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    audit_decisions: list[dict[str, Any]] = field(default_factory=list)
    tokens: int = 0
    cost: float = 0.0
    extract_tokens: int = 0
    extract_cost: float = 0.0
    fix_tokens: int = 0
    fix_cost: float = 0.0
    audit_tokens: int = 0
    audit_cost: float = 0.0
    error: str | None = None


def _append_audit_entries(path: Path, entries: list[dict[str, Any]]) -> None:
    if not entries:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in entries:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def format_scene_user_message(scene: dict[str, Any]) -> str:
    num = scene.get("number", "?")
    heading = scene.get("heading") or ""
    content = scene.get("content") or ""
    if not isinstance(content, str):
        content = str(content)
    return f"--- Scene {num} ---\n{heading}\n{content}\n"


def compact_lexicon_for_prompt(lexicon_obj: dict[str, Any]) -> str:
    """Dense lexicon text for extraction system prompt (Phase 1 — fewer tokens than pretty JSON)."""
    lines: list[str] = []
    chars = lexicon_obj.get("characters")
    if isinstance(chars, list):
        for it in chars:
            if isinstance(it, dict) and it.get("id"):
                lines.append(f"Character id={it['id']!r} name={it.get('name', '')!r}")
    locs = lexicon_obj.get("locations")
    if isinstance(locs, list):
        for it in locs:
            if isinstance(it, dict) and it.get("id"):
                lines.append(f"Location id={it['id']!r} name={it.get('name', '')!r}")
    if lines:
        return (
            "Lexicon — use exact `id` and `name` for Character and Location nodes; props may use snake_case `id` from the script.\n"
            + "\n".join(lines)
        )
    slim = json.dumps(lexicon_obj, ensure_ascii=False, separators=(",", ":"))
    return slim[:40_000]


def build_system_prompt(lexicon_block: str) -> str:
    """*lexicon_block* should be from :func:`compact_lexicon_for_prompt` (or a short legacy blob)."""
    return (
        "You are a Narrative Graph Architect. Extract the Character, Location, and Prop nodes "
        "and their Relationships from the provided scene.\n\n"
        f"CRITICAL: You may ONLY use Character/Location entities from this lexicon:\n{lexicon_block}\n\n"
        "If a character or location appears in the text but is NOT listed above, you MUST ignore them.\n\n"
        "Do not emit Event nodes; output only Character, Location, and Prop in `nodes`.\n\n"
        "Every relationship MUST include a source_quote which is the exact, verbatim text from the script "
        "that proves the relationship."
    )


def _append_failed_log(log_path: Path, scene_index: int, total: int, scene: dict[str, Any], exc: ValidationError) -> None:
    stamp = datetime.now(timezone.utc).isoformat()
    header = f"\n=== {stamp} | SCENE {scene_index}/{total} | number={scene.get('number')!r} | VALIDATION ===\n"
    body = exc.json(indent=2) if hasattr(exc, "json") else str(exc)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(header)
        f.write(body)
        f.write("\n")


def _append_api_failure_log(
    log_path: Path,
    scene_index: int,
    total: int,
    scene: dict[str, Any],
    exc: APIStatusError,
) -> None:
    stamp = datetime.now(timezone.utc).isoformat()
    header = (
        f"\n=== {stamp} | SCENE {scene_index}/{total} | number={scene.get('number')!r} "
        f"| API HTTP {exc.status_code} ===\n"
    )
    lines = [f"message: {exc.message}\n", f"request_id: {getattr(exc, 'request_id', None)!r}\n", f"body: {exc.body!r}\n"]
    with log_path.open("a", encoding="utf-8") as f:
        f.write(header)
        f.writelines(lines)


def _append_other_failure_log(
    log_path: Path,
    scene_index: int,
    total: int,
    scene: dict[str, Any],
    exc: BaseException,
) -> None:
    stamp = datetime.now(timezone.utc).isoformat()
    header = f"\n=== {stamp} | SCENE {scene_index}/{total} | number={scene.get('number')!r} | ERROR ===\n"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(header)
        f.write(f"{type(exc).__name__}: {exc}\n")


def _scene_number_key(scene: dict[str, Any], fallback_index: int) -> int:
    raw = scene.get("number")
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return int(fallback_index)


def _load_existing_by_scene_number(path: Path) -> dict[int, dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, list):
        return {}
    out: dict[int, dict[str, Any]] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        sn = entry.get("scene_number")
        if sn is None:
            continue
        try:
            out[int(sn)] = entry
        except (TypeError, ValueError):
            continue
    return out


def _ordered_entries(by_num: dict[int, dict[str, Any]], scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    for s in scenes:
        sn = _scene_number_key(s, len(ordered) + 1)
        if sn in by_num:
            ordered.append(by_num[sn])
    return ordered


def _write_validated_output(path: Path, by_num: dict[int, dict[str, Any]], scenes: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = _ordered_entries(by_num, scenes)
    path.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_single_scene_extraction(
    scenes: list[dict[str, Any]],
    i: int,
    system_prompt: str,
    by_num: dict[int, dict[str, Any]],
    *,
    lexicon_ids: set[str] | None = None,
    enable_audit: bool = True,
) -> SceneResult:
    """Run extraction for scene index *i* (1-based, same as ``enumerate(..., start=1)``).

    Mutates *by_num* for skip cache, empty graphs, and successful extractions.
    """
    _ensure_clients()
    total = len(scenes)
    scene = scenes[i - 1]
    sn = _scene_number_key(scene, i)
    heading = scene.get("heading") or ""

    if sn in by_num:
        return SceneResult(
            index=i, total=total, scene_number=sn, heading=heading,
            status="skip", graph_entry=by_num[sn],
        )

    if not (scene.get("content") or "").strip():
        entry = {
            "scene_number": scene.get("number"),
            "heading": heading,
            "graph": SceneGraph().model_dump(mode="json"),
        }
        by_num[sn] = entry
        return SceneResult(
            index=i, total=total, scene_number=sn, heading=heading,
            status="empty", graph_entry=entry,
        )

    user_text = format_scene_user_message(scene)
    try:
        graph, audit_entries, pipe_err, telem, pipe_warnings, audit_decisions = run_extraction_pipeline(
            sn, user_text, system_prompt,
            lexicon_ids=lexicon_ids,
            enable_audit=enable_audit,
        )
        if pipe_err:
            raise RuntimeError(pipe_err)
    except Exception as exc:
        return SceneResult(
            index=i, total=total, scene_number=sn, heading=heading,
            status="failed", audit_entries=[],
            error=f"{type(exc).__name__}: {exc}",
        )

    had_fix = any(
        e.get("node") in ("fixer", "auditor_auto_apply")
        for e in audit_entries
    )
    entry = {
        "scene_number": scene.get("number"),
        "heading": heading,
        "graph": graph.model_dump(mode="json"),
    }
    by_num[sn] = entry
    return SceneResult(
        index=i, total=total, scene_number=sn, heading=heading,
        status="fixed" if had_fix else "ok",
        graph_entry=entry,
        audit_entries=audit_entries,
        warnings=pipe_warnings,
        audit_decisions=audit_decisions,
        tokens=int(telem.get("total_tokens", 0) or 0),
        cost=float(telem.get("total_cost", 0.0) or 0.0),
        extract_tokens=int(telem.get("extract_tokens", 0) or 0),
        extract_cost=float(telem.get("extract_cost", 0.0) or 0.0),
        fix_tokens=int(telem.get("fix_tokens", 0) or 0),
        fix_cost=float(telem.get("fix_cost", 0.0) or 0.0),
        audit_tokens=int(telem.get("audit_tokens", 0) or 0),
        audit_cost=float(telem.get("audit_cost", 0.0) or 0.0),
    )


def extract_scenes(
    scenes: list[dict[str, Any]],
    system_prompt: str,
    *,
    existing_by_num: dict[int, dict[str, Any]] | None = None,
    lexicon_ids: set[str] | None = None,
    enable_audit: bool = True,
) -> Iterator[SceneResult]:
    """Yield one :class:`SceneResult` per scene.

    Scenes already present in *existing_by_num* are yielded as ``status="skip"``.
    Empty scenes are yielded as ``status="empty"``.  The caller decides what to
    do with each result (write to disk, update UI, etc.).
    """
    by_num = dict(existing_by_num or {})
    total = len(scenes)

    for i, _scene in enumerate(scenes, start=1):
        yield run_single_scene_extraction(
            scenes, i, system_prompt, by_num,
            lexicon_ids=lexicon_ids, enable_audit=enable_audit,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch-ingest raw scenes into validated SceneGraph JSON.")
    parser.add_argument(
        "--raw-scenes",
        type=Path,
        default=DEFAULT_RAW_SCENES,
        help="Path to raw_scenes.json (default: ./raw_scenes.json)",
    )
    parser.add_argument(
        "--lexicon",
        type=Path,
        default=DEFAULT_MASTER_LEXICON,
        help="Path to master_lexicon.json (default: ./master_lexicon.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Path for validated_graph.json (default: ./validated_graph.json)",
    )
    parser.add_argument(
        "--failed-log",
        type=Path,
        default=DEFAULT_FAILED_LOG,
        help="Append validation errors here (default: ./failed_scenes.log)",
    )
    parser.add_argument(
        "--audit-log",
        type=Path,
        default=DEFAULT_AUDIT_LOG,
        help="Append LangGraph extract/validator/fixer audit JSON lines (default: ./extraction_audit.jsonl)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Explicit flag for UI/scripts; partial files are auto-continued even without this.",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Delete validated_graph.json before running (full re-extract every scene).",
    )
    parser.add_argument(
        "--no-checkpoint",
        action="store_true",
        help="Write validated_graph.json only once at the end (default: save after each successful scene).",
    )
    args = parser.parse_args()

    _key = os.environ.get("ANTHROPIC_API_KEY")
    print(
        f"Using API Key: {_key[:5]}***" if _key else "Using API Key: (not set)",
        flush=True,
    )

    if not args.lexicon.is_file():
        print(f"❌ master lexicon not found: {args.lexicon}", flush=True)
        sys.exit(1)
    if not args.raw_scenes.is_file():
        print(f"❌ raw scenes not found: {args.raw_scenes}", flush=True)
        sys.exit(1)

    lexicon_obj = json.loads(args.lexicon.read_text(encoding="utf-8"))
    lexicon_ids: set[str] = set()
    if isinstance(lexicon_obj, dict):
        for entry in lexicon_obj.get("characters", []):
            if isinstance(entry, dict) and entry.get("id"):
                lexicon_ids.add(entry["id"])
        for entry in lexicon_obj.get("locations", []):
            if isinstance(entry, dict) and entry.get("id"):
                lexicon_ids.add(entry["id"])

    scenes: list[dict[str, Any]] = json.loads(args.raw_scenes.read_text(encoding="utf-8"))
    if not isinstance(scenes, list):
        print("❌ raw_scenes.json must be a JSON array.", flush=True)
        sys.exit(1)

    total = len(scenes)
    system_prompt = build_system_prompt(compact_lexicon_for_prompt(lexicon_obj))

    expected_nums = {_scene_number_key(s, j + 1) for j, s in enumerate(scenes)}

    if args.fresh and args.output.is_file():
        args.output.unlink()
        print(f"--fresh: removed {args.output.name}", flush=True)

    loaded = _load_existing_by_scene_number(args.output) if args.output.is_file() else {}
    by_num: dict[int, dict[str, Any]] = {}
    if loaded:
        by_num = {k: v for k, v in loaded.items() if k in expected_nums}

    if not args.fresh and by_num and len(expected_nums) > 0 and expected_nums <= set(by_num.keys()):
        print(
            f"{args.output.name} already has all {len(expected_nums)} scene(s). Nothing to do. "
            "Use --fresh to re-extract.",
            flush=True,
        )
        ordered = _ordered_entries(by_num, scenes)
        update_ingest_progress(
            raw_scene_count=total,
            entries=ordered,
            finished=True,
            last_scene_index=total,
        )
        sys.exit(0)

    if by_num:
        remaining = len(expected_nums - set(by_num.keys()))
        print(
            f"↩ Continuing from {len(by_num)} scene graph(s) on disk; "
            f"{remaining} scene number(s) still to extract.",
            flush=True,
        )

    args.failed_log.parent.mkdir(parents=True, exist_ok=True)
    interrupted = False
    last_scene_index = 0
    checkpoint = not args.no_checkpoint
    cumulative_tokens = 0
    cumulative_cost = 0.0

    try:
        for result in extract_scenes(scenes, system_prompt, existing_by_num=by_num, lexicon_ids=lexicon_ids):
            last_scene_index = result.index
            sn = result.scene_number

            if result.status == "skip":
                print(f"[SCENE {result.index}/{total}] number={sn} — skip (already in output).", flush=True)
                continue

            if result.status == "empty":
                print(f"[SCENE {result.index}/{total}] number={sn} — empty content.", flush=True)
            elif result.status == "failed":
                print(f"❌ Scene {result.index} failed: {result.error}", flush=True)
                _append_other_failure_log(
                    args.failed_log, result.index, total,
                    scenes[result.index - 1], RuntimeError(result.error or "unknown"),
                )
                continue
            else:
                label = "extracted (fixer intervened)" if result.status == "fixed" else "extracted"
                print(f"[SCENE {result.index}/{total}] number={sn} — {label}.", flush=True)

            _append_audit_entries(args.audit_log, result.audit_entries)
            cumulative_tokens += result.tokens
            cumulative_cost += result.cost

            if result.graph_entry:
                by_num[sn] = result.graph_entry

            if checkpoint and sn in by_num:
                _write_validated_output(args.output, by_num, scenes)
                ordered = _ordered_entries(by_num, scenes)
                expected = {_scene_number_key(s, j + 1) for j, s in enumerate(scenes)}
                update_ingest_progress(
                    raw_scene_count=total,
                    entries=ordered,
                    finished=expected <= set(by_num.keys()),
                    last_scene_index=result.index,
                )
            time.sleep(1)
    except KeyboardInterrupt:
        interrupted = True
        print(
            "\n⚠️ Interrupted (Ctrl+C) — saving partial results to "
            f"{args.output.name} …",
            flush=True,
        )
    finally:
        ordered = _ordered_entries(by_num, scenes)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(ordered, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        expected_nums_fin = {_scene_number_key(s, j + 1) for j, s in enumerate(scenes)}
        finished = not interrupted and (
            len(expected_nums_fin) == 0 or expected_nums_fin <= set(by_num.keys())
        )
        update_ingest_progress(
            raw_scene_count=total,
            entries=ordered,
            finished=finished,
            last_scene_index=last_scene_index,
        )

        total_relationships = sum(
            len(entry.get("graph", {}).get("relationships", []))
            for entry in ordered
            if isinstance(entry.get("graph"), dict)
        )
        if interrupted:
            print(
                f"Saved partial run: {len(ordered)} scene graph(s) in {args.output.name} "
                f"(interrupted at scene {last_scene_index}/{total}). "
                f"Relationships in file (total): {total_relationships}",
                flush=True,
            )
        else:
            print(
                f"Done. Wrote {len(ordered)} scene graph(s) to {args.output.name}. "
                f"Successfully extracted relationships (total): {total_relationships}",
                flush=True,
            )
        if cumulative_tokens:
            print(
                f"Telemetry: {cumulative_tokens:,} tokens | ${cumulative_cost:.4f} estimated cost",
                flush=True,
            )
        missing_ct = len(expected_nums_fin - set(by_num.keys()))
        if missing_ct:
            print(
                f"Note: {missing_ct} scene(s) still have no saved graph; see {args.failed_log.name}. "
                "Re-run ingest (partial files are continued automatically) or use --resume.",
                flush=True,
            )


if __name__ == "__main__":
    main()
