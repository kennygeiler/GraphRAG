from __future__ import annotations

import re
from typing import Annotated, Literal, Union

from pydantic import AfterValidator, BaseModel, Field, ValidationError

_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")


def _validate_snake_case(v: str) -> str:
    if not _SNAKE_CASE.fullmatch(v):
        raise ValueError(
            "must be snake_case: start with a letter, then lowercase letters, digits, or underscores"
        )
    return v


SnakeCaseId = Annotated[str, AfterValidator(_validate_snake_case)]

EntityLabel = Literal["Character", "Location", "Prop", "Event"]

RelationshipType = Literal[
    "INTERACTS_WITH",
    "LOCATED_IN",
    "USES",
    "CONFLICTS_WITH",
    "POSSESSES",
]

EdgeType = RelationshipType


class Character(BaseModel):
    kind: Literal["Character"] = Field(
        default="Character",
        description="Discriminator: narrative character node.",
    )
    name: str = Field(description="Display name.")
    id: str = Field(description="Lowercase slug from the lexicon (snake_case).")


class Location(BaseModel):
    kind: Literal["Location"] = Field(default="Location")
    name: str = Field(description="Display name.")
    id: str = Field(description="Lowercase slug from the lexicon (snake_case).")


class Prop(BaseModel):
    kind: Literal["Prop"] = Field(default="Prop")
    name: str = Field(description="Display name.")
    id: str = Field(description="Lowercase slug from the lexicon (snake_case).")


class Event(BaseModel):
    kind: Literal["Event"] = Field(default="Event")
    name: str = Field(description="Display name.")
    id: str = Field(description="Lowercase slug from the lexicon (snake_case).")
    number: int = Field(description="Scene or beat number this event anchors.")
    description: str = Field(description="Short summary of what happens in this event.")


GraphNode = Annotated[
    Union[Character, Location, Prop, Event],
    Field(discriminator="kind"),
]


class Relationship(BaseModel):
    source_id: str = Field(description="Source node id (snake_case).")
    target_id: str = Field(description="Target node id (snake_case).")
    type: RelationshipType
    source_quote: str = Field(
        description=(
            "Exact snippet from the script (dialogue or action) that proves this relationship—verbatim, not paraphrased."
        ),
        min_length=1,
    )


class SceneGraph(BaseModel):
    nodes: list[GraphNode] = Field(
        default_factory=list,
        description="Character, Location, Prop, and Event nodes for this scene excerpt.",
    )
    relationships: list[Relationship] = Field(
        default_factory=list,
        description="Typed edges between nodes; each must include a textual proof quote.",
    )


def main() -> None:
    """Fail fast if Relationship can be built without source_quote (proof requirement)."""
    try:
        Relationship(
            source_id="alan",
            target_id="theater",
            type="LOCATED_IN",
        )
    except ValidationError:
        print("Proof requirement active: Relationship requires source_quote.", flush=True)
        raise


if __name__ == "__main__":
    main()
