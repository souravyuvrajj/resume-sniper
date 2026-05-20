"""
Skills heatmap — tracks skills you're missing across JDs.

Data structure (skills_heatmap.json):
  { "rust": { "display": "Rust", "count": 3 }, ... }

Source: evaluation["skills_alignment"]["missing"] only.
Key_vocabulary is intentionally excluded — those are JD buzzwords, not skill gaps.
"""

import json
import re
from typing import Optional

from config import HEATMAP_PATH

# Strip LLM commentary like "Rust (Go/Python preferred instead)"
_PAREN_RE = re.compile(r'\s*\(.*?\)\s*$')


def _clean(skill: str) -> str:
    return _PAREN_RE.sub('', skill).strip()


def update_heatmap(
    company_profile: dict,
    evaluation: Optional[dict],
) -> None:
    if not evaluation:
        return

    heatmap = _load_raw()
    missing: list[str] = evaluation.get("skills_alignment", {}).get("missing", [])

    for raw in missing:
        skill = _clean(raw)
        if not skill:
            continue
        key = skill.lower()
        if key not in heatmap:
            heatmap[key] = {"display": skill, "count": 0}
        heatmap[key]["count"] += 1

    HEATMAP_PATH.write_text(json.dumps(heatmap, indent=2), encoding="utf-8")


def load_heatmap() -> dict:
    return _load_raw()


def print_heatmap(top_n: int = 30, min_count: int = 1) -> None:
    heatmap = _load_raw()
    if not heatmap:
        print("No skill data yet. Run apply.py on some job postings first.")
        return

    entries = sorted(heatmap.values(), key=lambda e: e["count"], reverse=True)
    entries = [e for e in entries if e["count"] >= min_count]

    if not entries:
        print(f"No skills with count >= {min_count}.")
        return

    W = max(len(e["display"]) for e in entries[:top_n]) + 2
    W = max(W, 30)

    print(f"\n{'Skill':<{W}} {'Count':>5}")
    print("─" * (W + 7))
    for entry in entries[:top_n]:
        print(f"{entry['display']:<{W}} {entry['count']:>5}")
    print()


def _load_raw() -> dict:
    if not HEATMAP_PATH.exists():
        return {}
    try:
        return json.loads(HEATMAP_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
