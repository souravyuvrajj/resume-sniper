#!/usr/bin/env python3
"""
Resume tailoring pipeline.

Usage:
  python apply.py <job_url>
  python apply.py --jd-file jd.txt            # JS-heavy pages: no URL needed
  python apply.py --jd-file jd.txt --contact alternate  # use alternate email/phone
  python apply.py <job_url> --jd-file jd.txt  # URL recorded but JD loaded from file
  python apply.py --jd-file jd.txt --verbose  # print full intermediate output

Output: output/<company>_<role>_<date>.pdf
        output/<company>_<role>_<date>.profile.json   ← company profile
        output/<company>_<role>_<date>.changes.json   ← rewritten resume content
"""

import os
import sys
import re
import json
import time
import argparse
from pathlib import Path
from datetime import datetime

from config import OUTPUT_DIR, TRACKER_PATH, EVAL_GATE
from agents.scraper import scrape_job, load_jd_file
from agents.enrichment import enrich_company
from agents.research import research_company
from agents.alignment import align_resume, load_master_content
from agents.compiler import patch_and_compile
from agents.answerer import parse_questions, answer_questions, print_answers, write_answers

from dotenv import load_dotenv
load_dotenv()


import yaml

RESUME_PATH = Path(__file__).parent / "resume" / "resume.tex"
_PROFILE_PATH = Path(__file__).parent / "config" / "profile.yml"


def _load_contacts() -> dict:
    data = yaml.safe_load(_PROFILE_PATH.read_text())
    return data.get("contacts", {})


def switch_contact(profile_name: str) -> None:
    """Switch resume contact details to specified profile."""
    contacts = _load_contacts()
    if profile_name not in contacts:
        print(f"✗ Unknown contact profile: {profile_name}. Use 'primary' or 'alternate'.")
        sys.exit(1)

    if not RESUME_PATH.exists():
        print(f"✗ Resume not found: {RESUME_PATH}")
        sys.exit(1)

    contact = contacts[profile_name]
    content = RESUME_PATH.read_text()

    content = re.sub(
        r'href\{mailto:[^}]+\}\{[^}]+\}',
        f'href{{mailto:{contact["email"]}}}{{{contact["email"]}}}',
        content
    )
    content = re.sub(
        r'Mobile: [^\n\\]+',
        f'Mobile: {contact["phone"]}',
        content
    )

    RESUME_PATH.write_text(content)
    print(f"  ✓ Switched to {profile_name} contact")
    print(f"    Email: {contact['email']}")
    print(f"    Phone: {contact['phone']}")


def slugify(text: str, max_len: int = 30) -> str:
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')[:max_len]


def dump(label: str, data: dict, verbose: bool):
    """Print a section header + full JSON when verbose."""
    if verbose:
        print(f"\n{'═' * 60}")
        print(f"  {label}")
        print('═' * 60)
        print(json.dumps(data, indent=2))


def _print_evaluation(evaluation):  # noqa: ANN001
    W = 70
    rec = evaluation.get("overall_recommendation", "").upper().replace("_", " ")
    score = evaluation.get("interview_likelihood", 0)
    sa = evaluation.get("skills_alignment", {})
    tl = evaluation.get("timeline", {})

    print(f"\n{'═' * W}")
    print(f"  EVALUATION — {rec}  ·  Interview Likelihood {score:.1f}/5.0")
    print(f"{'═' * W}")
    print(f"\n  Role Match       : {evaluation.get('role_match', '?')}/5  — {evaluation.get('role_match_reasoning', '')}")
    print(f"  Skills Alignment : {sa.get('score', '?')}/5")
    if sa.get("matched"):
        print(f"    Matched        : {', '.join(sa['matched'][:6])}")
    if sa.get("missing"):
        print(f"    Missing        : {', '.join(sa['missing'][:5])}")
    if sa.get("dealbreakers"):
        print(f"    Dealbreakers   : {', '.join(sa['dealbreakers'])}")
    print(f"  Seniority Fit    : {evaluation.get('seniority_fit', '?')}  — {evaluation.get('seniority_reasoning', '')}")
    print(f"  Timeline         : urgency={tl.get('urgency', '?')}, ~{tl.get('process_weeks', '?')} wk process")
    if evaluation.get("warnings"):
        print(f"\n  Warnings:")
        for w in evaluation["warnings"]:
            print(f"    • {w}")
    print(f"\n{'═' * W}\n")


def _print_profile(profile: dict):
    """Print the company profile from the research agent."""
    W = 70
    print(f"\n{'═' * W}")
    print(f"  COMPANY PROFILE — {profile.get('company_name','').upper()} · {profile.get('role_title','').upper()}")
    print(f"{'═' * W}")

    print(f"\n  Mission\n  {profile.get('team_mission', '')}\n")

    for field, label in [
        ("domain_problems",  "Domain Problems"),
        ("tech_values",      "Tech Values"),
        ("culture_signals",  "Culture Signals"),
        ("key_vocabulary",   "Key Vocabulary"),
    ]:
        items = profile.get(field, [])
        if items:
            print(f"  {label}")
            for item in items:
                print(f"    • {item}")
            print()

    story = profile.get("candidate_story", "")
    if story:
        print(f"  Candidate Story\n  {story}")

    print(f"\n{'═' * W}\n")


def main():
    parser = argparse.ArgumentParser(description="Tailor resume to a job posting using Claude.")
    parser.add_argument("url", nargs="?", default=None, help="Job posting URL (optional when --jd-file is given)")
    parser.add_argument("--jd-file", metavar="FILE", help="Path to a .txt file with the job description")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print full intermediate output at each step")
    parser.add_argument("--recompile", metavar="CHANGES_JSON", help="Skip Claude steps and recompile directly from a saved .changes.json file")
    parser.add_argument("--questions", metavar="FILE", help="Path to a .txt file with application form questions to answer alongside the resume")
    parser.add_argument("--max-words", metavar="N", type=int, default=None, help="Maximum words per answer (requires --questions)")
    parser.add_argument("--skip-eval", action="store_true", help="Skip role evaluation and go straight to resume tailoring")
    parser.add_argument("--eval-only", action="store_true", help="Run evaluation + generate report, then stop")
    parser.add_argument("--no-track", action="store_true", help="Do not append to applications.md tracker")
    parser.add_argument("--contact", metavar="PROFILE", choices=["primary", "alternate"], help="Switch resume contact: 'primary' or 'alternate'")
    parser.add_argument("--provider", choices=["claude", "kiro-cli", "codex"], default=None,
                        help="LLM provider to use (overrides LLM_PROVIDER env var)")
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
        import llm
        llm.PROVIDER = args.provider

    if not args.recompile and not args.url and not args.jd_file:
        parser.error("provide a job URL, a --jd-file, or --recompile <changes.json>")

    OUTPUT_DIR.mkdir(exist_ok=True)

    # ── Recompile shortcut ───────────────────────────────────────────────────
    if args.recompile:
        changes_path = Path(args.recompile)
        changes = json.loads(changes_path.read_text())
        output_name = changes_path.stem.replace(".changes", "")
        print(f"\n[recompile] Using {changes_path.name} → {output_name}.pdf")
        try:
            pdf_path = patch_and_compile(changes, output_name)
        except Exception as e:
            print(f"\n✗ Compilation failed: {e}")
            sys.exit(1)
        print(f"\n{'─' * 60}\n  ✓  {pdf_path}\n{'─' * 60}\n")
        return

    # ── Step 1: Get job description ──────────────────────────────────────────
    if args.jd_file:
        print(f"\n[1/5] Loading JD from {args.jd_file}...")
        jd_text = load_jd_file(args.jd_file)
    else:
        print(f"\n[1/5] Scraping {args.url}...")
        try:
            jd_text = scrape_job(args.url)
        except RuntimeError as e:
            print(f"\n✗ Scrape failed:\n  {e}")
            sys.exit(1)
    print(f"      {len(jd_text):,} characters loaded")

    if args.verbose:
        print(f"\n{'═' * 60}\n  JD TEXT\n{'═' * 60}")
        print(jd_text[:3000] + ("..." if len(jd_text) > 3000 else ""))

    # ── Step 1.5: Enrich with company website context ────────────────────────
    print("\n[1.5/5] Scraping company pages for richer context...")
    t_enrich = time.time()
    company_context = enrich_company(jd_text)
    if company_context:
        print(f"      [enrichment: {time.time() - t_enrich:.1f}s, {len(company_context):,} chars]")
    else:
        print(f"      [enrichment: skipped — no URL found or pages blocked]")

    # ── Step 2: Research the company ─────────────────────────────────────────
    print("\n[2/5] Building company profile (Claude)...")
    t0 = time.time()
    try:
        profile = research_company(jd_text, company_context, model=None)
    except Exception as e:
        print(f"\n✗ Research agent failed: {e}")
        sys.exit(1)
    print(f"      [research: {time.time() - t0:.1f}s]")

    _print_profile(profile)
    dump("COMPANY PROFILE (full)", profile, args.verbose)

    # ── Step 2.5: Evaluate role fit ──────────────────────────────────────────
    if not args.skip_eval:
        from agents.evaluator import load_profile, evaluate_role, generate_report, register_tracker
        try:
            user_profile = load_profile()
        except FileNotFoundError as e:
            print(f"\n  [eval] Skipped — {e}")
            user_profile = None

        if user_profile:
            print(f"\n[2.5/5] Evaluating role fit (Claude)...")
            t_eval = time.time()
            try:
                evaluation = evaluate_role(jd_text, profile, user_profile, eval_model=None)
            except Exception as e:
                print(f"      [eval] Failed: {e} — continuing without evaluation")
                evaluation = None

            if evaluation:
                print(f"      [eval: {time.time() - t_eval:.1f}s]")
                _print_evaluation(evaluation)

                date_str = datetime.now().strftime("%Y%m%d")
                _eval_output_name = f"{slugify(profile.get('company_name', 'company'))}_{slugify(profile.get('role_title', 'role'))}_{date_str}"

                eval_path = generate_report(profile, evaluation, _eval_output_name)
                print(f"      Report: {eval_path}")

                # Generate meaningful notes from evaluation data
                def generate_evaluation_notes(evaluation: dict) -> str:
                    """Extract actionable notes from evaluation results."""
                    notes_parts = []
                    
                    # Dealbreakers (most important)
                    dealbreakers = evaluation.get('skills_alignment', {}).get('dealbreakers', [])
                    if dealbreakers:
                        notes_parts.append(f"⚠️ Dealbreakers: {', '.join(dealbreakers)}")
                    
                    # Missing skills (high priority)
                    missing_skills = evaluation.get('skills_alignment', {}).get('missing', [])
                    if missing_skills and len(missing_skills) <= 3:
                        notes_parts.append(f"Missing: {', '.join(missing_skills[:3])}")
                    elif missing_skills:
                        notes_parts.append(f"Missing: {', '.join(missing_skills[:2])} +{len(missing_skills)-2}")
                    
                    # Seniority fit concerns
                    seniority_fit = evaluation.get('seniority_fit', '')
                    seniority_reason = evaluation.get('seniority_reasoning', '')
                    if seniority_fit not in ['exact_match', 'good_fit']:
                        if seniority_reason:
                            # Truncate long reasoning
                            short_reason = seniority_reason[:60] + "..." if len(seniority_reason) > 60 else seniority_reason
                            notes_parts.append(f"Level: {short_reason}")
                    
                    # Role match concerns
                    role_match = evaluation.get('role_match', 0)
                    role_reason = evaluation.get('role_match_reasoning', '')
                    if role_match < 4 and role_reason:
                        short_reason = role_reason[:60] + "..." if len(role_reason) > 60 else role_reason
                        notes_parts.append(f"Role: {short_reason}")
                    
                    # Timeline urgency
                    timeline = evaluation.get('timeline', {})
                    urgency = timeline.get('urgency', '')
                    if urgency in ['high', 'urgent']:
                        notes_parts.append(f"⏱️ {urgency.title()} timeline")
                    
                    # Interview likelihood flag
                    likelihood = evaluation.get('interview_likelihood', 0)
                    if likelihood >= 4.5:
                        notes_parts.append("🎯 Strong match")
                    elif likelihood < 3.5:
                        notes_parts.append("🤔 Weak match")
                    
                    # Top matched skills (for positioning)
                    matched = evaluation.get('skills_alignment', {}).get('matched', [])
                    if matched and len(matched) >= 3:
                        notes_parts.append(f"✅ Key matches: {', '.join(matched[:3])}")
                    
                    # Warnings (highest priority)
                    warnings = evaluation.get('warnings', [])
                    if warnings:
                        notes_parts.extend([f"⚠️ {w}" for w in warnings[:2]])
                    
                    # Join with separator, prioritize most important
                    if not notes_parts:
                        return ""
                    
                    # Reorder: warnings first, then dealbreakers, then other concerns
                    priority_order = ['⚠️', '🤔', '⏱️', '🎯']
                    sorted_notes = []
                    
                    # Priority items first
                    for priority in priority_order:
                        priority_items = [n for n in notes_parts if n.startswith(priority)]
                        sorted_notes.extend(priority_items)
                    
                    # Regular items last
                    regular_items = [n for n in notes_parts if not n.startswith(tuple(priority_order))]
                    sorted_notes.extend(regular_items)
                    
                    # Join with pipe separator, limit to reasonable length
                    full_notes = " | ".join(sorted_notes)
                    return full_notes[:200] + "..." if len(full_notes) > 200 else full_notes

                if not args.no_track:
                    meaningful_notes = generate_evaluation_notes(evaluation)
                    register_tracker(profile, evaluation, args.url or "", meaningful_notes, TRACKER_PATH)
                    print(f"      Tracked: {TRACKER_PATH}")
                    print(f"      Notes: {meaningful_notes}")

                from agents.skills_heatmap import update_heatmap
                update_heatmap(profile, evaluation)
                print(f"      Skills heatmap updated")

                score = evaluation.get("interview_likelihood", 0)
                if score < EVAL_GATE:
                    print(f"\n  ⚠  Interview likelihood {score:.1f}/5.0 below threshold ({EVAL_GATE})")
                    if evaluation.get("warnings"):
                        for w in evaluation["warnings"]:
                            print(f"     • {w}")

                if args.eval_only:
                    print("\n  [--eval-only] Done.")
                    return
    elif args.eval_only:
        print("\n  --eval-only has no effect with --skip-eval")

    # ── Step 2.7: Switch contact (if requested) ──────────────────────────────
    if args.contact:
        print(f"\n[2.7/5] Switching contact to {args.contact}...")
        switch_contact(args.contact)

    # ── Step 3: Align resume ─────────────────────────────────────────────────
    print("\n[3/5] Aligning resume to this role (Claude)...")
    master_content = load_master_content(profile)
    t1 = time.time()
    try:
        changes = align_resume(profile, master_content=master_content, model=None)
    except Exception as e:
        print(f"\n✗ Alignment agent failed: {e}")
        sys.exit(1)
    print(f"      [align: {time.time() - t1:.1f}s]")

    print(f"      Reasoning: {changes.get('reasoning', '')}")
    dump("RESUME CHANGES (full)", changes, args.verbose)

    # ── Step 4: Patch LaTeX + compile PDF ────────────────────────────────────
    print("\n[4/5] Patching LaTeX and compiling PDF...")
    company_slug = slugify(profile.get("company_name", "company"))
    role_slug = slugify(profile.get("role_title", "role"))
    date_str = datetime.now().strftime("%Y%m%d")
    output_name = f"{company_slug}_{role_slug}_{date_str}"

    # Always save intermediate JSONs alongside the PDF
    (OUTPUT_DIR / f"{output_name}.profile.json").write_text(json.dumps(profile, indent=2))
    (OUTPUT_DIR / f"{output_name}.changes.json").write_text(json.dumps(changes, indent=2))

    try:
        pdf_path = patch_and_compile(changes, output_name)
    except Exception as e:
        print(f"\n✗ Compilation failed: {e}")
        sys.exit(1)

    print(f"\n{'─' * 60}")
    print(f"  PDF     : {pdf_path}")
    print(f"  Profile : {OUTPUT_DIR / f'{output_name}.profile.json'}")
    print(f"  Changes : {OUTPUT_DIR / f'{output_name}.changes.json'}")
    print(f"{'─' * 60}\n")

    # ── Optional: Answer form questions ──────────────────────────────────────
    if args.questions:
        questions_path = Path(args.questions)
        if not questions_path.exists():
            print(f"\n✗ Questions file not found: {questions_path}")
            sys.exit(1)

        questions = parse_questions(questions_path.read_text(encoding="utf-8"))
        if not questions:
            print(f"\n✗ No questions found in {questions_path}")
            sys.exit(1)

        print(f"\n[5/5] Answering {len(questions)} form question(s) (Claude)...")
        if args.max_words:
            print(f"      Word limit: {args.max_words} words per answer")
        t2 = time.time()
        try:
            answers = answer_questions(profile, questions, master_content, max_words=args.max_words)
        except Exception as e:
            print(f"\n✗ Answer agent failed: {e}")
            sys.exit(1)
        print(f"      [answers: {time.time() - t2:.1f}s]")

        answers_path = OUTPUT_DIR / f"{output_name}.answers.txt"
        write_answers(answers, answers_path)
        print_answers(answers)

        print(f"{'─' * 60}")
        print(f"  Answers : {answers_path}")
        print(f"{'─' * 60}\n")


if __name__ == "__main__":
    main()
