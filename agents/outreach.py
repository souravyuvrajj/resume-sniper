"""
Outreach agent: verify a hiring manager's email via SMTP pattern matching,
then draft a personalized cold email from the company research profile.

No external API or account needed. Pass the hiring manager's name via CLI:

    python apply.py --jd-file jd.txt --hiring-manager "Jane Smith"

Without --hiring-manager the email is still drafted — To: left blank to fill in manually.
"""

import json
import smtplib
import time
from typing import Optional

import dns.resolver

_SYSTEM = (
    "You are a sharp, concise writer who drafts cold emails for senior engineers "
    "reaching out to hiring managers. You write emails that feel human, specific, "
    "and direct — never like a template."
)

_PROMPT = """Draft a cold email from the candidate to {recipient_desc} at {company_name} about the {role_title} role.

COMPANY RESEARCH (use this — it makes the email specific, not generic):
- Team mission: {team_mission}
- Tech values they care about: {tech_values}
- Their exact vocabulary: {key_vocabulary}
- Why the candidate fits: {candidate_story}

RECIPIENT CONTEXT — adjust tone accordingly:
{recipient_context}

RULES:
1. 3-4 sentences max. Shorter is better.
2. Open with ONE specific observation about the team/problem — drawn from the research. Not generic praise.
3. Connect the candidate's background to their specific problem using their vocabulary.
4. End with a concrete ask: a 20-minute call to see if there's a fit.
5. Never use: "I hope this finds you well", "I came across your posting", "I am very excited",
   "I would love to", "I am writing to express", "I wanted to reach out".
6. Sign off with the candidate's first name
7. Subject line: specific and non-salesy. Reference the role and one concrete signal from the research.

Return ONLY valid JSON, no markdown wrapper:
{{
  "subject": "...",
  "body": "..."
}}"""


# ── Email pattern generation ─────────────────────────────────────────────────

def _email_patterns(first: str, last: str, domain: str) -> list[str]:
    f, l = first.lower(), last.lower()
    return [
        f"{f}.{l}@{domain}",
        f"{f}{l}@{domain}",
        f"{f[0]}{l}@{domain}",
        f"{f}@{domain}",
        f"{f[0]}.{l}@{domain}",
    ]


# ── SMTP verification ────────────────────────────────────────────────────────

def _smtp_verify(email: str) -> bool:
    """Return True if the MX server accepts RCPT TO for this address.

    Many servers use catch-all or greylisting, so False doesn't mean the address
    doesn't exist — but True is a strong signal that it does.
    """
    domain = email.split("@")[1]
    try:
        records = dns.resolver.resolve(domain, "MX")
        mx = str(sorted(records, key=lambda r: r.preference)[0].exchange).rstrip(".")
        with smtplib.SMTP(timeout=8) as smtp:
            smtp.connect(mx, 25)
            smtp.ehlo("outreach.local")
            smtp.mail("check@outreach.local")
            code, _ = smtp.rcpt(email)
        return code == 250
    except Exception:
        return False


def find_email(name: str, domain: str) -> Optional[str]:
    """Try common email patterns for name@domain, return first that SMTP-verifies."""
    parts = name.strip().split()
    if len(parts) < 2:
        return None
    first, last = parts[0], parts[-1]
    for pattern in _email_patterns(first, last, domain):
        if _smtp_verify(pattern):
            return pattern
    return None


# ── Company domain lookup ────────────────────────────────────────────────────

def _get_domain(company_name: str) -> Optional[str]:
    """Ask Claude haiku for the company's email domain (e.g. 'equifax.com')."""
    from llm import call_raw
    
    prompt = (
        "Return only the email domain (e.g. equifax.com) for this company. "
        "No explanation, no https://, just the domain.\n\n"
        f"Company: {company_name}"
    )
    try:
        domain = call_raw(prompt, model="haiku", timeout=20).strip().lower().lstrip("@").split("/")[0]
        return domain if "." in domain else None
    except Exception:
        return None


# ── Recipient context for email tone ─────────────────────────────────────────

def _recipient_context(name: Optional[str], email: Optional[str]) -> tuple[str, str]:
    """Return (recipient_desc, tone_instructions) for the Claude prompt."""
    if not name:
        return (
            "the hiring manager",
            "Recipient unknown — open with 'Hi [Name],' (user will fill in).\n"
            "Write for an engineering manager: focus on technical fit and specific problems.",
        )
    first = name.split()[0]
    desc = first if not email else f"{first} <{email}>"
    context = (
        f"Address {first} directly.\n"
        "They may be an engineering manager, recruiter, or founder — write for all three:\n"
        "Lead with a concrete technical signal, connect it to their mission, keep it human."
    )
    return desc, context


# ── Cold email drafting ──────────────────────────────────────────────────────

def _draft_cold_email(profile: dict, name: Optional[str], email: Optional[str]) -> dict:
    recipient_desc, recipient_context = _recipient_context(name, email)

    prompt = _SYSTEM + "\n\n" + _PROMPT.format(
        company_name=profile.get("company_name", "the company"),
        role_title=profile.get("role_title", "the role"),
        team_mission=profile.get("team_mission", ""),
        tech_values=", ".join(profile.get("tech_values", [])[:3]),
        key_vocabulary=", ".join(profile.get("key_vocabulary", [])[:5]),
        candidate_story=profile.get("candidate_story", ""),
        recipient_desc=recipient_desc,
        recipient_context=recipient_context,
    )

    from llm import call_raw
    
    for attempt in range(2):
        try:
            raw = call_raw(prompt, model="haiku", timeout=60)
            break
        except Exception as e:
            if attempt == 0:
                time.sleep(2)
            else:
                raise RuntimeError(f"Claude email draft failed after 2 attempts: {str(e)}")
    if "```" in raw:
        parts = raw.split("```")
        raw = parts[1]
        if raw.startswith("json"):
            raw = raw[4:]

    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Email draft returned invalid JSON: {e}\n\nRaw:\n{raw[:300]}")


# ── Public entry point ───────────────────────────────────────────────────────

def run_outreach(profile: dict, hiring_manager_name: Optional[str] = None) -> dict:
    """Draft a cold email, optionally finding the hiring manager's email via SMTP.

    Args:
        profile:              Company research dict from research_company().
        hiring_manager_name:  Full name from --hiring-manager CLI arg (optional).
                              If provided, SMTP pattern verification is attempted.

    Returns:
        {"name": str|None, "email": str|None, "subject": str, "body": str}
    """
    email = None

    if hiring_manager_name:
        domain = _get_domain(profile.get("company_name", ""))
        if domain:
            print(f"      [SMTP] trying patterns for {hiring_manager_name} @ {domain}...")
            email = find_email(hiring_manager_name, domain)
            if email:
                print(f"      [SMTP] verified: {email}")
            else:
                print(f"      [SMTP] no pattern verified — To: left blank")
        else:
            print("      [SMTP] could not determine company domain — To: left blank")
    else:
        print("      [email] no --hiring-manager provided — drafting email, To: left blank")

    draft = _draft_cold_email(profile, hiring_manager_name, email)

    return {
        "name": hiring_manager_name,
        "email": email,
        "subject": draft.get("subject", ""),
        "body": draft.get("body", ""),
    }
