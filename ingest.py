from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anthropic import APIStatusError
from pydantic import ValidationError

from extraction_graph import run_extraction_pipeline
from extraction_llm import _get_anthropic_client
from schema import SceneGraph

from pipeline_state import update_ingest_progress

_ROOT = Path(__file__).resolve().parent
DEFAULT_RAW_SCENES = _ROOT / "raw_scenes.json"
DEFAULT_MASTER_LEXICON = _ROOT / "master_lexicon.json"
DEFAULT_OUTPUT = _ROOT / "validated_graph.json"
DEFAULT_FAILED_LOG = _ROOT / "failed_scenes.log"
DEFAULT_AUDIT_LOG = _ROOT / "extraction_audit.jsonl"


def _append_audit_entries(path: Path, entries: list[dict[str, Any]]) -> None:
    if not entries:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in entries:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _format_scene_user_message(scene: dict[str, Any]) -> str:
    num = scene.get("number", "?")
    heading = scene.get("heading") or ""
    content = scene.get("content") or ""
    if not isinstance(content, str):
        content = str(content)
    return f"--- Scene {num} ---\n{heading}\n{content}\n"


def _build_system_prompt(lexicon_content: str) -> str:
    return (
        "You are a Narrative Graph Architect. Extract the Character, Location, and Prop nodes "
        "and their Relationships from the provided scene.\n\n"
        f"CRITICAL: You may ONLY use names from this Lexicon: {lexicon_content}. "
        "If a character or location appears in the text but is NOT in the Lexicon, you MUST ignore them.\n\n"
        "The lexicon lists canonical characters and locations (each with `id` and `name`). "
        "For Character and Location nodes, use those exact `id` and `name` values only. "
        "For Prop nodes (plot-significant objects only), use snake_case `id` and a `name` taken from the script "
        "when the lexicon does not list props.\n\n"
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
    lexicon_content = json.dumps(lexicon_obj, ensure_ascii=False, indent=2)

    scenes: list[dict[str, Any]] = json.loads(args.raw_scenes.read_text(encoding="utf-8"))
    if not isinstance(scenes, list):
        print("❌ raw_scenes.json must be a JSON array.", flush=True)
        sys.exit(1)

    total = len(scenes)
    system_prompt = _build_system_prompt(lexicon_content)
    _get_anthropic_client()

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

    try:
        for i, scene in enumerate(scenes, start=1):
            last_scene_index = i
            sn = _scene_number_key(scene, i)
            if sn in by_num:
                print(f"[SCENE {i}/{total}] number={sn} — skip (already in output).", flush=True)
                continue

            print(f"[SCENE {i}/{total}] number={sn} — extracting...", flush=True)
            user_text = _format_scene_user_message(scene)
            if not (scene.get("content") or "").strip():
                by_num[sn] = {
                    "scene_number": scene.get("number"),
                    "heading": scene.get("heading"),
                    "graph": SceneGraph().model_dump(mode="json"),
                }
            else:
                try:
                    graph, audit_entries, pipe_err = run_extraction_pipeline(sn, user_text, system_prompt)
                    _append_audit_entries(args.audit_log, audit_entries)
                    if pipe_err:
                        raise RuntimeError(pipe_err)
                except ValidationError as exc:
                    _append_failed_log(args.failed_log, i, total, scene, exc)
                except APIStatusError as exc:
                    _append_api_failure_log(args.failed_log, i, total, scene, exc)
                except Exception as exc:
                    print(f"❌ Scene {i} failed ({type(exc).__name__}): {exc}", flush=True)
                    _append_other_failure_log(args.failed_log, i, total, scene, exc)
                else:
                    by_num[sn] = {
                        "scene_number": scene.get("number"),
                        "heading": scene.get("heading"),
                        "graph": graph.model_dump(mode="json"),
                    }

            if checkpoint and sn in by_num:
                _write_validated_output(args.output, by_num, scenes)
                ordered = _ordered_entries(by_num, scenes)
                expected = {_scene_number_key(s, j + 1) for j, s in enumerate(scenes)}
                update_ingest_progress(
                    raw_scene_count=total,
                    entries=ordered,
                    finished=expected <= set(by_num.keys()),
                    last_scene_index=i,
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
        missing_ct = len(expected_nums_fin - set(by_num.keys()))
        if missing_ct:
            print(
                f"Note: {missing_ct} scene(s) still have no saved graph; see {args.failed_log.name}. "
                "Re-run ingest (partial files are continued automatically) or use --resume.",
                flush=True,
            )


if __name__ == "__main__":
    main()
