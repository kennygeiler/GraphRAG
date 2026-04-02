"""Shim: re-exports from domains.screenplay.rules for backward compatibility."""

from __future__ import annotations

from typing import Any

from domains.screenplay.rules import validate_business_logic


class ValidationException(Exception):
    """Raised when a deterministic extraction rule fails."""


def validate_no_duplicate_located_in_for_same_character(graph: dict[str, Any]) -> None:
    errors = validate_business_logic(graph)
    if errors:
        raise ValidationException("; ".join(errors))
