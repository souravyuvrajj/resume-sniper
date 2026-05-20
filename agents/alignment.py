import json
import re
from typing import Optional
import yaml
from config import TEMPLATE_TEX, PROJECTS_DIR, PROFILE_CONFIG
from llm import call_cached

SYSTEM = (
    "You are an expert resume writer who translates a candidate's real, verified skills "
    "into language that resonates with a specific company's mission, culture, and tech stack. "
    "profile.yml is the authoritative source of everything the candidate knows — "
    "you must use it aggressively to position them as the top candidate for every role."
)

# ── Stable block (cached across calls) ───────────────────────────────────────
# Contains the full skill inventory + resume context. Changes only when the user
# updates their profile or resume — not on every JD. Cache saves ~$0.10-0.20/call.
PROMPT_STABLE = """You are tailoring the candidate's resume for a specific role.

═══════════════════════════════════════════
CANDIDATE SKILL INVENTORY (profile.yml — SOURCE OF TRUTH):
═══════════════════════════════════════════
{profile_yaml}

This is everything the candidate knows — built through FAANG production work, personal projects,
and courses. Every skill listed here is real and defensible in an interview. You are
authorised to use ANY of these skills when tailoring the resume, even if they don't appear
explicitly in the current resume bullets. His resume is a conservative snapshot; profile.yml
is the complete picture.

═══════════════════════════════════════════
CANDIDATE'S RESUME BULLETS + PROJECT CONTEXT (starting point, not a ceiling):
═══════════════════════════════════════════
{master_content}

═══════════════════════════════════════════
RULES:
═══════════════════════════════════════════
1. SKILL INVENTORY IS THE CEILING, NOT THE RESUME BULLETS.
   - If a JD mentions a technology and it appears in profile.yml → use it in the resume, even
     if the current bullet uses a different tool. Swap freely: Bedrock → LangChain, FAISS → Pinecone,
     Q CLI → agent framework, TensorFlow → PyTorch. These are equivalent stacks; use the JD's vocabulary.
   - If a JD mentions a technology NOT in profile.yml → do not add it.
   - Personal projects and courses count. If profile.yml lists LoRA fine-tuning, write it.

2. USE key_vocabulary VERBATIM. These exact phrases are what the ATS and hiring manager scan for.
   Weave them naturally into bullets and the summary.

3. MAP TO domain_problems. Every bullet should answer: "does this help with THIS team's problems?"

4. RESPECT tech_values. Correctness culture → lead with correctness. Scale culture → lead with metrics.

5. REFLECT culture_signals in tone. Async/remote → mention RFCs, async docs. High-ownership → end-to-end.

6. SUMMARY RULES:
   - Lead with the candidate's experience. Never open with a description of the company's domain.
   - BAD: "Document intelligence is transforming..." / "Reliability is what powers..."
   - GOOD: "Applied AI engineer with 6 years shipping production LLM systems..."
   - 3-4 sentences. Dense with metrics, real systems, and role-specific vocabulary. No adjectives.
   - ADAPT identity to role type:
     * AI/ML/GenAI/Applied AI → "Applied AI engineer" — foreground LLMs, RAG, fine-tuning, eval harnesses.
     * Backend/Platform/Infra → "Senior Backend Engineer" — foreground distributed systems, scale, pipelines.
     * Hybrid → "Senior Engineer | Applied AI · Distributed Systems"
   - No first-person "I" except at most once. Prefer third-person constructions.
   - NEVER negation: no "not just Y", "not X but Y", "more than just Y". Direct affirmative only.

7. No negation in bullets either.

8. SURFACE NON-OBVIOUS CONNECTIONS aggressively:
   - Settlement engine (DynamoDB conditional writes, idempotency) → payment finality, financial correctness
   - Kafka pipeline (effectively-once, 10M+ events/day) → streaming infra, data pipelines, event-driven
   - Multi-tenant B2B platform (8M+ req/day, P99 90ms) → high-scale API, SaaS infrastructure
   - LLM code migration (dual-RAG, self-healing build loop, eval harness) → AI copilots, code transformation
   - Fine-tuning (LoRA/QLoRA/PEFT, training data pipelines) → model customization, domain adaptation
   - Evaluation harnesses (hallucination regression, grounding metrics, quality gates) → ML accountability
   - Support Bot RAG (hybrid retrieval, Recall@3, semantic cache, drift monitoring) → search infrastructure
   - NL-to-SQL filter agent (regression suite, 75→98% accuracy) → text-to-query, natural language interfaces
   - Action agent (multi-step workflows, DynamoDB session memory, constrained output) → agentic automation
   - 6 years across backend, frontend, data engineering, DevOps, and AI → full breadth, T-shaped depth

9. JSON escaping: backslashes must be double-escaped. Write \\\\textbf{{text}} NOT \\textbf{{text}}.
10. Keep EXACTLY: Amazon=3 bullets, Demandbase=6 bullets, Samsung=4 bullets.
11. SKILLS (STRICT STRUCTURE + QUALITY):

- You MUST preserve ALL categories exactly:
 Languages, Frameworks, Architecture, AI/ML, AI Systems,
  Data, Streaming / Messaging, Cloud & Infra, Observability, Testing

- DO NOT:
  • merge categories
  • rename categories
  • remove categories

- You MAY:
  • reorder items within each category
  • add relevant skills from profile.yml

- Backend roles:
  → Languages: Java MUST be first
  → Frameworks: Spring Boot MUST appear

- AI roles:
  → Languages: Python first
  → AI Systems prioritized

- Limit each category to 8–12 items
- Prefer widely recognized tools (Kafka, Spring Boot, PostgreSQL)
- Avoid niche/internal terms (semantic cache, drift monitor, etc.)"""

# ── Variable block (changes every call — company profile + output schema) ─────
PROMPT_VARIABLE = """═══════════════════════════════════════════
COMPANY PROFILE:
═══════════════════════════════════════════
{company_profile}

Return ONLY valid JSON, no markdown wrapper, no explanation:
{{
  "subtitle": "The line under his name. AI roles: 'Applied AI Engineer $|$ LLM Systems · Distributed Infrastructure'. Backend roles: 'Senior Backend Engineer $|$ Distributed Systems · Applied AI'. Hybrid: 'Senior Engineer $|$ Applied AI · LLM Systems · Distributed Systems'. Use LaTeX $|$ for the pipe separator.",
  "summary": "3-4 sentences. Lead with the candidate's background. Weave in key_vocabulary naturally. \\\\textbf{{}} for bold. Direct affirmative statements only.",
  "experience": {{
    "Amazon": ["Bullet 1", "Bullet 2", "Bullet 3"],
    "Demandbase": ["Bullet 1", "Bullet 2", "Bullet 3", "Bullet 4", "Bullet 5", "Bullet 6"],
    "Samsung": ["Bullet 1", "Bullet 2", "Bullet 3", "Bullet 4"]
  }},
  "skills": {{
    "Languages": "Max 8–12 items. Backend: Java first.",
    "Frameworks": "Max 8–12 items. Include Spring Boot.",
    "Architecture": "Max 8–12 items.",
    "AI/ML": "Max 8–12 items. Widely recognized tools only.",
    "AI Systems": "Max 8–12 items.",
    "Data": "Max 8–12 items.",
    "Streaming / Messaging": "Max 8–12 items.",
    "Cloud & Infra": "Max 8–12 items.",
    "Observability": "Max 8–12 items.",
    "Testing": "Max 8–12 items."
  }},
  "reasoning": "2-3 sentences: key translation decisions, non-obvious connections surfaced, vocabulary borrowed."
}}"""


def _score_project(content: str, signals: list[str]) -> int:
    text = content.lower()
    score = 0
    for signal in signals:
        # score each word in the phrase, not the full phrase
        words = [w for w in signal.lower().split() if len(w) > 4]  # skip short words
        score += sum(1 for w in words if w in text)
    return score

def load_master_content(company_profile: dict, top_n: int = 4, total_cap: int = 100_000) -> str:
    """
    Always include the full resume .tex.
    For projects, score each file against the company profile signals,
    pick the top_n most relevant, then distribute remaining budget
    proportionally by relevance score — no project gets silently cut off.
    """
    tex = TEMPLATE_TEX.read_text(encoding="utf-8")
    tex_chars = len(tex)

    signals = (
        company_profile.get("domain_problems", [])
        + company_profile.get("tech_values", [])
        + company_profile.get("key_vocabulary", [])
    )

    project_files = list(PROJECTS_DIR.glob("*.md"))
    scored = []
    for md_file in project_files:
        content = md_file.read_text(encoding="utf-8")
        score = _score_project(content, signals)
        scored.append((score, md_file.stem, content))

    scored.sort(key=lambda x: x[0], reverse=True)
    selected = scored[:top_n]

    print(f"      Projects selected ({top_n} most relevant):")
    for score, name, _ in selected:
        print(f"        [{score:2d} matches] {name}")

    # Budget: total_cap minus the resume tex, split across projects
    project_budget = total_cap - tex_chars
    if project_budget <= 0:
        print(f"WARNING: resume tex alone ({tex_chars:,} chars) exceeds cap — no project context included")
        return tex

    # Distribute budget proportionally by score
    # If all scores are 0, fall back to equal split
    total_score = sum(score for score, _, _ in selected)
    if total_score == 0:
        shares = [1 / len(selected)] * len(selected)
    else:
        shares = [score / total_score for score, _, _ in selected]

    projects_block_parts = []
    for (score, name, content), share in zip(selected, shares):
        alloc = int(project_budget * share)
        # Always give at least 500 chars even for low-scoring projects
        alloc = max(alloc, 500)
        truncated = content[:alloc]
        was_truncated = len(content) > alloc
        suffix = f" [truncated at {alloc:,} chars]" if was_truncated else ""
        print(f"        [{score:2d} matches] {name}: {len(content):,} chars → {alloc:,} allocated{suffix}")
        projects_block_parts.append(
            f"--- Project: {name} (relevance: {score}) ---\n{truncated}"
        )

    projects_block = "\n\n".join(projects_block_parts)
    master_content = f"{tex}\n\n=== MOST RELEVANT PROJECT DETAIL ===\n\n{projects_block}"

    final_size = len(master_content)
    print(f"      master_content final size: {final_size:,} chars")
    return master_content


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    if "```" in raw:
        parts = raw.split("```")
        raw = parts[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        result = json.loads(raw.strip())
        return _fix_latex_escapes(result)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Alignment agent returned invalid JSON: {e}\n\nRaw output:\n{raw[:500]}")


def _fix_latex_escapes(obj):
    if isinstance(obj, str):
        s = obj
        s = s.replace('\x09textbf{', r'\textbf{')   # \t (tab) → \textbf
        s = s.replace('\x09textit{', r'\textit{')   # \t (tab) → \textit
        s = s.replace('\\emph{',     r'\emph{')
        s = re.sub(r'(?<!\\)%', r'\\%', s)          # escape bare % (LaTeX comment char)
        return s
    elif isinstance(obj, dict):
        return {k: _fix_latex_escapes(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_fix_latex_escapes(i) for i in obj]
    return obj

def _load_profile_yaml() -> str:
    """Load profile.yml as a YAML string for inclusion in the prompt."""
    if PROFILE_CONFIG.exists():
        with open(PROFILE_CONFIG, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return yaml.dump(data, default_flow_style=False, allow_unicode=True)
    return "(profile.yml not found — skill inventory unavailable)"


def align_resume(
    company_profile: dict,
    master_content: Optional[str] = None,
    model: Optional[str] = None,
) -> dict:
    if master_content is None:
        master_content = load_master_content(company_profile)

    profile_yaml = _load_profile_yaml()

    # Block 1 — stable: skill inventory + resume/project context (cached across calls).
    # Block 2 — variable: company profile JSON + output schema (changes every call).
    raw = call_cached(
        system=SYSTEM,
        content_blocks=[
            {
                "text": PROMPT_STABLE.format(
                    profile_yaml=profile_yaml,
                    master_content=master_content,
                ),
                "cache_control": {"type": "ephemeral"},
            },
            {
                "text": PROMPT_VARIABLE.format(
                    company_profile=json.dumps(company_profile, indent=2)
                ),
            },
        ],
        model=model or "sonnet",
        max_tokens=8192,
        timeout=300,
    )
    return _parse_json(raw)