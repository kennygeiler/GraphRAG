"""
LLM auditor agents for semantic validation of screenplay graph extractions.

Three specialized agents run after deterministic checks pass:
1. **Quote Fidelity** — quotes exist but do they *support* the relationship?
2. **Completeness** — did the extractor miss significant interactions?
3. **Attribution** — are source_id/target_id correct for the described action?

Each auditor returns structured ``AuditFinding`` items via ``instructor``.
Optional **mapping_decision** + **patch_*** fields support gated auto-apply (see ``audit_pipeline``).
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

MappingDecision = Literal[
    "defer_human",
    "none",
    "propose_retype",
    "propose_remove",
    "propose_swap",
    "propose_add",
]

AllowedRelType = Literal[
    "INTERACTS_WITH",
    "LOCATED_IN",
    "USES",
    "CONFLICTS_WITH",
    "POSSESSES",
]


class AuditFinding(BaseModel):
    check: Literal["quote_fidelity", "completeness", "attribution"]
    severity: Literal["error", "warning"]
    relationship_index: int | None = Field(
        default=None, description="Index into the relationships list, if applicable."
    )
    detail: str = Field(description="Human-readable explanation of the finding.")
    suggestion: str | None = Field(
        default=None, description="Recommended fix, if any."
    )

    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Your estimated confidence [0,1] that the patch (if any) is correct.",
    )
    mapping_decision: MappingDecision = Field(
        default="defer_human",
        description=(
            "How to act: defer_human = needs human Verify only; "
            "propose_retype/remove/swap/add = structured patch (may auto-apply when gated)."
        ),
    )
    risk_flags: list[str] = Field(
        default_factory=list,
        description='Strings like "ambiguous_quote" when unsure; blocks auto-apply.',
    )

    patch_relationship_index: int | None = Field(
        default=None,
        description="For retype/remove/swap: index in relationships list.",
    )
    patch_new_type: AllowedRelType | None = Field(
        default=None, description="For propose_retype: new relationship type."
    )
    patch_source_id: str | None = Field(default=None, description="For propose_add: source node id.")
    patch_target_id: str | None = Field(default=None, description="For propose_add: target node id.")
    patch_relationship_type: AllowedRelType | None = Field(
        default=None, description="For propose_add: edge type."
    )
    patch_source_quote: str | None = Field(
        default=None, description="For propose_add: verbatim quote substring from scene text.",
    )


class AuditResult(BaseModel):
    findings: list[AuditFinding] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_QUOTE_FIDELITY_SYSTEM = """\
You are a Quote Fidelity Auditor for a narrative graph extraction pipeline.

You receive:
- The raw scene text from a screenplay.
- A list of extracted relationships, each with a source_quote, type, source_id, and target_id.

Your job: for each relationship, verify that the source_quote actually demonstrates \
the claimed relationship type.  A quote might exist in the text but NOT support the \
labelled type (e.g. "Alan sits next to Zev" tagged as CONFLICTS_WITH).

Rules:
- Use severity="error" only for **hard** failures: the source_quote is missing or not a substring of the scene text, or the quote is about different entities than source_id/target_id.
- If the quote is present but the **relationship type** is debatable (e.g. INTERACTS_WITH vs directional edge, mild POSSESSES vs wearing), use severity="warning" — humans resolve in Verify.
- If the quote clearly supports the type, do NOT report it.
- Only report issues; an empty findings list means everything is correct.

**Structured remediation (optional):** Set mapping_decision and patch fields only when you are \
≥0.6 confident the fix is correct (pipeline auto-applies gated patches at that threshold):
- **propose_retype**: wrong type but quote/entities ok — set patch_relationship_index, patch_new_type \
(one of INTERACTS_WITH, LOCATED_IN, USES, CONFLICTS_WITH, POSSESSES).
- **propose_remove**: relationship is bogus / unsupported — patch_relationship_index only.
Otherwise use mapping_decision="defer_human" and keep confidence below 0.6 for narrative-only notes.
"""

_COMPLETENESS_SYSTEM = """\
You are a Completeness Auditor for a narrative graph extraction pipeline.

You receive:
- The raw scene text from a screenplay.
- The extracted graph (nodes and relationships).

Your job: identify significant character interactions, conflicts, or prop uses that \
are clearly present in the scene text but MISSING from the extracted graph.

Allowed relationship **type** values ONLY (do not invent names like DENIES_ENTRY or BLOCKS):
INTERACTS_WITH, CONFLICTS_WITH, USES, LOCATED_IN, POSSESSES.
Map narrative ideas to these: denial of entry, arguments, or refusal → usually \
CONFLICTS_WITH or INTERACTS_WITH with a verbatim quote from the script.

Rules:
- Focus on relationships that meaningfully advance the narrative (fights, key prop \
  usage, significant dialogue exchanges).
- Ignore trivial background details that are not plot-relevant.
- In **detail** and **suggestion**, name real lexicon **source_id** / **target_id** \
  from the graph when possible, and cite the exact relationship type from the list above.
- Default mapping_decision="defer_human" — pipeline may only auto-add when you set \
**propose_add** with confidence ≥0.6, **patch_source_id**, **patch_target_id**, \
**patch_relationship_type**, and **patch_source_quote** as a verbatim substring of the scene text, \
and both ids exist as nodes in the graph.
- Only report genuinely missing items; an empty findings list means extraction is complete.
"""

_ATTRIBUTION_SYSTEM = """\
You are an Attribution Auditor for a narrative graph extraction pipeline.

You receive:
- The raw scene text from a screenplay.
- A list of extracted relationships with source_id, target_id, type, and source_quote.

Your job: for each relationship, verify that source_id and target_id are the CORRECT \
entities for the action described in the source_quote.

Common errors to catch:
- Swapped source/target (e.g. "Zev attacks Alan" but source_id=alan, target_id=zev).
- Wrong entity entirely (e.g. a quote about character A but attributed to character B).

Rules:
- Use severity="error" only when the quote clearly names or implies different actors than source_id/target_id (e.g. swapped roles, wrong character id).
- If attribution is ambiguous but plausible, or direction of action is arguable, use severity="warning".
- Only report issues; an empty findings list means attribution is correct.

**Structured remediation (optional)** when ≥0.6 confident:
- **propose_swap**: swap source_id and target_id for that edge — set patch_relationship_index.
- **propose_retype** / **propose_remove**: same as quote fidelity rules.
Otherwise defer_human.
"""


# ---------------------------------------------------------------------------
# Individual audit functions
# ---------------------------------------------------------------------------

def _build_audit_user_msg(graph_json: dict[str, Any], raw_text: str) -> str:
    """Compact JSON (Phase 1) — same cap, more graph fits without pretty-print whitespace."""
    payload = {"scene_text": raw_text, "extracted_graph": graph_json}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:120_000]


def audit_quote_fidelity(
    graph_json: dict[str, Any],
    raw_text: str,
    llm_fn: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the quote fidelity auditor. Returns (findings_dicts, usage_dict)."""
    result, usage = llm_fn(
        _build_audit_user_msg(graph_json, raw_text),
        _QUOTE_FIDELITY_SYSTEM,
        AuditResult,
    )
    return [f.model_dump(mode="json") for f in result.findings], usage


def audit_completeness(
    graph_json: dict[str, Any],
    raw_text: str,
    llm_fn: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the completeness auditor. Returns (findings_dicts, usage_dict)."""
    result, usage = llm_fn(
        _build_audit_user_msg(graph_json, raw_text),
        _COMPLETENESS_SYSTEM,
        AuditResult,
    )
    return [f.model_dump(mode="json") for f in result.findings], usage


def audit_attribution(
    graph_json: dict[str, Any],
    raw_text: str,
    llm_fn: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the attribution auditor. Returns (findings_dicts, usage_dict)."""
    result, usage = llm_fn(
        _build_audit_user_msg(graph_json, raw_text),
        _ATTRIBUTION_SYSTEM,
        AuditResult,
    )
    return [f.model_dump(mode="json") for f in result.findings], usage


# ---------------------------------------------------------------------------
# Composite runner
# ---------------------------------------------------------------------------

def run_audits(
    graph_json: dict[str, Any],
    raw_text: str,
    llm_fn: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Run all three auditors and combine findings.

    *llm_fn* signature: ``(user_text, system_prompt, response_model) -> (parsed_model, usage_dict)``

    Returns ``(combined_findings, combined_usage)`` where combined_usage
    aggregates token counts across all auditor calls.
    """
    all_findings: list[dict[str, Any]] = []
    total_input = 0
    total_output = 0
    model_name = ""

    for fn in (audit_quote_fidelity, audit_completeness, audit_attribution):
        findings, usage = fn(graph_json, raw_text, llm_fn)
        all_findings.extend(findings)
        total_input += usage.get("input_tokens", 0)
        total_output += usage.get("output_tokens", 0)
        model_name = usage.get("model", model_name)

    combined_usage = {
        "model": model_name,
        "input_tokens": total_input,
        "output_tokens": total_output,
    }
    return all_findings, combined_usage
