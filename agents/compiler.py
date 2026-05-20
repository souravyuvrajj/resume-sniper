import re
import subprocess
from pathlib import Path
from config import TEMPLATE_TEX, OUTPUT_DIR
import shutil

PDFLATEX = (
    shutil.which("pdflatex")
    or "/usr/local/texlive/2026basic/bin/universal-darwin/pdflatex"
)

# Map the company names used in the JSON to their exact LaTeX representations
COMPANY_TEX_NAMES = {
    "Amazon": "Amazon",
    "Demandbase": "Demandbase",
    "Samsung": "Samsung R\\&D",
    "Samsung R&D": "Samsung R\\&D",
}

def patch_company_bullets(tex: str, company_key: str, new_bullets: list) -> str:
    tex_name = COMPANY_TEX_NAMES.get(company_key, company_key)
    pattern_name = tex_name.replace('\\', r'\\')  # was: re.escape(tex_name)

    pattern = (
        r'(\\textbf\{' + pattern_name + r'\}.*?'
        r'\\begin\{itemize\}\[leftmargin=\*\]\n\n)'
        r'(.*?)'
        r'(\n\n\\end\{itemize\})'
    )

    bullets_tex = "\n\n".join(f"\\item {b}" for b in new_bullets)
    result = re.sub(pattern, r'\g<1>' + _re_safe(bullets_tex) + r'\g<3>', tex, flags=re.DOTALL)

    if result == tex:
        raise RuntimeError(
            f"Could not find bullet block for company '{company_key}' (looked for '{tex_name}')"
        )
    return result


def patch_summary(tex: str, new_summary: str) -> str:
    pattern = r'(\\section\{Summary\}\n\n)(.*?)(\n\n\\vspace\{-4pt\})'
    result = re.sub(pattern, r'\g<1>' + _re_safe(new_summary) + r'\g<3>', tex, flags=re.DOTALL)
    if result == tex:
        raise RuntimeError("Summary section not found in template — check regex pattern")
    return result


def patch_skills(tex: str, skills: dict) -> str:
    # The order and exact LaTeX labels must match the original template
    SKILL_ORDER = [
        ("Languages",           "Languages:"),
        ("Frameworks",          "Frameworks:"),
        ("Architecture",        "Architecture:"),
        ("AI/ML",               "AI/ML:"),
        ("AI Systems",          "AI Systems:"),
        ("Data",                "Data:"),
        ("Streaming / Messaging", "Streaming / Messaging: "),   # note trailing space — matches original
        ("Cloud & Infra",       "Cloud \\& Infra:"),
        ("Observability",       "Observability:"),
        ("Testing",             "Testing:"),
    ]

    rows = []
    for key, label in SKILL_ORDER:
        value = skills.get(key, "")
        if value:
            # Escape bare & in values (e.g. "REST & gRPC") so it doesn't break tabularx columns
            value = re.sub(r'(?<!\\)&', r'\\&', value)
            rows.append(f"\\textbf{{{label}}} & {value} \\\\")

    skills_tex = "\n\n".join(rows)

    pattern = r'(\\begin\{tabularx\}\{\\textwidth\}\{l X\}\n\n)(.*?)(\n\n\\end\{tabularx\})'
    result = re.sub(pattern, r'\g<1>' + _re_safe(skills_tex) + r'\g<3>', tex, flags=re.DOTALL)

    if result == tex:
        raise RuntimeError("Skills tabularx block not found in template — check regex pattern")
    return result


def patch_subtitle(tex: str, new_subtitle: str) -> str:
    pattern = r'(\\textbf\{\\LARGE CANDIDATE_NAME\} & Email:.*?\n).*?( & Mobile:)'
    # The subtitle is the second line of the heading tabular
    pattern = r'(Senior Engineer \$\|?\$.*?)( & Mobile:)'
    result = re.sub(pattern, _re_safe(new_subtitle) + r'\g<2>', tex)
    if result == tex:
        # fallback: try the original pattern
        pattern = r'(Applied AI Engineer \$\|?\$.*?)( & Mobile:)'
        result = re.sub(pattern, _re_safe(new_subtitle) + r'\g<2>', tex)
    return result


def patch_and_compile(changes: dict, output_name: str) -> Path:
    tex = TEMPLATE_TEX.read_text(encoding="utf-8")

    if "subtitle" in changes:
        tex = patch_subtitle(tex, changes["subtitle"])

    tex = patch_summary(tex, changes["summary"])

    for company, bullets in changes["experience"].items():
        tex = patch_company_bullets(tex, company, bullets)

    tex = patch_skills(tex, changes["skills"])

    OUTPUT_DIR.mkdir(exist_ok=True)
    tex_out = OUTPUT_DIR / f"{output_name}.tex"
    tex_out.write_text(tex, encoding="utf-8")

    _run_pdflatex(tex_out)

    _cleanup_aux(output_name)

    pdf = OUTPUT_DIR / f"{output_name}.pdf"
    if not pdf.exists():
        raise RuntimeError(f"pdflatex ran but no PDF found at {pdf}")
    return pdf


def _run_pdflatex(tex_path: Path):
    """Run pdflatex twice (second pass resolves any internal references)."""
    cmd = [
        PDFLATEX,
        "-interaction=nonstopmode",
        f"-output-directory={OUTPUT_DIR}",
        str(tex_path),
    ]
    for run in range(2):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            # Show the last 30 lines of pdflatex output to help debug
            tail = "\n".join(result.stdout.splitlines()[-30:])
            raise RuntimeError(f"pdflatex failed (run {run + 1}):\n{tail}")


def _cleanup_aux(name: str):
    for ext in [".aux", ".log", ".out"]:
        f = OUTPUT_DIR / f"{name}{ext}"
        if f.exists():
            f.unlink()


def _re_safe(s: str) -> str:
    """Escape a string for safe use as a re.sub replacement.

    In re.sub replacement strings, backslashes are special (\\1, \\g<n>).
    Doubling every backslash makes them all literal in the output.
    """
    return s.replace("\\", "\\\\")
