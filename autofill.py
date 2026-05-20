#!/usr/bin/env python3
"""
Playwright auto-fill for job application forms.

Usage:
  python autofill.py <job_url>
  python autofill.py <job_url> --jd-file jd.txt   # use cached JD, still navigate URL
  python autofill.py <job_url> --headless          # no visible browser window
  python autofill.py <job_url> --fast              # skip manual review for high-confidence fields
"""

import sys
import argparse
from pathlib import Path

from agents.scraper import scrape_job, load_jd_file
from agents.enrichment import enrich_company
from agents.research import research_company
from agents.alignment import load_master_content
from agents.evaluator import load_profile
from agents.playwright_fill import run_autofill  # type: ignore[import-untyped]


def main():
    parser = argparse.ArgumentParser(description="Auto-fill job application form using Playwright + Claude.")
    parser.add_argument("--url", help="Job application form URL")
    parser.add_argument("--jd-file", metavar="FILE", help="Use cached JD instead of re-scraping")
    parser.add_argument("--headless", action="store_true", help="Run browser headlessly (no window)")
    parser.add_argument("--fast", action="store_true", help="Auto-accept high-confidence answers without prompting")
    args = parser.parse_args()

    # Load user career profile
    try:
        user_profile = load_profile()
    except FileNotFoundError as e:
        print(f"\n✗ {e}")
        sys.exit(1)

    # Step 1: Get JD
    if args.jd_file:
        jd_path = Path(args.jd_file)
        if not jd_path.exists():
            print(f"\n✗ JD file not found: {jd_path}")
            sys.exit(1)
        print(f"\n[1/4] Loading JD from {args.jd_file}...")
        jd_text = load_jd_file(args.jd_file)
    else:
        print(f"\n[1/4] Scraping JD from {args.url}...")
        try:
            jd_text = scrape_job(args.url)
        except RuntimeError as e:
            print(f"\n✗ Scrape failed: {e}")
            sys.exit(1)
    print(f"      {len(jd_text):,} characters loaded")

    # Step 1.5: Enrich
    print("\n[1.5/4] Scraping company pages for context...")
    company_context = enrich_company(jd_text)

    # Step 2: Research
    print("\n[2/4] Building company profile (Claude)...")
    try:
        profile = research_company(jd_text, company_context)
    except Exception as e:
        print(f"\n✗ Research agent failed: {e}")
        sys.exit(1)
    print(f"      {profile.get('company_name', '?')} — {profile.get('role_title', '?')}")

    # Load resume context
    master_content = load_master_content(profile)

    # Step 3+4: Playwright fill
    run_autofill(
        job_url=args.url,
        company_profile=profile,
        master_content=master_content,
        user_profile=user_profile,
        headless=args.headless,
        fast=args.fast,
    )


if __name__ == "__main__":
    main()
