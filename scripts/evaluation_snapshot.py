from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.bootstrap import init_db
from app.database import session_scope
from app.services.repository import get_evaluation_summary


def main() -> None:
    init_db()
    with session_scope() as session:
        payload = get_evaluation_summary(session, days=7)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
