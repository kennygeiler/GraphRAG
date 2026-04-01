from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, TypedDict


class SceneDict(TypedDict):
    scene_number: int
    heading: str
    text: str


class RawSceneDict(TypedDict):
    number: int
    heading: str
    content: str


_BODY_TYPES = frozenset({"Action", "Character", "Dialogue", "Parenthetical"})


def _local_tag(element: ET.Element) -> str:
    tag = element.tag
    return tag.split("}", 1)[1] if tag.startswith("{") else tag


def _direct_text(paragraph: ET.Element) -> str:
    parts: list[str] = []
    for child in paragraph:
        if _local_tag(child) != "Text":
            continue
        parts.append("".join(child.itertext()).strip())
    return "\n".join(p for p in parts if p).strip()


def _scene_heading(paragraph: ET.Element) -> str:
    slug = _direct_text(paragraph)
    if slug:
        return slug
    for child in paragraph:
        if _local_tag(child) != "SceneProperties":
            continue
        title = child.get("Title")
        if title and title.strip():
            return title.strip()
    return ""


def _content_element(root: ET.Element) -> ET.Element | None:
    for child in root:
        if _local_tag(child) == "Content":
            return child
    return None


def parse_fdx_to_raw_scenes(path: str | Path) -> list[RawSceneDict]:
    """Parse .fdx into scenes with number, heading, and combined body content."""
    tree = ET.parse(path)
    root = tree.getroot()
    content = _content_element(root)
    if content is None:
        return []

    scenes: list[RawSceneDict] = []
    current: RawSceneDict | None = None
    scene_index = 0

    for node in content:
        if _local_tag(node) != "Paragraph":
            continue
        ptype = node.get("Type")
        if ptype == "Scene Heading":
            if current is not None:
                current["content"] = current["content"].strip()
                scenes.append(current)
            scene_index += 1
            num_attr = node.get("Number")
            if num_attr is not None and num_attr.isdigit():
                number = int(num_attr)
            else:
                number = scene_index
            current = {
                "number": number,
                "heading": _scene_heading(node),
                "content": "",
            }
            continue
        if current is None or ptype not in _BODY_TYPES:
            continue
        block = _direct_text(node)
        if not block:
            continue
        if current["content"]:
            current["content"] += "\n" + block
        else:
            current["content"] = block

    if current is not None:
        current["content"] = current["content"].strip()
        scenes.append(current)

    return scenes


def write_raw_scenes_json(
    fdx_path: str | Path,
    out_path: str | Path = "raw_scenes.json",
) -> Path:
    """Parse FDX and write a list of {number, heading, content} to JSON."""
    out = Path(out_path)
    scenes = parse_fdx_to_raw_scenes(fdx_path)
    out.write_text(
        json.dumps(scenes, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out


def parse_fdx_scenes(path: str | Path) -> list[SceneDict]:
    """Same parse as raw scenes with keys aligned to per-scene ingest (`scene_number`, `text`)."""
    raw = parse_fdx_to_raw_scenes(path)
    return [
        {
            "scene_number": s["number"],
            "heading": s["heading"],
            "text": s["content"],
        }
        for s in raw
    ]


if __name__ == "__main__":
    fdx = sys.argv[1] if len(sys.argv) > 1 else "sample.fdx"
    written = write_raw_scenes_json(fdx)
    data: list[dict[str, Any]] = json.loads(written.read_text(encoding="utf-8"))
    print(f"Wrote {len(data)} scene(s) to {written.resolve()}")
