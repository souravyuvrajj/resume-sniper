# resume-sniper

An agentic AI pipeline that evaluates job fit, tailors your resume, and auto-fills
application forms — all orchestrated locally via Claude. Feed it a job URL or JD
file; get a scored evaluation report, a compiled PDF resume, and written form
answers back.

Built to explore multi-agent orchestration patterns: each stage is an independent
agent with a focused prompt, structured JSON output, and a clear contract with the
next stage. The pipeline composes them in sequence, with optional short-circuits
(`--eval-only`, `--skip-eval`) and a recompile path that replays the final stage
without re-invoking the LLM.

---

## Architecture: agent pipeline

```
Job URL / JD file
      │
      ▼
[scraper]          Crawl4AI — handles JS-heavy pages, extracts clean JD text
      │
      ▼
[enrichment]       Scrapes company website: engineering blog, values, culture pages
      │
      ▼
[research]         Claude builds a structured company profile (JSON) from JD + pages
      │                → company_name, domain_problems, tech_values, key_vocabulary
      ▼
[evaluator]        Claude scores role fit against your career profile (profile.yml)
      │                → Role Match, Skills Alignment, Seniority, Interview Likelihood
      ▼
[alignment]        Claude rewrites resume bullets + summary using the company profile
      │                → maps experience to domain problems, weaves key_vocabulary
      ▼
[compiler]         Patches LaTeX template, runs pdflatex → PDF
      │
      ▼ (optional)
[answerer]         Claude answers application form questions in your voice
```

Each agent is a single file in `agents/`. Agents communicate via structured JSON;
no shared mutable state. The orchestrator (`apply.py`) wires them together and
handles flags, short-circuits, and output routing.

---

## What it does

**`apply.py`** — full pipeline:
1. Scrapes the job posting URL (handles JS-heavy pages via Crawl4AI)
2. Scrapes the company website for richer context (engineering blog, values, culture pages)
3. Builds a company profile: mission, domain problems, tech values, key vocabulary
4. **Evaluates role fit** against your career profile — scores Role Match, Skills
   Alignment, Seniority, Interview Likelihood, Timeline; writes a markdown report;
   appends a row to `applications.md`; updates `skills_heatmap.json` with skill
   frequencies across all JDs (even with `--skip-eval`, vocab is tracked)
5. Rewrites your resume bullets and summary to speak directly to that role
6. Patches the LaTeX template and compiles a PDF
7. *(Optional)* Answers application form questions in your voice, tailored to the role

**`autofill.py`** — Playwright browser auto-fill:
Navigates to the job URL in a visible browser, detects form fields, generates
proposed answers via Claude, lets you review each one, then fills without submitting.

**`fill.py`** — lightweight variant:
Skips PDF generation. Only answers form questions from a saved JD + questions file.

**`tracker.py`** — tracker management:
View, deduplicate, get stats on `applications.md`, and inspect the skills heatmap.

---

## Prerequisites

| Dependency | Install |
|---|---|
| Python 3.11+ | [python.org](https://python.org) |
| Claude Code CLI | `npm install -g @anthropic-ai/claude-code` |
| pdflatex | `brew install basictex` (macOS) or any TeX Live distribution |

The `claude` CLI must be authenticated and available in your `PATH`.

---

## Installation

```bash
git clone https://github.com/your-username/resume-sniper.git
cd resume-sniper
pip install -r requirements.txt
playwright install chromium   # one-time: downloads headless browser for autofill
```

---

## Setup: career profile

Edit `config/profile.yml` before first use. It defines your target roles, tech
stack, and compensation so the evaluator can score every job against your actual
goals. It's `.gitignored` — never committed.

---

## Project structure

```
resume-sniper/
├── apply.py                          # Orchestrator: wires all agents in sequence
├── fill.py                           # Form answers only (no PDF)
├── autofill.py                       # Playwright browser auto-fill
├── tracker.py                        # Manage applications.md tracker
├── config.py                         # Path constants
├── llm.py                            # LLM provider abstraction (claude / ollama / codex)
├── requirements.txt
│
├── config/
│   └── profile.yml                   # Your career profile (gitignored, edit this)
│
├── agents/
│   ├── scraper.py                    # Crawl4AI job posting scraper
│   ├── enrichment.py                 # Company website scraper
│   ├── research.py                   # Build structured company profile from JD
│   ├── evaluator.py                  # Score role fit; write report + tracker row
│   ├── alignment.py                  # Rewrite resume bullets + summary for the role
│   ├── compiler.py                   # Patch LaTeX template + run pdflatex
│   ├── answerer.py                   # Answer application form questions
│   ├── skills_heatmap.py             # Track skill frequencies across JDs
│   └── playwright_fill.py            # DOM extraction + fill execution
│
├── resume/
│   └── *.tex                         # LaTeX resume template
│
├── projects/
│   └── *.md                          # Project write-ups (context for tailoring)
│
├── tests/
│   ├── test_answerer.py
│   ├── test_alignment.py
│   ├── test_compiler.py
│   ├── test_evaluator.py
│   └── test_research.py
│
├── applications.md                   # Application tracker (auto-written, gitignored)
├── skills_heatmap.json               # Skill frequency across all JDs (auto-written, gitignored)
└── output/                           # Generated PDFs, reports, answers (gitignored)
```

---

## Usage

### Full pipeline

```bash
# From a URL
python apply.py https://jobs.example.com/backend-engineer

# From a saved JD file
python apply.py --jd-file jd.txt

# With verbose output (prints full company profile + resume changes JSON)
python apply.py --jd-file jd.txt --verbose

# Evaluation only — score the role, write report, skip resume tailoring
python apply.py --jd-file jd.txt --eval-only

# Skip evaluation and go straight to tailoring
python apply.py --jd-file jd.txt --skip-eval

# Don't append to applications.md tracker
python apply.py --jd-file jd.txt --no-track

# Switch contact details (email + phone) in the resume
python apply.py --jd-file jd.txt --contact alternate
python apply.py --jd-file jd.txt --contact primary
```

Output files written to `output/`:
```
output/<company>_<role>_<date>.pdf
output/<company>_<role>_<date>.eval.md      ← evaluation report
output/<company>_<role>_<date>.profile.json
output/<company>_<role>_<date>.changes.json
```

Tracker row appended to `applications.md`.

### With form question answering

```bash
python apply.py --jd-file jd.txt --questions questions.txt
python apply.py --jd-file jd.txt --questions questions.txt --max-words 200
```

`questions.txt` format — one question per blank-separated block:

```
Do you have experience with distributed systems?

Describe an unexpected challenge you faced in a recent project.

How have you used AI tools to improve your work?
```

Numbered prefixes (`1.`, `Q1.`, `Q:`) and `Type here...` placeholders stripped automatically.

### Recompile PDF without re-running Claude

```bash
python apply.py --recompile output/<company>_<role>_<date>.changes.json
```

### Playwright auto-fill

Open a visible browser, navigate to the application form, review proposed answers
field-by-field, then fill — never auto-submits.

```bash
python autofill.py https://jobs.example.com/apply/12345
python autofill.py https://jobs.example.com/apply/12345 --jd-file jd.txt
python autofill.py <url> --fast      # auto-accept high-confidence fields
python autofill.py <url> --headless
```

If no form fields are detected at the URL (e.g. you passed the job listing page),
the browser stays open so you can click "Apply", log in, and navigate to the form.
Press Enter in the terminal when you're on the form page.

Interactive review per field:
```
[1] First Name [text, required]
     Proposed: 'Jane' [auto]
     > <Enter to accept / type to override / s to skip>

[2] Why do you want to work here? [textarea]
     Proposed: 'I've built settlement engines...' [medium]
     >
```

### Answer form questions only (no PDF)

```bash
python fill.py --jd-file jd.txt --questions questions.txt
python fill.py --jd-file jd.txt --questions questions.txt --max-words 200
```

### Tracker management

```bash
python tracker.py show                # print the full tracker table
python tracker.py stats               # summary: N applied, N interview, N offer, avg score
python tracker.py dedup               # find and remove duplicate entries
python tracker.py dedup --dry-run     # preview what would be removed
python tracker.py skills              # show skill frequency heatmap across all JDs
python tracker.py skills --top 10     # top 10 skills only
python tracker.py skills --min 2      # only skills seen in 2+ JDs
```

`skills_heatmap.json` accumulates every `key_vocabulary` term from company profiles and
every "missing" skill flagged by the evaluator. After a few applications, `--min 2` shows
which skills are genuinely recurring across companies — signal for what to learn vs what
to just translate in resume vocabulary.

`applications.md` schema:

| Date | Company | Role | Match | Skills | Seniority | Likelihood | Status | URL | Notes |
|------|---------|------|-------|--------|-----------|------------|--------|-----|-------|

`Status` starts as `applied`. Update manually to: `screen`, `interview`, `offer`,
`rejected`, `ghosted`.

The tracker is persistent state maintained across pipeline runs — each application
appends a row automatically, giving you a running record of every role scored,
tailored, and submitted. Combined with the skills heatmap, it closes the feedback
loop: you can see which skills keep surfacing across JDs (`--min 2`) and use that
signal to prioritise what to learn or how to reframe your resume vocabulary, rather
than optimising for a single job in isolation.

---

## How it works

### Orchestration (`apply.py`)

`apply.py` is the orchestrator. It runs agents in a fixed DAG, passes structured
JSON between them, and handles short-circuit flags. Each agent is independently
testable — the orchestrator only wires inputs and outputs.

### Company research (`agents/research.py`)

Claude reads the JD and scraped company pages and returns a structured profile:

```json
{
  "company_name": "Stripe",
  "role_title": "Senior Backend Engineer",
  "team_mission": "...",
  "domain_problems": ["payment latency", "settlement correctness"],
  "tech_values": ["correctness over speed", "horizontal scale"],
  "culture_signals": ["high ownership", "async-first"],
  "key_vocabulary": ["idempotent", "exactly-once", "settlement"],
  "candidate_story": "..."
}
```

This profile is the shared context passed to every downstream agent.

### Role evaluation (`agents/evaluator.py`)

Single Claude call scores 6 dimensions against `config/profile.yml`:

| Dimension | What it measures |
|-----------|-----------------|
| Role Match | Title, scope, responsibilities vs your target roles |
| Skills Alignment | Core stack overlap; flags dealbreakers |
| Seniority Fit | YOE requirements vs your experience |
| Interview Likelihood | Weighted score (Skills 40% + Role 30% + Seniority 30%) |
| Timeline | Urgency signals, estimated process length, career trajectory fit |
| Overall | `apply` / `apply_with_caution` / `skip` |

If `interview_likelihood < 3.5`, a warning is printed but the pipeline continues.

### Resume alignment (`agents/alignment.py`)

Claude rewrites bullets and summary using the company profile as a lens:
- Maps real experience to the company's domain problems
- Weaves `key_vocabulary` verbatim into bullets (ATS + hiring manager signal)
- Reorders skills section to lead with what matters most for this role
- No fabrication — every bullet traces to something real in your resume or project files

### Project scoring (`agents/alignment.py` → `load_master_content`)

Project write-ups in `projects/*.md` are scored against company profile signals.
Top 4 most relevant included as context; character budget distributed by score.

### Skills heatmap (`agents/skills_heatmap.py`)

After each application, `key_vocabulary` and `missing` skills are written to
`skills_heatmap.json`:

```json
{
  "langchain":     { "display": "LangChain",     "count": 5 },
  "rag pipelines": { "display": "RAG pipelines", "count": 8 }
}
```

High-count skills are your real learning priorities — confirmed by the market.

### Form question answering (`agents/answerer.py`)

Questions classified as **behavioral** or **factual** before being sent to Claude:
- **Behavioral**: STAR narrative; can invent plausible detail but not skills
- **Factual**: direct answer with one concrete example from the resume

---

## LLM providers

### Per-run override

`--provider` selects the CLI to use:

```bash
python apply.py --jd-file jd.txt --provider claude
python apply.py --jd-file jd.txt --provider kiro-cli
python apply.py --jd-file jd.txt --provider codex
```

Default models per step: research=**sonnet**, eval=**haiku**, align=**opus**. Change them in `llm.py`.

### Global provider switch

```bash
LLM_PROVIDER=claude     # default — uses claude CLI auth
LLM_PROVIDER=kiro-cli   # uses kiro-cli auth
LLM_PROVIDER=codex      # uses codex CLI auth
```

`--provider` takes precedence over `LLM_PROVIDER`.

---

## Adding your own context

### Resume template

Edit `resume/*.tex`. The pipeline patches:
- `\section{Summary}` — summary paragraph
- Company name `\textbf{...}` blocks — bullet rewrites per employer
- `\begin{tabularx}` — skills table reorder

### Project write-ups

Add `.md` files to `projects/`. Each should describe one project: the problem,
approach, stack, and measurable outcomes. Scored against each company profile;
most relevant ones included as context for tailoring and question answering.

---

## Running tests

```bash
python3 -m pytest tests/ -v
```

Covers pure-logic functions in all agents. Claude subprocess calls are mocked.

---

## Environment

No `.env` required. Uses the `claude` CLI directly, reading auth from your Claude
Code session.
