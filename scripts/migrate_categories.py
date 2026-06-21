"""一次性迁移脚本：将旧三分类（高材制造/清洁能源/AI/其他）重映射为新三分类（塑料/橡胶/纤维）。

映射规则：
  高材制造 → 塑料
  清洁能源 → 塑料（回收政策 + 功能薄膜均归塑料）
  AI       → AI（不变，AI 日报独立保留）
  其他     → 塑料（默认桶）

涉及两张表：
  article_pool.category              —— String 列，直接 UPDATE
  report_items.decision_trace['category'] —— JSON 列，读出→改值→写回

用法：
  python -m scripts.migrate_categories --dry-run   # 预览，不写入
  python -m scripts.migrate_categories              # 执行迁移
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter

from sqlalchemy import bindparam, text

from app.database import session_scope

logger = logging.getLogger("migrate_categories")

MAPPING: dict[str, str] = {
    "高材制造": "塑料",
    "清洁能源": "塑料",
    "其他": "塑料",
    # AI 不变，不在映射表中
}


def _remap(old: str | None) -> str | None:
    if old is None:
        return None
    return MAPPING.get(old, old)


def migrate_article_pool(session, dry_run: bool) -> Counter[str]:
    """用原生 SQL 只查 category 列，规避 ORM 模型与实际 DB 的列漂移。"""
    counts: Counter[str] = Counter()
    rows = session.execute(
        text("SELECT id, category FROM article_pool WHERE category IN :cats").bindparams(
            bindparam("cats", expanding=True)
        ),
        {"cats": list(MAPPING.keys())},
    ).all()
    for row in rows:
        old = row.category
        new = _remap(old)
        counts[f"{old} -> {new}"] += 1
        if not dry_run:
            session.execute(
                text("UPDATE article_pool SET category = :new WHERE id = :id"),
                {"new": new, "id": row.id},
            )
    return counts


def migrate_report_items(session, dry_run: bool) -> Counter[str]:
    """读出 decision_trace JSON，改 category 值后写回。"""
    counts: Counter[str] = Counter()
    rows = session.execute(
        text("SELECT id, decision_trace FROM report_items WHERE decision_trace IS NOT NULL"),
    ).all()
    for row in rows:
        raw = row.decision_trace
        if raw is None:
            continue
        trace = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(trace, dict):
            continue
        old = trace.get("category")
        if old is None or old not in MAPPING:
            continue
        new = _remap(old)
        counts[f"{old} -> {new}"] += 1
        if not dry_run:
            trace["category"] = new
            session.execute(
                text("UPDATE report_items SET decision_trace = :dt WHERE id = :id"),
                {"dt": json.dumps(trace, ensure_ascii=False), "id": row.id},
            )
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移旧三分类到新三分类")
    parser.add_argument("--dry-run", action="store_true", help="只预览不写入")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    mode = "DRY-RUN" if args.dry_run else "APPLY"
    logger.info("=== 分类迁移 (%s) ===", mode)

    with session_scope() as session:
        pool_counts = migrate_article_pool(session, args.dry_run)
        item_counts = migrate_report_items(session, args.dry_run)

    logger.info("article_pool.category 更新：")
    if pool_counts:
        for label, n in pool_counts.most_common():
            logger.info("  %s: %d 条", label, n)
    else:
        logger.info("  无需更新")

    logger.info("report_items.decision_trace['category'] 更新：")
    if item_counts:
        for label, n in item_counts.most_common():
            logger.info("  %s: %d 条", label, n)
    else:
        logger.info("  无需更新")

    total = sum(pool_counts.values()) + sum(item_counts.values())
    logger.info("总计 %d 条记录%s。", total, "（未写入）" if args.dry_run else "已更新")
    return 0


if __name__ == "__main__":
    sys.exit(main())
