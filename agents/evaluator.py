"""
Role evaluation agent.

Scores a job against the user's career profile across 6 dimensions:
  role_match, skills_alignment, seniority_fit, interview_likelihood,
  timeline, overall_recommendation.

Also handles: report generation, tracker registration, tracker dedup.
Ported from career-ops (JS) evaluation framework.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from rapidfuzz import fuzz  # type: ignore[import-untyped]
from llm import call_messages

from config import PROFILE_CONFIG, OUTPUT_DIR


# ── Status rank for dedup (higher = more informative / advanced) ───────────────
STATUS_RANK = {
    "offer":      6,
    "interview":  5,
    "screen":     4,
    "applied":    3,
    "evaluated":  2,
    "rejected":   1,
    "ghosted":    1,
}


# ── Profile ────────────────────────────────────────────────────────────────────

def load_profile() -> dict:
    if not PROFILE_CONFIG.exists():
        raise FileNotFoundError(
            f"Career profile not found at {PROFILE_CONFIG}\n"
            f"Create it to enable role evaluation."
        )
    with open(PROFILE_CONFIG, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── Evaluation ─────────────────────────────────────────────────────────────────

def evaluate_role(
    jd_text: str,
    company_profile: dict,
    user_profile: dict,
    eval_model: Optional[str] = None,
) -> dict:
    """Single LLM call scoring all 6 dimensions. eval_model can be None (claude sonnet),
    a claude model name like 'haiku', or 'ollama:<model>' for local models."""
    prompt = _build_eval_prompt(jd_text, company_profile, user_profile)
    raw = _call_eval_llm(prompt, eval_model)
    return _parse_eval_json(raw)


_EVAL_SYSTEM = (
    "You are a career strategist evaluating whether a job matches a candidate's profile. "
    "Be direct and calibrated — a 5 is exceptional fit, a 3 is average, a 1 is poor or missing. "
    "Never inflate scores. Never invent information."
)

_EVAL_PROMPT = """CANDIDATE PROFILE:
{profile_yaml}

COMPANY RESEARCH:
{profile_json}

RAW JOB DESCRIPTION:
{jd_text}

Score this role across 6 dimensions. Return ONLY valid JSON, no markdown, no explanation:
{{
  "role_match": <1-5 int>,
  "role_match_reasoning": "...",

  "skills_alignment": {{
    "score": <1-5 int>,
    "matched": ["skill1", "skill2"],
    "missing": ["skill3"],
    "dealbreakers": []
  }},

  "seniority_fit": "too_junior" | "right_level" | "too_senior",
  "seniority_reasoning": "...",

  "interview_likelihood": <1.0-5.0 float, one decimal>,
  "interview_likelihood_reasoning": "...",

  "timeline": {{
    "urgency": "hot" | "normal" | "slow",
    "urgency_signals": "what in the JD signals this urgency",
    "process_weeks": <int>,
    "trajectory_fit": "how this role fits the candidate's career goals"
  }},

  "overall_recommendation": "apply" | "apply_with_caution" | "skip",
  "warnings": ["red flags: dealbreaker tech required, comp mismatch, onsite-only, visa req, etc."]
}}

SCORING GUIDANCE:
- role_match: compare role title, scope, responsibilities vs candidate.target_roles
- skills_alignment: score = matched_core / total_core scaled to 1-5; penalize dealbreakers
- seniority_fit: parse explicit YOE requirements from JD; compare vs candidate.identity.years_experience
- interview_likelihood: skills_alignment*0.4 + role_match*0.3 + seniority_fit_numeric*0.3, ±0.5 for intangibles
  (seniority_fit_numeric: right_level=5, too_junior=2, too_senior=3)
- timeline.urgency: "hot" if JD says ASAP/immediate/urgent; "slow" if large enterprise/govt
- timeline.process_weeks: estimate based on company size and role seniority
- warnings: flag comp below candidate min, required dealbreaker tech, onsite-only when candidate prefers remote"""


def _build_eval_prompt(jd_text: str, company_profile: dict, user_profile: dict) -> str:
    return _EVAL_PROMPT.format(
        profile_yaml=yaml.dump(user_profile, default_flow_style=False, allow_unicode=True),
        profile_json=json.dumps(company_profile, indent=2),
        jd_text=jd_text[:8000],
    )


def _call_eval_llm(prompt: str, eval_model: Optional[str]) -> str:
    return call_messages(
        _EVAL_SYSTEM, prompt, model=eval_model or "haiku", max_tokens=2048, timeout=120
    )


def _parse_eval_json(raw: str) -> dict:
    raw = raw.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Evaluator returned invalid JSON: {e}\n\nRaw:\n{raw[:500]}")

    # Normalise seniority_fit to our enum
    sf = data.get("seniority_fit", "right_level").lower()
    if "junior" in sf:
        data["seniority_fit"] = "too_junior"
    elif "senior" in sf and ("too" in sf or "over" in sf):
        data["seniority_fit"] = "too_senior"
    else:
        data["seniority_fit"] = "right_level"

    # Ensure skills_alignment subfields exist
    sa = data.setdefault("skills_alignment", {})
    sa.setdefault("score", 3)
    sa.setdefault("matched", [])
    sa.setdefault("missing", [])
    sa.setdefault("dealbreakers", [])

    # Ensure timeline subfields exist
    tl = data.setdefault("timeline", {})
    tl.setdefault("urgency", "normal")
    tl.setdefault("urgency_signals", "")
    tl.setdefault("process_weeks", 6)
    tl.setdefault("trajectory_fit", "")

    data.setdefault("warnings", [])
    return data


# ── Report generation ──────────────────────────────────────────────────────────

def generate_report(
    company_profile: dict,
    evaluation: dict,
    output_name: str,
) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    report_path = OUTPUT_DIR / f"{output_name}.eval.md"
    report_path.write_text(_format_report(company_profile, evaluation), encoding="utf-8")
    return report_path


def _format_report(company_profile: dict, evaluation: dict) -> str:
    company = company_profile.get("company_name", "Unknown")
    role = company_profile.get("role_title", "Unknown")
    rec = evaluation.get("overall_recommendation", "").upper().replace("_", " ")
    score = evaluation.get("interview_likelihood", 0)
    date = datetime.now().strftime("%Y-%m-%d")
    sa = evaluation.get("skills_alignment", {})
    tl = evaluation.get("timeline", {})
    warnings = evaluation.get("warnings", [])

    seniority_emoji = {
        "right_level": "✓",
        "too_junior":  "↑ stretch",
        "too_senior":  "↓ overqualified",
    }.get(evaluation.get("seniority_fit", ""), "?")

    lines = [
        f"# Role Evaluation: {company} — {role}",
        f"**Date:** {date} | **Recommendation:** {rec}",
        "",
        "---",
        "",
        "## Scores",
        "",
        "| Dimension | Score | Notes |",
        "|-----------|-------|-------|",
        f"| Role Match | {evaluation.get('role_match', '?')}/5 | {evaluation.get('role_match_reasoning', '')} |",
        f"| Skills Alignment | {sa.get('score', '?')}/5 | matched: {', '.join(sa.get('matched', [])[:5])} |",
        f"| Seniority Fit | {evaluation.get('seniority_fit', '?')} {seniority_emoji} | {evaluation.get('seniority_reasoning', '')} |",
        f"| Interview Likelihood | **{score:.1f}/5.0** | {evaluation.get('interview_likelihood_reasoning', '')} |",
        "",
        "## Skills Breakdown",
    ]

    if sa.get("matched"):
        lines.append(f"**Matched:** {', '.join(sa['matched'])}")
    if sa.get("missing"):
        lines.append(f"**Missing:** {', '.join(sa['missing'])}")
    if sa.get("dealbreakers"):
        lines.append(f"**Dealbreakers:** {', '.join(sa['dealbreakers'])}")

    lines += [
        "",
        "## Timeline",
        f"- **Urgency:** {tl.get('urgency', '?')} — {tl.get('urgency_signals', '')}",
        f"- **Estimated process:** ~{tl.get('process_weeks', '?')} weeks",
        f"- **Trajectory fit:** {tl.get('trajectory_fit', '')}",
        "",
        "## Warnings",
    ]

    if warnings:
        for w in warnings:
            lines.append(f"- {w}")
    else:
        lines.append("_none_")

    lines += [
        "",
        "---",
        "*Generated by resume-sniper evaluate*",
    ]

    return "\n".join(lines) + "\n"


# ── Tracker ────────────────────────────────────────────────────────────────────

def register_tracker(
    company_profile: dict,
    evaluation: dict,
    job_url: Optional[str],
    output_name: str,
    tracker_path: Path,
) -> None:
    company = company_profile.get("company_name", "Unknown")
    role = company_profile.get("role_title", "Unknown")
    sa = evaluation.get("skills_alignment", {})
    date = datetime.now().strftime("%Y-%m-%d")

    row = (
        f"| {date} "
        f"| {company} "
        f"| {role} "
        f"| {evaluation.get('role_match', '?')} "
        f"| {sa.get('score', '?')} "
        f"| {evaluation.get('seniority_fit', '?')} "
        f"| {evaluation.get('interview_likelihood', 0):.1f} "
        f"| applied "
        f"| {job_url or ''} "
        f"| {output_name} |"
    )

    if not tracker_path.exists():
        header = (
            "# Job Applications\n\n"
            "| Date | Company | Role | Match | Skills | Seniority | Likelihood | Status | URL | Notes |\n"
            "|------|---------|------|-------|--------|-----------|------------|--------|-----|-------|\n"
        )
        tracker_path.write_text(header + row + "\n", encoding="utf-8")
        return

    # Dedup check — skip if a fuzzy match already exists at same/higher status
    _, existing_rows = _parse_tracker(tracker_path)
    key_new = f"{company} {role}".lower()
    for existing in existing_rows:
        key_existing = f"{existing['company']} {existing['role']}".lower()
        if fuzz.token_sort_ratio(key_new, key_existing) >= 85:
            existing_rank = STATUS_RANK.get(existing["status"].strip().lower(), 0)
            if existing_rank >= STATUS_RANK.get("applied", 3):
                print(f"      [tracker] Duplicate detected ({existing['company']} — {existing['role']}), skipping.")
                return

    with open(tracker_path, "a", encoding="utf-8") as f:
        f.write(row + "\n")


# ── Dedup ──────────────────────────────────────────────────────────────────────

def dedup_tracker(tracker_path: Path, dry_run: bool = False) -> list:
    """Deduplicate tracker rows by fuzzy company+role matching.
    Returns list of {kept, dropped, similarity} dicts for reporting."""
    if not tracker_path.exists():
        return []

    header_lines, rows = _parse_tracker(tracker_path)
    if len(rows) <= 1:
        return []

    seen: set = set()
    clusters: list = []

    for i, row_i in enumerate(rows):
        if i in seen:
            continue
        cluster = [i]
        key_i = f"{row_i['company']} {row_i['role']}".lower()
        for j, row_j in enumerate(rows):
            if j <= i or j in seen:
                continue
            key_j = f"{row_j['company']} {row_j['role']}".lower()
            if fuzz.token_sort_ratio(key_i, key_j) >= 85:
                cluster.append(j)
                seen.add(j)
        seen.add(i)
        clusters.append(cluster)

    report = []
    rows_to_keep = []

    for cluster in clusters:
        if len(cluster) == 1:
            rows_to_keep.append(rows[cluster[0]])
            continue

        cluster_rows = sorted(
            [rows[idx] for idx in cluster],
            key=lambda r: (STATUS_RANK.get(r["status"].lower(), 0), r["date"]),
            reverse=True,
        )
        kept = cluster_rows[0]
        dropped = cluster_rows[1:]
        rows_to_keep.append(kept)
        sim = fuzz.token_sort_ratio(
            f"{kept['company']} {kept['role']}".lower(),
            f"{dropped[0]['company']} {dropped[0]['role']}".lower(),
        )
        report.append({"kept": kept, "dropped": dropped, "similarity": sim})

    if not dry_run and report:
        new_content = "\n".join(header_lines)
        if not new_content.endswith("\n"):
            new_content += "\n"
        for row in rows_to_keep:
            new_content += row["_raw"] + "\n"

        tmp = tracker_path.with_suffix(".tmp")
        tmp.write_text(new_content, encoding="utf-8")
        tmp.replace(tracker_path)

    return report


def _parse_tracker(tracker_path: Path) -> tuple:
    """Returns (header_lines, rows). Each row is a dict with keys:
    date, company, role, match, skills, seniority, likelihood, status, url, notes, _raw."""
    lines = tracker_path.read_text(encoding="utf-8").splitlines()
    header_lines = []
    rows = []
    in_table = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("| Date"):
            in_table = True
            header_lines.append(line)
            continue
        if in_table and stripped.startswith("|---") or stripped.startswith("| ---"):
            header_lines.append(line)
            continue
        if in_table and stripped.startswith("|") and stripped.count("|") >= 9:
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if len(cells) >= 9:
                rows.append({
                    "date":       cells[0],
                    "company":    cells[1],
                    "role":       cells[2],
                    "match":      cells[3],
                    "skills":     cells[4],
                    "seniority":  cells[5],
                    "likelihood": cells[6],
                    "status":     cells[7],
                    "url":        cells[8],
                    "notes":      cells[9] if len(cells) > 9 else "",
                    "_raw":       line,
                })
        elif in_table and stripped and not stripped.startswith("|"):
            in_table = False
            header_lines.append(line)
        elif not in_table:
            header_lines.append(line)

    return header_lines, rows
