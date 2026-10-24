"""CLI for the dependency-free T6 offline contract baseline."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

FRAMEWORK_ROOT = Path(__file__).resolve().parents[1]
if str(FRAMEWORK_ROOT) not in sys.path:
    sys.path.insert(0, str(FRAMEWORK_ROOT))

from findoc.baseline.runner import BaselineError, run_t6_baseline


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--verification-id", default="T6-20260730-offline-contract-v3")
    args = parser.parse_args()
    try:
        manifest = run_t6_baseline(args.output, verification_id=args.verification_id)
    except BaselineError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "completed", "verification_id": manifest["verification_id"], "summary": manifest["summary"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
