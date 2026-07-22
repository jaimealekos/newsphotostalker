"""Run every enabled search once, then exit (useful for cron / manual testing).

Run:  python -m scripts.run_once            # all enabled searches
      python -m scripts.run_once <id>       # a single search by id
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import init_db  # noqa: E402
from app.ingest.runner import run_all_enabled, run_search  # noqa: E402


def main() -> None:
    init_db()
    if len(sys.argv) > 1:
        results = [run_search(int(sys.argv[1]))]
    else:
        results = run_all_enabled()
    for r in results:
        print(f"[{r.status}] search {r.search_id}: {r.message}")


if __name__ == "__main__":
    main()
