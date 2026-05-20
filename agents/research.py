import json
from typing import Optional
from llm import call_messages

SYSTEM = "You are a senior talent strategist who specializes in understanding what companies actually need, not just what they write in job descriptions."

PROMPT = """Analyze this job posting and build a deep company profile for resume tailoring.

JOB POSTING:
{jd_text}
{company_context_section}
Do NOT just list requirements. Go deeper:
- What does this team actually build and why does it matter to the business?
- What are the real technical problems they face every day?
- What separates a "great hire" from a "barely qualified" hire here?
- What language and vocabulary do THEY use to describe their work? (These exact phrases must appear in the tailored resume.)
- What would make a candidate's story resonate with this team specifically?

Return ONLY valid JSON, no markdown, no explanation:
{{
  "company_name": "exact company name",
  "role_title": "exact role title",
  "team_mission": "1-2 sentences: what this team actually builds and why it matters to the business",
  "domain_problems": [
    "specific technical challenge this team faces day-to-day",
    "..."
  ],
  "tech_values": [
    "what technical quality they care about (e.g. correctness over speed, horizontal scale, low-latency)",
    "..."
  ],
  "culture_signals": [
    "how they work: async/remote/fast-moving/high-ownership/etc.",
    "..."
  ],
  "key_vocabulary": [
    "exact phrase from the JD that should appear verbatim in the resume",
    "..."
  ],
  "candidate_story": "2-3 sentences: what background and experience narrative would make someone exceptional here — not just qualified. What story connects most powerfully to this team's mission?"
}}"""


def research_company(jd_text: str, company_context: str = "", model: Optional[str] = None) -> dict:
    if company_context.strip():
        ctx_section = (
            "\nCOMPANY WEBSITE CONTEXT (from their actual site — use this to understand "
            "real values and culture beyond the JD):\n"
            + company_context[:8000]
            + "\n"
        )
    else:
        ctx_section = ""
    user = PROMPT.format(jd_text=jd_text[:12000], company_context_section=ctx_section)
    # Use Haiku for research - structured analysis task, 12x cheaper than Sonnet
    raw = call_messages(SYSTEM, user, model=model or "haiku", max_tokens=2048, timeout=90)
    return _parse_json(raw)


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Research agent returned invalid JSON: {e}\n\nRaw output:\n{raw[:500]}")
