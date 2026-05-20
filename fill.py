#!/usr/bin/env python3
"""
Form-filling pipeline.

Reads a job description and a file of application questions, then
answers every question in the candidate's voice, tailored to the specific role.

Usage:
  python fill.py <job_url> --questions questions.txt
  python fill.py --jd-file jd.txt --questions questions.txt
  python fill.py --jd-file jd.txt --questions questions.txt --max-words 200
  python fill.py --jd-file jd.txt --questions questions.txt --verbose

Questions file format:
  One question per block. Blank lines separate questions.
  "Type here..." lines are stripped automatically.

  Example:
    Do you have experience with distributed systems?

    Describe an unexpected challenge you faced in a recent project.

    How have you used AI tools to improve your work?

Output:
  output/<company>_<role>_<date>.answers.txt  (also printed to stdout)
"""

import sys
import re
import json
import time
import argparse
from pathlib import Path
from datetime import datetime

from config import OUTPUT_DIR
from agents.scraper import scrape_job, load_jd_file
from agents.enrichment import enrich_company
from agents.research import research_company
from agents.alignment import load_master_content
from agents.answerer import parse_questions, answer_questions, print_answers, write_answers


def slugify(text: str, max_len: int = 30) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:max_len]


def main():
    parser = argparse.ArgumentParser(
        description="Answer job application form questions using Claude."
    )
    parser.add_argument(
        "url", nargs="?", default=None,
        help="Job posting URL (optional when --jd-file is given)",
    )
    parser.add_argument(
        "--jd-file", metavar="FILE",
        help="Path to a .txt file with the job description",
    )
    parser.add_argument(
        "--questions", metavar="FILE", required=True,
        help="Path to a .txt file with application questions (one per blank-separated block)",
    )
    parser.add_argument(
        "--max-words", metavar="N", type=int, default=None,
        help="Maximum words per answer (useful for forms with word limits)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print full intermediate output at each step",
    )
    args = parser.parse_args()

    if not args.url and not args.jd_file:
        parser.error("provide a job URL or --jd-file")

    # ── Load questions ────────────────────────────────────────────────────────
    questions_path = Path(args.questions)
    if not questions_path.exists():
        print(f"\n✗ Questions file not found: {questions_path}")
        sys.exit(1)

    questions = parse_questions(questions_path.read_text(encoding="utf-8"))
    if not questions:
        print(f"\n✗ No questions found in {questions_path}")
        sys.exit(1)

    print(f"\n  {len(questions)} question(s) loaded from {questions_path.name}")

    OUTPUT_DIR.mkdir(exist_ok=True)

    # ── Step 1: Get job description ───────────────────────────────────────────
    if args.jd_file:
        print(f"\n[1/3] Loading JD from {args.jd_file}...")
        jd_text = load_jd_file(args.jd_file)
    else:
        print(f"\n[1/3] Scraping {args.url}...")
        try:
            jd_text = scrape_job(args.url)
        except RuntimeError as e:
            print(f"\n✗ Scrape failed:\n  {e}")
            sys.exit(1)
    print(f"      {len(jd_text):,} characters loaded")

    # ── Step 1.5: Enrich with company pages ───────────────────────────────────
    print("\n[1.5/3] Scraping company pages for richer context...")
    t_enrich = time.time()
    company_context = enrich_company(jd_text)
    if company_context:
        print(f"      [enrichment: {time.time() - t_enrich:.1f}s, {len(company_context):,} chars]")
    else:
        print("      [enrichment: skipped — no URL found or pages blocked]")

    # ── Step 2: Research the company ──────────────────────────────────────────
    print("\n[2/3] Building company profile (Claude)...")
    t0 = time.time()
    try:
        profile = research_company(jd_text, company_context)
    except Exception as e:
        print(f"\n✗ Research agent failed: {e}")
        sys.exit(1)
    print(f"      [research: {time.time() - t0:.1f}s]")
    print(f"      {profile.get('company_name')} — {profile.get('role_title')}")

    if args.verbose:
        print(json.dumps(profile, indent=2))

    # ── Step 3: Answer questions ──────────────────────────────────────────────
    print(f"\n[3/3] Answering {len(questions)} question(s) (Claude)...")
    print("      Loading resume and project context...")
    master_content = load_master_content(profile)

    if args.max_words:
        print(f"      Word limit: {args.max_words} words per answer")

    t1 = time.time()
    try:
        answers = answer_questions(profile, questions, master_content, max_words=args.max_words)
    except Exception as e:
        print(f"\n✗ Answer agent failed: {e}")
        sys.exit(1)
    print(f"      [answers: {time.time() - t1:.1f}s]")

    # ── Output ────────────────────────────────────────────────────────────────
    company_slug = slugify(profile.get("company_name", "company"))
    role_slug = slugify(profile.get("role_title", "role"))
    date_str = datetime.now().strftime("%Y%m%d")
    output_name = f"{company_slug}_{role_slug}_{date_str}"

    answers_path = OUTPUT_DIR / f"{output_name}.answers.txt"
    write_answers(answers, answers_path)

    print_answers(answers)

    print(f"{'─' * 60}")
    print(f"  Answers : {answers_path}")
    print(f"{'─' * 60}\n")


if __name__ == "__main__":
    main()
