#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
SHARED_DIR = CURRENT_DIR / "shared"
if str(SHARED_DIR) not in sys.path:
    sys.path.append(str(SHARED_DIR))

from alert_policy import AlertPolicy


def main():
    parser = argparse.ArgumentParser(description="Acknowledge v2 repeating alert")
    parser.add_argument("--site", choices=["cultizm", "hyundai"], required=True)
    parser.add_argument("--dedup-key", required=True)
    args = parser.parse_args()

    policy = AlertPolicy(args.site)
    ok = policy.ack(args.dedup_key)
    if ok:
        print("acknowledged")
        return 0
    print("dedup key not found")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
