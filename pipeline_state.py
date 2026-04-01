"""Lightweight pipeline progress for CLI + Streamlit (no dependency on Streamlit)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent
PIPELINE_STATE_PATH = _ROOT / "pipeline_state.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state() -> dict[str, Any]:
    if not PIPELINE_STATE_PATH.is_file():
        return {"version": 1, "updated_at": None, "ingest": {}}
    try:
        data = json.loads(PIPELINE_STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("version", 1)
            data.setdefault("ingest", {})
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"version": 1, "updated_at": None, "ingest": {}}


def save_state(state: dict[str, Any]) -> None:
    state = dict(state)
    state["version"] = 1
    state["updated_at"] = _utc_now_iso()
    PIPELINE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(state, indent=2, ensure_ascii=False) + "\n"
    tmp = PIPELINE_STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(PIPELINE_STATE_PATH)


def update_ingest_progress(
    *,
    raw_scene_count: int,
    entries: list[dict[str, Any]],
    finished: bool,
    last_scene_index: int | None = None,
) -> None:
    state = load_state()
    nums: list[int] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        sn = e.get("scene_number")
        if sn is not None:
            try:
                nums.append(int(sn))
            except (TypeError, ValueError):
                pass
    state["ingest"] = {
        "raw_scene_count": raw_scene_count,
        "entries_count": len(entries),
        "scene_numbers_succeeded": sorted(set(nums)),
        "finished": finished,
        "last_scene_index": last_scene_index,
        "last_touch_at": _utc_now_iso(),
    }
    save_state(state)


def record_neo4j_loader_ok(*, entries_loaded: int, path_name: str) -> None:
    state = load_state()
    state["neo4j_loader"] = {
        "last_ok_at": _utc_now_iso(),
        "entries_loaded": int(entries_loaded),
        "path": str(path_name),
    }
    save_state(state)


def filesystem_snapshot(root: Path | None = None) -> dict[str, Any]:
    """Derive pipeline status from artifacts (source of truth for 'how far')."""
    root = root or _ROOT
    raw_path = root / "raw_scenes.json"
    master_path = root / "master_lexicon.json"
    val_path = root / "validated_graph.json"
    fdx_path = root / "target_script.fdx"
    out: dict[str, Any] = {
        "root": str(root),
        "target_script_fdx": fdx_path.is_file(),
        "parser": None,
        "lexicon": None,
        "ingest": None,
    }
    raw_list: list[Any] = []
    if raw_path.is_file():
        try:
            raw_list = json.loads(raw_path.read_text(encoding="utf-8"))
            if not isinstance(raw_list, list):
                raw_list = []
            out["parser"] = {
                "ok": True,
                "path": str(raw_path.name),
                "scene_count": len(raw_list),
            }
        except (json.JSONDecodeError, OSError) as exc:
            out["parser"] = {"ok": False, "error": str(exc)}
    else:
        out["parser"] = {"ok": False, "error": "raw_scenes.json missing"}

    out["lexicon"] = (
        {"ok": True, "path": str(master_path.name)}
        if master_path.is_file()
        else {"ok": False, "error": "master_lexicon.json missing"}
    )

    expected_numbers: list[int] = []
    for s in raw_list:
        if isinstance(s, dict) and s.get("number") is not None:
            try:
                expected_numbers.append(int(s["number"]))
            except (TypeError, ValueError):
                pass

    if val_path.is_file():
        try:
            val_list = json.loads(val_path.read_text(encoding="utf-8"))
            if not isinstance(val_list, list):
                val_list = []
            have = set()
            for e in val_list:
                if isinstance(e, dict) and e.get("scene_number") is not None:
                    try:
                        have.add(int(e["scene_number"]))
                    except (TypeError, ValueError):
                        pass
            expected_set = set(expected_numbers)
            missing = sorted(expected_set - have) if expected_set else []
            total = len(expected_numbers) if expected_numbers else len(raw_list)
            n = len(val_list)
            out["ingest"] = {
                "ok": True,
                "path": str(val_path.name),
                "entries_in_file": n,
                "raw_scene_count": total,
                "unique_scene_numbers_in_file": len(have),
                "missing_scene_numbers": missing[:50],
                "missing_count": len(missing),
                "is_complete": total > 0 and len(missing) == 0 and n >= total,
            }
        except (json.JSONDecodeError, OSError) as exc:
            out["ingest"] = {"ok": False, "error": str(exc)}
    else:
        out["ingest"] = {"ok": False, "error": "validated_graph.json missing"}

    st = load_state()
    if st.get("neo4j_loader"):
        out["neo4j_loader"] = st["neo4j_loader"]
    if st.get("ingest"):
        out["ingest_state_file"] = st["ingest"]

    return out
