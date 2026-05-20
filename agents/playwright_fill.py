"""
Playwright auto-fill for job application forms.

Flow:
  1. Navigate to job URL (headful by default so you can watch)
  2. Extract all visible form fields via DOM inspection
  3. LLM generates proposed answers for each field
  4. Interactive per-field review (Enter=accept, type to override, s=skip)
  5. Execute fills — never submits
"""

import json
import subprocess
import time
from typing import Optional, TypedDict

from playwright.sync_api import Page, sync_playwright


class FormField(TypedDict):
    label: str
    field_type: str   # text | textarea | select | checkbox | radio | file
    selector: str
    options: list
    required: bool
    max_length: Optional[int]


# JS that walks the DOM and extracts form fields
_FIELD_EXTRACTOR_JS = """
() => {
  const fields = [];
  const inputs = document.querySelectorAll('input:not([type=hidden]), textarea, select');

  inputs.forEach((el, idx) => {
    // Derive a human-readable label
    let label = '';
    if (el.id) {
      const lbl = document.querySelector('label[for="' + el.id + '"]');
      if (lbl) label = lbl.innerText.trim();
    }
    if (!label && el.getAttribute('aria-label')) label = el.getAttribute('aria-label');
    if (!label && el.placeholder) label = el.placeholder;
    if (!label) {
      const parent = el.closest('div, fieldset, li');
      if (parent) {
        const lbl = parent.querySelector('label, legend, span[class*="label"], div[class*="label"]');
        if (lbl) label = lbl.innerText.trim();
      }
    }
    if (!label) label = el.name || el.id || 'field_' + idx;

    // Build a stable selector
    let selector = '';
    if (el.dataset && el.dataset.testid) selector = '[data-testid="' + el.dataset.testid + '"]';
    else if (el.id) selector = '#' + CSS.escape(el.id);
    else if (el.name) selector = el.tagName.toLowerCase() + '[name="' + el.name + '"]';
    else selector = el.tagName.toLowerCase() + ':nth-of-type(' + (idx + 1) + ')';

    const fieldType = el.type || el.tagName.toLowerCase();

    // Options for select/radio
    let options = [];
    if (el.tagName === 'SELECT') {
      options = Array.from(el.options).map(o => o.text.trim()).filter(Boolean);
    }
    if (el.type === 'radio') {
      const name = el.name;
      const radios = document.querySelectorAll('input[type=radio][name="' + name + '"]');
      options = Array.from(radios).map(r => {
        const lbl = document.querySelector('label[for="' + r.id + '"]');
        return lbl ? lbl.innerText.trim() : (r.value || '');
      }).filter(Boolean);
    }

    fields.push({
      label: label,
      field_type: fieldType === 'textarea' ? 'textarea' : fieldType,
      selector: selector,
      options: options,
      required: el.required || false,
      max_length: el.maxLength > 0 ? el.maxLength : null,
    });
  });

  return fields;
}
"""

SKIP_TYPES = {"hidden", "submit", "button", "reset", "image"}


def extract_form_fields(page: Page) -> list:
    raw = page.evaluate(_FIELD_EXTRACTOR_JS)
    return [f for f in raw if f.get("field_type") not in SKIP_TYPES]


def generate_fill_plan(
    fields: list,
    company_profile: dict,
    master_content: str,
    user_profile: dict,
) -> list:
    identity = user_profile.get("identity", {})
    fields_json = json.dumps(fields, indent=2)
    prompt = f"""You are filling out a job application form for {identity.get('name', 'the candidate')}.
Answer each field accurately using only information from the resume. Be concise for text fields.
For selects and radios, pick the closest matching option from the provided list.

CANDIDATE: {identity.get('name', '')}, {identity.get('current_title', '')}
ROLE: {company_profile.get('role_title', '')} at {company_profile.get('company_name', '')}

FORM FIELDS:
{fields_json}

RESUME CONTEXT (use for accurate answers):
{master_content[:20000]}

Return ONLY valid JSON:
{{
  "fill_plan": [
    {{
      "field_label": "First Name",
      "proposed_answer": "",
      "confidence": "high",
      "reasoning": "literal name"
    }}
  ]
}}

Confidence levels:
- high: factual, directly stated in resume (name, email, years exp, etc.)
- medium: inferred or paraphrased from resume
- low: uncertain — flag for user review
"""
    from llm import call_raw
    
    try:
        raw = call_raw(prompt, model="sonnet", timeout=120)
    except Exception as e:
        raise RuntimeError(f"Claude API call failed: {str(e)}")
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    data = json.loads(raw.strip())

    plan_items = data.get("fill_plan", [])
    # Match plan items back to fields by label
    output = []
    for _idx, field in enumerate(fields):
        match = next(
            (p for p in plan_items if p.get("field_label", "").lower() == field["label"].lower()),
            None,
        )
        output.append({
            "field": field,
            "proposed_answer": match["proposed_answer"] if match else "",
            "confidence": match.get("confidence", "low") if match else "low",
            "reasoning": match.get("reasoning", "") if match else "no match found",
        })
    return output


def present_fill_plan_for_review(fill_plan: list, fast: bool = False) -> list:
    """Interactive per-field review. Returns approved plan."""
    approved = []
    print(f"\n{'─' * 60}")
    print("  FORM FILL REVIEW")
    print(f"  Enter=accept · type new answer · 's'=skip")
    print(f"{'─' * 60}\n")

    for n, item in enumerate(fill_plan, start=1):
        field = item["field"]
        proposed = item["proposed_answer"]
        confidence = item["confidence"]
        label = field["label"]
        ftype = field["field_type"]
        req = " [required]" if field.get("required") else ""
        conf_tag = " [auto]" if confidence == "high" else f" [{confidence}]"

        if ftype == "file":
            print(f"  [{n}] {label} [file upload — skipped, attach PDF manually]")
            approved.append({**item, "proposed_answer": None, "skipped": True})
            continue

        if field.get("options"):
            print(f"  [{n}] {label} [{ftype}{req}]")
            print(f"         Options: {', '.join(field['options'][:8])}")
        else:
            print(f"  [{n}] {label} [{ftype}{req}]")

        print(f"         Proposed: {proposed!r}{conf_tag}")

        if fast and confidence == "high":
            print(f"         → auto-accepted")
            approved.append({**item, "skipped": False})
            continue

        try:
            user_input = input(f"         > ").strip()
        except EOFError:
            user_input = ""

        if user_input.lower() == "s":
            approved.append({**item, "proposed_answer": proposed, "skipped": True})
            print(f"         skipped")
        elif user_input:
            approved.append({**item, "proposed_answer": user_input, "skipped": False})
        else:
            approved.append({**item, "skipped": False})

        print()

    return approved


def execute_fill(page: Page, approved_plan: list) -> dict:
    filled = 0
    skipped = 0
    errors = []

    for item in approved_plan:
        if item.get("skipped") or item["proposed_answer"] is None:
            skipped += 1
            continue

        field = item["field"]
        answer = str(item["proposed_answer"])
        selector = field["selector"]
        ftype = field["field_type"]

        try:
            if ftype in ("text", "email", "tel", "number", "url", "textarea"):
                # Try primary selector; fall back to pierce for shadow DOM (Workday)
                try:
                    page.fill(selector, answer, timeout=3000)
                except Exception:
                    page.locator(f"pierce/{selector}").fill(answer)
            elif ftype == "select-one" or ftype == "select":
                try:
                    page.select_option(selector, label=answer, timeout=3000)
                except Exception:
                    page.select_option(selector, value=answer, timeout=3000)
            elif ftype == "checkbox":
                if answer.lower() in ("yes", "true", "1", "on"):
                    page.check(selector, timeout=3000)
            elif ftype == "radio":
                # Click the matching radio option
                page.locator(f"input[type=radio][value='{answer}']").first.click(timeout=3000)
            else:
                skipped += 1
                continue
            filled += 1
        except Exception as e:
            errors.append({"field": field["label"], "error": str(e)[:200]})
            skipped += 1

    return {"filled": filled, "skipped": skipped, "errors": errors}


def run_autofill(
    job_url: str,
    company_profile: dict,
    master_content: str,
    user_profile: dict,
    headless: bool = False,
    fast: bool = False,
) -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        print(f"\n[3/4] Navigating to {job_url}...")
        page.goto(job_url, wait_until="networkidle", timeout=30000)
        time.sleep(1)  # let SPA settle

        print("[3/4] Extracting form fields...")
        fields = extract_form_fields(page)
        print(f"      Found {len(fields)} form field(s)")

        # If no fields found, this is probably the job listing page, not the form.
        # Let the user navigate to the application form in the open browser, then continue.
        if not fields:
            print("\n  No form fields detected on this page.")
            print("  The browser is open — navigate to the application form yourself")
            print("  (click 'Apply', log in to the ATS, etc.), then come back here.\n")
            input("  Press Enter once you're on the form page to start filling... ")
            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(1)
            fields = extract_form_fields(page)
            print(f"      Found {len(fields)} form field(s) after navigation")

        if not fields:
            print("      Still no fields found — closing.")
            input("      Press Enter to close the browser...")
            browser.close()
            return

        print("[3/4] Generating fill plan (Claude)...")
        fill_plan = generate_fill_plan(fields, company_profile, master_content, user_profile)

        approved_plan = present_fill_plan_for_review(fill_plan, fast=fast)

        print("[4/4] Filling fields...")
        summary = execute_fill(page, approved_plan)
        print(f"\n{'─' * 60}")
        print(f"  Filled   : {summary['filled']}")
        print(f"  Skipped  : {summary['skipped']} (file uploads or user-skipped)")
        if summary["errors"]:
            print(f"  Errors   : {len(summary['errors'])}")
            for err in summary["errors"]:
                print(f"    • {err['field']}: {err['error']}")
        print(f"\n  ⚠  Do NOT submit — review all fields before submitting manually.")
        print(f"{'─' * 60}\n")

        input("  Press Enter to close the browser...")
        browser.close()
