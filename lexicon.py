from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

import json
import os
import sys
from pathlib import Path
from typing import Any

import instructor
from anthropic import Anthropic
from pydantic import BaseModel, Field

from schema import SnakeCaseId


class LexiconInputError(Exception):
    """Invalid or unusable input path / JSON for master lexicon."""


_FDX_ERROR_MSG = (
    "❌ ERROR: lexicon.py requires the JSON output from parser.py. "
    "Run: uv run python lexicon.py raw_scenes.json"
)


def _load_raw_scenes_json_array(raw_path: Path) -> list[dict[str, Any]]:
    path = raw_path.expanduser().resolve()
    if path.suffix.lower() == ".fdx":
        raise LexiconInputError(_FDX_ERROR_MSG)
    if not path.is_file():
        raise LexiconInputError(f"❌ ERROR: File not found: {path}")
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LexiconInputError(f"❌ ERROR: Could not read file: {path}") from exc
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise LexiconInputError(
            f"❌ ERROR: Invalid JSON while reading file: {path}\n  Detail: {exc}"
        ) from None
    if not isinstance(data, list):
        raise LexiconInputError(
            f"❌ ERROR: Expected a JSON array of scenes in: {path}"
        )
    return data


_ROOT = Path(__file__).resolve().parent
RAW_SCENES_PATH = _ROOT / "raw_scenes.json"
MASTER_LEXICON_PATH = _ROOT / "master_lexicon.json"
LEXICON_PATH = _ROOT / "lexicon.json"

# Legacy dated Sonnet IDs (e.g. claude-3-5-sonnet-20240620) return 404 on current API.
_PRIMARY_MODEL = "claude-sonnet-4-6"
_FALLBACK_MODEL = "claude-3-haiku-20240307"
_SONNET_MAX_TOKENS = 8192
_HAIKU_MAX_TOKENS = 4096

_instructor_client: Any | None = None


def _get_anthropic_client() -> Any:
    global _instructor_client
    if _instructor_client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if api_key is None:
            print("❌ Missing ANTHROPIC_API_KEY. Please add it to your .env file.", flush=True)
            sys.exit(1)
        _instructor_client = instructor.from_anthropic(Anthropic(api_key=api_key))
    return _instructor_client


def call_claude_with_fallback(
    system_prompt: str,
    user_text: str,
    response_model: type,
    *,
    max_tokens: int = 8192,
    temperature: float = 0,
) -> Any:
    client = _get_anthropic_client()
    max_tokens_sonnet = min(max_tokens, _SONNET_MAX_TOKENS)
    max_tokens_haiku = _HAIKU_MAX_TOKENS
    try:
        return client.messages.create(
            model=_PRIMARY_MODEL,
            max_tokens=max_tokens_sonnet,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_text}],
            response_model=response_model,
            max_retries=1,
        )
    except Exception as e:
        print(f"⚠️ Primary model failed. Rerouting to Haiku fallback... (Error: {e})", flush=True)
        return client.messages.create(
            model=_FALLBACK_MODEL,
            max_tokens=max_tokens_haiku,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_text}],
            response_model=response_model,
            max_retries=3,
        )


_MASTER_SYSTEM = """You are a screenplay lexicon auditor. You read raw scene JSON and output ONE canonical master list of characters and locations.

RUTHLESS DEDUPLICATION:
- Strip ages, parentheticals, and physical descriptions from names (e.g. 'ALAN (60s)' -> canonical name 'Alan'; 'JIMMY (30s)' -> 'Jimmy').
- Merge case variants: 'ALAN', 'Alan', 'alan' are ONE person — pick a single Title Case display name and one snake_case id (e.g. alan).
- Merge location aliases that clearly denote the same place (e.g. 'THEATER LOBBY' and 'LOBBY' when both mean Cinema Four's lobby — one entry, best canonical name).
- Do not list the same person twice under different spellings.

ENTITY FILTERING (person vs abstract):
- Decide from dialogue and action whether a title refers to a person or a concept.
- Example pattern: 'The General' — if dialogue treats them as staff/security (e.g. janitor, escorting someone, uniform), they are a CHARACTER, not an abstract role. Include them in characters with a sensible id (e.g. the_general).

LOCATIONS:
- Macro settings only (theater, office, screening room, etc.). Skip generic furniture-only mentions unless they are named recurring settings.

OUTPUT:
- characters: each entry has id (snake_case) and name (canonical Title Case display string, no age tags).
- locations: same shape.
- Lists must be deduplicated and sorted logically (e.g. alphabetically by name)."""


class CanonicalEntry(BaseModel):
    id: SnakeCaseId = Field(description="Unique snake_case identifier")
    name: str = Field(description="Canonical display name (Title Case), no ages or parenthetical descriptors")


class MasterLexicon(BaseModel):
    characters: list[CanonicalEntry] = Field(description="Deduplicated canonical characters")
    locations: list[CanonicalEntry] = Field(description="Deduplicated canonical locations")


class ScriptLexicon(BaseModel):
    canonical_characters: list[SnakeCaseId] = Field(
        description="Named recurring characters as snake_case ids.",
    )
    canonical_locations: list[SnakeCaseId] = Field(
        description="Major locations as snake_case ids.",
    )


def _combine_raw_scenes_for_prompt(scenes: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for scene in scenes:
        num = scene.get("number", "?")
        heading = scene.get("heading") or ""
        content = scene.get("content") or ""
        if not isinstance(content, str):
            content = str(content)
        parts.append(f"--- Scene {num} ---\n{heading}\n{content}\n")
    return "\n".join(parts).strip()


def build_master_lexicon(
    raw_scenes_path: Path | None = None,
    out_path: Path | None = None,
    *,
    write_slim_lexicon_json: bool = True,
) -> MasterLexicon:
    """Read raw_scenes.json, call Claude + Instructor, write master_lexicon.json."""
    raw_path = Path(raw_scenes_path or RAW_SCENES_PATH)
    scenes = _load_raw_scenes_json_array(raw_path)

    combined = _combine_raw_scenes_for_prompt(scenes)
    if not combined:
        master = MasterLexicon(characters=[], locations=[])
    else:
        user_text = (
            "Build the master lexicon from the following screenplay scenes (raw export).\n\n" + combined
        )
        master = call_claude_with_fallback(
            _MASTER_SYSTEM,
            user_text,
            MasterLexicon,
            max_tokens=8192,
            temperature=0,
        )

    out = Path(out_path or MASTER_LEXICON_PATH)
    out.write_text(master.model_dump_json(indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if write_slim_lexicon_json:
        slim = {
            "canonical_characters": [e.id for e in master.characters],
            "canonical_locations": [e.id for e in master.locations],
        }
        LEXICON_PATH.write_text(
            json.dumps(slim, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    return master


def _print_validation_prompt(master: MasterLexicon) -> None:
    nc = len(master.characters)
    nl = len(master.locations)
    print(
        f"I found these {nc} characters and {nl} locations. "
        'Should "The Levine Family" be a character or an background entity? '
        'Should "The General" be pinned as a Lead? Update the JSON manually if needed.',
        flush=True,
    )


_LEXICON_SYSTEM = """You are a screenplay analyst performing a global lexicon pass over scene excerpts.

Extract only:
1) Named, recurring characters (ignore unnamed extras, crowds, and one-line background roles unless they are clearly named and story-relevant).
2) Major settings / locations (macro-settings that recur or anchor scenes—not props, furniture, weather, or tiny set details).

Output snake_case identifiers only for the lists. Use lowercase ASCII letters, digits, underscores; each id must start with a letter."""


def _combine_scene_text(scenes: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for i, scene in enumerate(scenes, start=1):
        heading = scene.get("heading") or ""
        text = scene.get("text") or scene.get("content") or ""
        if not isinstance(text, str):
            text = str(text)
        parts.append(f"--- Scene {i} ---\n{heading}\n{text}\n")
    return "\n".join(parts).strip()


def generate_lexicon(scenes: list[dict[str, Any]]) -> None:
    """Build ScriptLexicon from in-memory scenes; write lexicon.json only (not master_lexicon)."""
    combined = _combine_scene_text(scenes)
    if not combined:
        payload = ScriptLexicon(canonical_characters=[], canonical_locations=[])
        LEXICON_PATH.write_text(payload.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return

    user_text = (
        "From the following screenplay scene excerpts, produce canonical_characters and "
        "canonical_locations as specified.\n\n" + combined
    )
    lexicon = call_claude_with_fallback(
        _LEXICON_SYSTEM,
        user_text,
        ScriptLexicon,
        max_tokens=4096,
        temperature=0,
    )
    LEXICON_PATH.write_text(lexicon.model_dump_json(indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raw_arg = Path(sys.argv[1]) if len(sys.argv) > 1 else RAW_SCENES_PATH
    try:
        master = build_master_lexicon(raw_arg)
    except LexiconInputError as err:
        print(str(err), flush=True)
        sys.exit(1)
    print("Characters:", flush=True)
    for e in master.characters:
        print(f"  - {e.name} ({e.id})", flush=True)
    print("Locations:", flush=True)
    for e in master.locations:
        print(f"  - {e.name} ({e.id})", flush=True)
    print(flush=True)
    _print_validation_prompt(master)
    print(f"\nWrote {MASTER_LEXICON_PATH.name} (and synced {LEXICON_PATH.name}).", flush=True)
