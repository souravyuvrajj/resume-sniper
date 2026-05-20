#!/usr/bin/env python3
"""
Tracker CLI for applications.md.

Usage:
  python tracker.py show                  # print the tracker table
  python tracker.py stats                 # summary counts by status
  python tracker.py dedup                 # deduplicate, show diff, rewrite file
  python tracker.py dedup --dry-run       # show what would be removed, no changes
"""

import argparse
import sys
from pathlib import Path

from config import TRACKER_PATH
from agents.evaluator import dedup_tracker, _parse_tracker
from agents.skills_heatmap import print_heatmap


def cmd_show(tracker_path: Path) -> None:
    if not tracker_path.exists():
        print("No tracker found at", tracker_path)
        return
    print(tracker_path.read_text(encoding="utf-8"))


def cmd_stats(tracker_path: Path) -> None:
    if not tracker_path.exists():
        print("No tracker found at", tracker_path)
        return
    _, rows = _parse_tracker(tracker_path)
    if not rows:
        print("Tracker is empty.")
        return

    from collections import Counter
    counts: Counter = Counter(r["status"].lower() for r in rows)
    scores = [float(r["likelihood"]) for r in rows if r["likelihood"].replace(".", "").isdigit()]

    print(f"\nTotal applications: {len(rows)}")
    print(f"{'─' * 30}")
    for status in ["offer", "interview", "screen", "applied", "evaluated", "rejected", "ghosted"]:
        if counts.get(status, 0):
            print(f"  {status:<12} {counts[status]}")
    other = {k: v for k, v in counts.items() if k not in ["offer", "interview", "screen", "applied", "evaluated", "rejected", "ghosted"]}
    for k, v in other.items():
        print(f"  {k:<12} {v}")
    if scores:
        print(f"\nAvg likelihood   : {sum(scores)/len(scores):.1f}")
        print(f"Highest          : {max(scores):.1f}")
        print(f"Lowest           : {min(scores):.1f}")
    print()


def cmd_dedup(tracker_path: Path, dry_run: bool) -> None:
    if not tracker_path.exists():
        print("No tracker found at", tracker_path)
        return

    report = dedup_tracker(tracker_path, dry_run=dry_run)

    if not report:
        print("No duplicates found.")
        return

    print(f"\n{'Dry run — ' if dry_run else ''}Found {len(report)} duplicate cluster(s):\n")
    for item in report:
        kept = item["kept"]
        sim = item["similarity"]
        print(f"  KEEP  [{kept['date']}] {kept['company']} — {kept['role']} (status: {kept['status']})")
        for dropped in item["dropped"]:
            print(f"  DROP  [{dropped['date']}] {dropped['company']} — {dropped['role']} (status: {dropped['status']}, sim: {sim:.0f}%)")
        print()

    if dry_run:
        print("(dry run — no changes written)")
    else:
        print(f"Tracker updated: {tracker_path}")


def cmd_skills(top_n: int, min_count: int) -> None:
    print_heatmap(top_n=top_n, min_count=min_count)


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage applications.md tracker")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("show", help="Print the tracker table")
    sub.add_parser("stats", help="Summary counts by status")

    dedup_p = sub.add_parser("dedup", help="Deduplicate tracker entries")
    dedup_p.add_argument("--dry-run", action="store_true", help="Show changes without writing")

    skills_p = sub.add_parser("skills", help="Show skill frequency heatmap across all JDs")
    skills_p.add_argument("--top", metavar="N", type=int, default=30, help="Show top N skills (default: 30)")
    skills_p.add_argument("--min", metavar="N", type=int, default=1, help="Minimum mention count (default: 1)")

    args = parser.parse_args()

    if not args.cmd:
        parser.print_help()
        sys.exit(1)

    if args.cmd == "show":
        cmd_show(TRACKER_PATH)
    elif args.cmd == "stats":
        cmd_stats(TRACKER_PATH)
    elif args.cmd == "dedup":
        cmd_dedup(TRACKER_PATH, dry_run=args.dry_run)
    elif args.cmd == "skills":
        cmd_skills(top_n=args.top, min_count=args.min)


if __name__ == "__main__":
    main()
