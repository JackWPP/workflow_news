"""
Smoke test: trigger a real DailyReportAgent.run() and validate output.

Usage:
    python scripts/smoke_test_report.py

Exit codes:
    0  – all quality gates passed
    1  – one or more quality gates failed

Assumptions:
    - .env is already configured with real API keys
    - No FastAPI server needs to be running
    - A temporary SQLite database is created for isolation
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

# ── Bootstrap: point DATABASE_URL at a temp database BEFORE importing app modules ──
_DB_PATH = Path(tempfile.gettempdir()) / "workflow_news_smoke_test.db"
if _DB_PATH.exists():
    _DB_PATH.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["SHADOW_MODE"] = "false"

# Ensure project root is on sys.path so `app` package is importable
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import select, func

from app.bootstrap import init_db
from app.database import session_scope
from app.models import Report, ReportItem, RetrievalRun
from app.services.daily_report_agent import DailyReportAgent
from app.utils import now_local


# ── Quality gate definitions ──────────────────────────────────────────────────────


class QualityGate:
    """A single pass/fail check."""

    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description
        self.passed: bool = False
        self.detail: str = ""

    def evaluate(self, passed: bool, detail: str = "") -> None:
        self.passed = passed
        self.detail = detail


def _build_gates() -> list[QualityGate]:
    return [
        QualityGate("report_exists", "Report row exists in database"),
        QualityGate("status_not_failed", "Report status is not 'failed'"),
        QualityGate("min_items", "Report has >= 6 ReportItems"),
        QualityGate("min_sections", "Report covers >= 2 distinct sections"),
        QualityGate("markdown_content", "markdown_content is non-empty"),
        QualityGate("summary_nonempty", "summary is non-empty"),
    ]


# ── Core smoke test logic ─────────────────────────────────────────────────────────


async def _run_smoke_test() -> list[QualityGate]:
    """Initialise DB, run agent, validate output, return quality gates."""

    print("[smoke] Initialising database …")
    init_db()

    with session_scope() as session:
        from app.models import Source

        sources = session.scalars(select(Source)).all()
        for source in sources:
            source.rss_or_listing_url = None
            source.use_direct_source = False

    print("[smoke] Starting DailyReportAgent.run(shadow_mode=False, mode='publish') …")
    agent = DailyReportAgent()
    started_at = now_local()

    try:
        report = await agent.run(shadow_mode=False, mode="publish")
    except Exception as exc:
        print(f"[smoke] Agent raised exception: {exc}", file=sys.stderr)
        report = None

    elapsed = (now_local() - started_at).total_seconds()
    print(f"[smoke] Agent finished in {elapsed:.1f}s")

    gates = _build_gates()

    with session_scope() as session:
        if report is not None:
            db_report = session.get(Report, report.id)
        else:
            db_report = session.scalars(
                select(Report).order_by(Report.id.desc()).limit(1)
            ).first()

        # Gate: report_exists
        if db_report is None:
            gates[0].evaluate(False, "No report found in database")
            for g in gates[1:]:
                g.evaluate(False, "Skipped – no report")
            return gates

        gates[0].evaluate(True, f"Report id={db_report.id}")

        # Gate: status_not_failed
        gates[1].evaluate(
            db_report.status != "failed",
            f"status='{db_report.status}'",
        )

        items = session.scalars(
            select(ReportItem)
            .where(ReportItem.report_id == db_report.id)
            .order_by(ReportItem.rank)
        ).all()

        # Gate: min_items
        item_count = len(items)
        gates[2].evaluate(
            item_count >= 6,
            f"Found {item_count} items (need >= 6)",
        )

        # Gate: min_sections
        sections = {item.section for item in items}
        section_count = len(sections)
        gates[3].evaluate(
            section_count >= 2,
            f"Found {section_count} sections: {sorted(sections)}",
        )

        # Gate: markdown_content
        md = db_report.markdown_content or ""
        gates[4].evaluate(
            len(md) > 0,
            f"markdown_content length={len(md)}",
        )

        # Gate: summary_nonempty
        summary = db_report.summary or ""
        gates[5].evaluate(
            len(summary) > 0,
            f"summary length={len(summary)}",
        )

    return gates


def _print_report(gates: list[QualityGate]) -> bool:
    """Print a quality report table. Returns True if all gates passed."""

    print()
    print("=" * 72)
    print("  SMOKE TEST QUALITY REPORT")
    print("=" * 72)
    print(f"  {'Gate':<24} {'Result':<10} {'Detail'}")
    print("-" * 72)

    all_passed = True
    for gate in gates:
        status = "PASS" if gate.passed else "FAIL"
        if not gate.passed:
            all_passed = False
        print(f"  {gate.name:<24} {status:<10} {gate.detail}")

    print("=" * 72)
    if all_passed:
        print("  RESULT: ALL GATES PASSED")
    else:
        failed = [g.name for g in gates if not g.passed]
        print(f"  RESULT: FAILED – {failed}")
    print("=" * 72)
    print()

    return all_passed


# ── Cleanup ────────────────────────────────────────────────────────────────────────


def _cleanup() -> None:
    """Remove the temporary database file."""
    try:
        if _DB_PATH.exists():
            _DB_PATH.unlink()
            print(f"[smoke] Cleaned up {_DB_PATH}")
    except OSError:
        pass


# ── Entry point ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        gates = asyncio.run(_run_smoke_test())
        all_passed = _print_report(gates)
        sys.exit(0 if all_passed else 1)
    except KeyboardInterrupt:
        print("\n[smoke] Interrupted by user")
        sys.exit(1)
    finally:
        _cleanup()
