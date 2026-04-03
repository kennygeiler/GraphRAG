"""Persist pipeline efficiency metrics as :PipelineRun nodes in Neo4j."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from neo4j import Driver


def ensure_pipeline_run_schema(session: Any) -> None:
    """Idempotent constraint for PipelineRun ids (Neo4j 5+)."""
    try:
        session.run(
            """
            CREATE CONSTRAINT pipeline_run_id_unique IF NOT EXISTS
            FOR (p:PipelineRun) REQUIRE p.id IS UNIQUE
            """
        )
    except Exception:
        pass


def save_pipeline_run(
    driver: Driver,
    *,
    scenes_extracted: int,
    total_scenes: int,
    corrections_count: int,
    warnings_count: int,
    telemetry_tokens: int,
    telemetry_cost_usd: float,
    agent_optimization_version: int,
    failed_scenes: int,
    llm_auditors_enabled: bool,
    fdx_filename: str = "",
) -> str:
    """Write one run record. Returns the new run id.

    ``fdx_filename`` is stored for the **Script Name** column: prefer the uploader’s original filename;
    fall back to the on-disk target (e.g. ``target_script.fdx``) when the run did not go through upload.
    """
    run_id = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    with driver.session() as session:
        ensure_pipeline_run_schema(session)
        session.run(
            """
            CREATE (p:PipelineRun {
                id: $id,
                ts: $ts,
                fdx_filename: $fdx_filename,
                scenes_extracted: toInteger($scenes_extracted),
                total_scenes: toInteger($total_scenes),
                corrections_count: toInteger($corrections_count),
                warnings_count: toInteger($warnings_count),
                telemetry_tokens: toInteger($telemetry_tokens),
                telemetry_cost_usd: $telemetry_cost_usd,
                agent_optimization_version: toInteger($agent_opt),
                failed_scenes: toInteger($failed_scenes),
                llm_auditors_enabled: $auditors
            })
            """,
            id=run_id,
            ts=ts,
            fdx_filename=str(fdx_filename or ""),
            scenes_extracted=scenes_extracted,
            total_scenes=total_scenes,
            corrections_count=corrections_count,
            warnings_count=warnings_count,
            telemetry_tokens=telemetry_tokens,
            telemetry_cost_usd=float(telemetry_cost_usd),
            agent_opt=agent_optimization_version,
            failed_scenes=failed_scenes,
            auditors=bool(llm_auditors_enabled),
        )
    return run_id


def list_pipeline_runs(driver: Driver, *, limit: int = 200) -> list[dict[str, Any]]:
    """Newest first."""
    with driver.session() as session:
        ensure_pipeline_run_schema(session)
        result = session.run(
            """
            MATCH (p:PipelineRun)
            RETURN p AS node
            ORDER BY p.ts DESC
            LIMIT $limit
            """,
            limit=int(limit),
        )
        rows: list[dict[str, Any]] = []
        for record in result:
            node = record["node"]
            rows.append({k: node[k] for k in node.keys()})
        return rows
