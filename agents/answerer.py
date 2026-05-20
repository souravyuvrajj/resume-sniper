"""
Answerer agent: reads job application form questions and answers them
using the resume, project files, and the company research profile.

Factual questions ("Do you have experience with X?") are answered
strictly from what is in the resume. Behavioral questions ("Describe a
challenge...") use STAR narrative and may include invented detail
(dates, names, specifics) while staying anchored to real experience.
"""

import json
import re
import time
from pathlib import Path
from typing import Optional

# Keywords that signal a behavioral question needing a narrative answer
_BEHAVIORAL = [
    "describe", "tell me about", "share an example", "give an example",
    "walk me through", "when you", "have you ever", "challenge", "difficult",
    "overcome", "situation", "how did you", "what was", "can you share",
    "please share", "unexpected", "time when", "talk about",
]

_SYSTEM = (
    "You answer job application form questions on behalf of the candidate, "
    "a senior backend engineer with six years of production experience. "
    "You write in his voice: direct, specific, and human. "
    "Every answer reads like a person typed it into a form, not an AI."
)

_PROMPT = """Answer the job application questions below for the candidate.

COMPANY CONTEXT — let this shape the vocabulary and emphasis of every answer:
Company    : {company_name}
Role       : {role_title}
They value : {tech_values}
Use these words verbatim where natural: {key_vocabulary}
Narrative fit: {candidate_story}

CANDIDATE'S RESUME AND PROJECT EXPERIENCE:
{master_content}

QUESTIONS:
{questions_block}

WRITING RULES — follow every one, no exceptions:

Voice and tone
- Write in first person. Contractions are fine but do not overuse them. Mix "I've" with "I have", "didn't" with "did not".
- Sound like a smart engineer who learned English as a second language. Slightly formal in places, direct, no filler.
- Sentence structures should be simple and clear. Avoid complex nested clauses.
- Occasional slightly non-native phrasing is fine and preferred over overly polished prose.
  Examples: "I looked at this and thought..." / "So I decided to..." / "This was the main problem we were facing."
- Never open an answer with: Certainly, Great question, Absolutely, Of course, I am pleased,
  I am excited, I would like to, I am writing, Happy to, I wanted to.

Plain text rules
- No markdown. No asterisks, no bold, no bullet dashes, no headers, no backticks.
- Use a plain colon to introduce a list item label: "Token revocation: I fixed a TTL bug where..."
- For lists, write them as sentences or use a plain hyphen only if absolutely needed for readability.
- The output will be pasted directly into a plain text form field.

Grammar and punctuation
- No em dashes. Use a comma or a period instead.
- No negation structures. Not "my approach is not just X" — write "my approach is X".
  Not "this was not a simple problem" — write "this was a hard problem".
- No passive voice where active works. "I built" not "it was built by me".

Banned words — never use these:
delve, crucial, robust, comprehensive, nuanced, multifaceted, pivotal, showcase,
vibrant, fundamental, intricate, foster, underscore, leverage (as a verb),
innovative, transformative, synergy, seamlessly, streamline, game-changer,
cutting-edge, best-in-class, holistic, impactful, actionable.

Specificity rule
- Name the actual system, the actual error message, the actual metric.
- Vague: "I worked on a large distributed system." Good: "The settlement engine handled 50K transactions a day across 15 partners."

BEHAVIORAL QUESTIONS (describe a challenge, tell me about a time, how have you used, etc.)
- Use a loose STAR shape: what the situation was, what you did, what happened.
- Invent plausible narrative detail freely: exact dates ("a Tuesday in Q3 last year"),
  who was in the room, the specific error message, the Slack thread, the deadline pressure.
  This is normal storytelling — every good answer has specific texture.
- Do NOT invent skills, tools, or experience absent from the resume above.
  If the resume does not show React experience, do not claim it.
- Write the story the way you would tell it to a peer, not the way you would write a press release.

FACTUAL QUESTIONS (do you have experience with X, how many years, etc.)
- Answer directly in the first sentence. Yes or no, then the evidence from the resume.
- One concrete example, then stop. Do not pad.

{max_words_instruction}

Return ONLY valid JSON, no markdown wrapper:
{{
  "answers": [
    {{"question": "exact question text", "answer": "answer text"}},
    {{"question": "exact question text", "answer": "answer text"}}
  ]
}}"""


def parse_questions(text: str) -> list[str]:
    """Parse a questions file into a clean list of question strings.

    Handles:
      - "Type here..." placeholder lines (stripped)
      - Blank-line separators between questions
      - Numbered prefixes: "1.", "Q1.", "Q:"
      - Trailing whitespace
    """
    # Normalise line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Split into chunks on blank lines
    chunks = re.split(r"\n{2,}", text.strip())

    questions = []
    for chunk in chunks:
        lines = chunk.strip().splitlines()
        # Drop placeholder lines
        cleaned = [
            l for l in lines
            if l.strip().lower() not in ("type here...", "type here", "")
            and not re.match(r"^type here\.?\.?\.?$", l.strip(), re.IGNORECASE)
        ]
        if not cleaned:
            continue
        question = " ".join(l.strip() for l in cleaned)
        # Strip leading numbering: "1.", "2)", "Q1.", "Q:"
        question = re.sub(r"^(?:Q\d*[.:]?\s*|\d+[.)]\s*)", "", question).strip()
        if question:
            questions.append(question)

    return questions


def classify_question(q: str) -> str:
    """Return 'behavioral' or 'factual' for a question string."""
    lower = q.lower()
    if any(kw in lower for kw in _BEHAVIORAL):
        return "behavioral"
    return "factual"


def _build_questions_block(questions: list[str]) -> str:
    lines = []
    for i, q in enumerate(questions, 1):
        qtype = classify_question(q)
        lines.append(f"[{i}] ({qtype}) {q}")
    return "\n\n".join(lines)


def _call_claude(prompt: str, timeout: int = 120) -> str:
    """Call Claude via Anthropic SDK with retry logic."""
    from llm import call_raw
    
    for attempt in range(2):
        try:
            return call_raw(prompt, model="sonnet", timeout=timeout)
        except Exception as e:
            if attempt == 0:
                time.sleep(2)
            else:
                raise RuntimeError(f"Claude API call failed after 2 attempts: {str(e)}")


def _parse_answers(raw: str) -> list[dict]:
    raw = raw.strip()
    if "```" in raw:
        parts = raw.split("```")
        raw = parts[1]
        if raw.startswith("json"):
            raw = raw[4:]
    data = json.loads(raw.strip())
    return data["answers"]


def answer_questions(
    profile: dict,
    questions: list[str],
    master_content: str,
    max_words: Optional[int] = None,
) -> list[dict]:
    """Answer all questions in one Claude call.

    Returns list of {"question": str, "answer": str}.
    Falls back to a second attempt on JSON parse failure.
    """
    if not questions:
        return []

    max_words_instruction = (
        f"Word limit: keep each answer under {max_words} words."
        if max_words
        else "Length: write as much as the question genuinely needs. Do not pad."
    )

    prompt = _SYSTEM + "\n\n" + _PROMPT.format(
        company_name=profile.get("company_name", "the company"),
        role_title=profile.get("role_title", "the role"),
        tech_values=", ".join(profile.get("tech_values", [])[:4]),
        key_vocabulary=", ".join(profile.get("key_vocabulary", [])[:6]),
        candidate_story=profile.get("candidate_story", ""),
        master_content=master_content[:60_000],
        questions_block=_build_questions_block(questions),
        max_words_instruction=max_words_instruction,
    )

    raw = _call_claude(prompt)

    try:
        return _parse_answers(raw)
    except (json.JSONDecodeError, KeyError):
        # Retry once with an explicit nudge
        retry_prompt = (
            "The previous response was not valid JSON. "
            "Return ONLY the JSON object with an 'answers' array. No markdown, no prose.\n\n"
            + prompt
        )
        raw2 = _call_claude(retry_prompt)
        try:
            return _parse_answers(raw2)
        except (json.JSONDecodeError, KeyError) as e:
            raise RuntimeError(
                f"Answer agent returned invalid JSON after two attempts: {e}\n\nRaw:\n{raw2[:400]}"
            )


def print_answers(answers: list[dict]) -> None:
    """Print answers to stdout in a formatted block."""
    W = 70
    for i, item in enumerate(answers, 1):
        print(f"\n{'─' * W}")
        print(f"  Q{i}. {item['question']}")
        print(f"{'─' * W}")
        print()
        for line in item["answer"].splitlines():
            print(f"  {line}")
        print()


def write_answers(answers: list[dict], path: Path) -> None:
    """Write answers to a text file at path."""
    lines: list[str] = []
    for i, item in enumerate(answers, 1):
        lines.append(f"Q{i}. {item['question']}")
        lines.append("")
        lines.append(item["answer"])
        lines.append("")
        lines.append("─" * 70)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
