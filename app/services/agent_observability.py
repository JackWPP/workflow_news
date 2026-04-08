"""
agent_observability.py — Agent 运行可观测性

提供 API 支持，让后台可以查看和回放 Agent 的运行 trace：
  - 每一步的思考过程
  - 工具选择和参数
  - 工具执行结果
  - Harness 拦截事件
  - 工作记忆的演变
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select

from app.models import AgentRun, AgentStep


def get_agent_run_trace(session, run_id: int) -> dict[str, Any] | None:
    """
    获取一次 Agent 运行的完整 trace，用于后台回放。

    Returns:
        完整的 trace dict，或 None 如果未找到
    """
    run = session.get(AgentRun, run_id)
    if run is None:
        return None

    steps = sorted(run.steps, key=lambda s: s.created_at)

    step_traces = []
    for step in steps:
        payload = step.input_payload or {}
        step_traces.append({
            "step_id": step.id,
            "step_index": step.round_index,
            "tool_name": step.stage_name,
            "thought": payload.get("thought", ""),
            "arguments": payload.get("arguments", {}),
            "result_summary": step.decision_summary or "",
            "status": step.status,
            "harness_blocked": step.fallback_triggered,
            "block_reason": step.error_message or "",
            "duration_seconds": round(step.duration_seconds, 2),
            "created_at": step.created_at.isoformat() if step.created_at else None,
        })

    memory_snapshot = run.memory_snapshot or (run.debug_payload or {}).get("memory", {})

    return {
        "run_id": run.id,
        "agent_type": run.agent_type if hasattr(run, "agent_type") else "daily_report",
        "status": run.status,
        "finished_reason": run.finished_reason if hasattr(run, "finished_reason") else None,
        "total_steps": len(steps),
        "total_tokens": run.total_tokens if hasattr(run, "total_tokens") else 0,
        "retrieval_run_id": run.retrieval_run_id,
        "started_at": run.created_at.isoformat() if run.created_at else None,
        "steps": step_traces,
        "memory_snapshot": memory_snapshot,
        "harness_violations": sum(1 for s in step_traces if s["harness_blocked"]),
        "debug_payload": run.debug_payload,
    }


def list_agent_runs(
    session,
    agent_type: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    列出最近的 Agent 运行记录摘要列表。
    """
    stmt = select(AgentRun).order_by(desc(AgentRun.id)).limit(limit)
    runs = list(session.scalars(stmt).all())

    result = []
    for run in runs:
        step_count = len(run.steps) if run.steps else 0
        blocked_count = sum(1 for s in (run.steps or []) if s.fallback_triggered)
        result.append({
            "run_id": run.id,
            "agent_type": run.agent_type if hasattr(run, "agent_type") else "daily_report",
            "status": run.status,
            "finished_reason": run.finished_reason if hasattr(run, "finished_reason") else None,
            "step_count": step_count,
            "harness_violations": blocked_count,
            "retrieval_run_id": run.retrieval_run_id,
            "created_at": run.created_at.isoformat() if run.created_at else None,
        })
    return result


def get_agent_step_detail(session, step_id: int) -> dict[str, Any] | None:
    """获取单个 AgentStep 的完整详情。"""
    step = session.get(AgentStep, step_id)
    if step is None:
        return None

    payload = step.input_payload or {}
    return {
        "step_id": step.id,
        "agent_run_id": step.agent_run_id,
        "tool_name": step.stage_name,
        "thought": payload.get("thought", ""),
        "arguments": payload.get("arguments", {}),
        "result_summary": step.decision_summary or "",
        "output_payload": step.output_payload,
        "status": step.status,
        "harness_blocked": step.fallback_triggered,
        "block_reason": step.error_message or "",
        "duration_seconds": round(step.duration_seconds, 2),
        "created_at": step.created_at.isoformat() if step.created_at else None,
    }
