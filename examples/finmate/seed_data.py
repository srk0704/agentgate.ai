"""
FinMate — seed data utility.

`mock_db.py` already auto-creates and seeds on first instantiation, so this
script is a convenience entry point: run it once to materialize finmate.db
without launching the agent.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from examples.finmate.mock_db import FinMateDB  # noqa: E402


def main() -> None:
    db = FinMateDB()
    print(f"Seeded {db.path}")
    print(f"  Pending expenses: {len(db.get_pending_expenses())}")
    print(f"  Engineering Q1-2026 budget: {db.get_budget('engineering', 'Q1-2026')}")


if __name__ == "__main__":
    main()
