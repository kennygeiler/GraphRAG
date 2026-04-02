"""Token and cost tracking for Anthropic models (no OpenAI callback dependency)."""

from __future__ import annotations

from typing import Any

# USD per 1 million tokens (input / output). Update when pricing changes.
_ANTHROPIC_PRICING: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6":      (3.00, 15.00),
    "claude-3-5-sonnet":      (3.00, 15.00),
    "claude-3-haiku-20240307": (0.25,  1.25),
    "claude-3-5-haiku":       (0.80,  4.00),
}

_DEFAULT_INPUT = 3.00
_DEFAULT_OUTPUT = 15.00


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    per_in, per_out = _ANTHROPIC_PRICING.get(model, (_DEFAULT_INPUT, _DEFAULT_OUTPUT))
    return (input_tokens * per_in + output_tokens * per_out) / 1_000_000


def accumulate_usage(
    state: dict[str, Any],
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> dict[str, Any]:
    """Return a state-update dict that merges tokens + cost."""
    added_tokens = input_tokens + output_tokens
    added_cost = estimate_cost(model, input_tokens, output_tokens)
    return {
        "total_tokens": int(state.get("total_tokens") or 0) + added_tokens,
        "total_cost": float(state.get("total_cost") or 0.0) + added_cost,
    }
