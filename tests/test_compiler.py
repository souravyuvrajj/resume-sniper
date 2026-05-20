import pytest
from agents.compiler import patch_summary, patch_company_bullets, patch_skills, _re_safe


# ── Minimal LaTeX fixtures ────────────────────────────────────────────────────

def make_tex_with_summary(summary: str) -> str:
    return (
        r"\section{Summary}" + "\n\n"
        + summary + "\n\n"
        + r"\vspace{-4pt}" + "\nrest of doc"
    )


def make_tex_with_company(company_tex: str, bullets: list[str]) -> str:
    items = "\n\n".join(f"\\item {b}" for b in bullets)
    return (
        r"\textbf{" + company_tex + r"} — Engineer" + "\n"
        r"\begin{itemize}[leftmargin=*]" + "\n\n"
        + items + "\n\n"
        + r"\end{itemize}" + "\nrest"
    )


def make_tex_with_skills(rows: str) -> str:
    return (
        r"\begin{tabularx}{\textwidth}{l X}" + "\n\n"
        + rows + "\n\n"
        + r"\end{tabularx}" + "\nrest"
    )


# ── _re_safe ──────────────────────────────────────────────────────────────────

class TestReSafe:
    def test_doubles_backslashes(self):
        assert _re_safe(r"\textbf{word}") == r"\\textbf{word}"

    def test_no_backslash_unchanged(self):
        assert _re_safe("hello world") == "hello world"

    def test_multiple_backslashes(self):
        result = _re_safe(r"\textbf{hello} \textit{world}")
        assert result == r"\\textbf{hello} \\textit{world}"

    def test_empty_string(self):
        assert _re_safe("") == ""


# ── patch_summary ─────────────────────────────────────────────────────────────

class TestPatchSummary:
    def test_replaces_summary(self):
        tex = make_tex_with_summary("Old summary text.")
        result = patch_summary(tex, r"New summary \textbf{content}.")
        assert r"New summary \textbf{content}." in result
        assert "Old summary text." not in result

    def test_preserves_surrounding_latex(self):
        tex = make_tex_with_summary("Old summary.")
        result = patch_summary(tex, "New summary.")
        assert r"\section{Summary}" in result
        assert r"\vspace{-4pt}" in result

    def test_raises_when_section_not_found(self):
        tex = r"\section{Experience}" + "\n\nsome content\n\n" + r"\vspace{-4pt}"
        with pytest.raises(RuntimeError, match="Summary section not found"):
            patch_summary(tex, "New summary.")

    def test_backslash_in_replacement_safe(self):
        tex = make_tex_with_summary("Old.")
        result = patch_summary(tex, r"Built \textbf{settlement} engine.")
        assert r"\textbf{settlement}" in result


# ── patch_company_bullets ─────────────────────────────────────────────────────

class TestPatchCompanyBullets:
    def test_replaces_amazon_bullets(self):
        old_bullets = ["Old bullet 1.", "Old bullet 2.", "Old bullet 3."]
        tex = make_tex_with_company("Amazon", old_bullets)
        new_bullets = ["New bullet A.", "New bullet B.", "New bullet C."]
        result = patch_company_bullets(tex, "Amazon", new_bullets)
        assert "New bullet A." in result
        assert "Old bullet 1." not in result

    def test_replaces_demandbase_bullets(self):
        old = ["B1", "B2", "B3", "B4", "B5", "B6"]
        tex = make_tex_with_company("Demandbase", old)
        new = ["N1", "N2", "N3", "N4", "N5", "N6"]
        result = patch_company_bullets(tex, "Demandbase", new)
        assert "N1" in result
        assert "B1" not in result

    def test_samsung_key_maps_to_tex_name(self):
        # "Samsung" in JSON maps to "Samsung R\&D" in LaTeX
        old = ["B1", "B2", "B3", "B4"]
        tex = make_tex_with_company(r"Samsung R\&D", old)
        new = ["N1", "N2", "N3", "N4"]
        result = patch_company_bullets(tex, "Samsung", new)
        assert "N1" in result

    def test_raises_when_company_not_found(self):
        tex = make_tex_with_company("Acme", ["Bullet."])
        with pytest.raises(RuntimeError, match="Could not find bullet block"):
            patch_company_bullets(tex, "NonExistent", ["New."])

    def test_bullet_backslash_safe(self):
        old = ["Old bullet."]
        tex = make_tex_with_company("Amazon", old)
        new = [r"Built \textbf{settlement} engine with 50K tx/day."]
        result = patch_company_bullets(tex, "Amazon", new)
        assert r"\textbf{settlement}" in result

    def test_preserves_itemize_delimiters(self):
        tex = make_tex_with_company("Amazon", ["Old."])
        result = patch_company_bullets(tex, "Amazon", ["New."])
        assert r"\begin{itemize}[leftmargin=*]" in result
        assert r"\end{itemize}" in result


# ── patch_skills ──────────────────────────────────────────────────────────────

class TestPatchSkills:
    def test_replaces_language_row(self):
        old_rows = r"\textbf{Languages:} & Java, Python \\"
        tex = make_tex_with_skills(old_rows)
        skills = {
            "Languages": "Python, Go, Java",
            "Frameworks": "FastAPI, Spring",
            "Architecture": "microservices",
            "Data": "PostgreSQL",
            "Streaming / Messaging": "Kafka",
            "Cloud & Infra": "AWS, GCP",
            "Observability": "Datadog",
            "AI Systems": "LangChain",
            "Blockchain": "Ethereum",
            "Testing": "pytest",
        }
        result = patch_skills(tex, skills)
        assert "Python, Go, Java" in result
        assert "Java, Python" not in result

    def test_ampersand_escaped_in_values(self):
        old_rows = r"\textbf{Languages:} & Java \\"
        tex = make_tex_with_skills(old_rows)
        skills = {
            "Languages": "REST & gRPC",
            "Frameworks": "Spring",
            "Architecture": "microservices",
            "Data": "PostgreSQL",
            "Streaming / Messaging": "Kafka",
            "Cloud & Infra": "AWS",
            "Observability": "Datadog",
            "AI Systems": "LangChain",
            "Blockchain": "Ethereum",
            "Testing": "pytest",
        }
        result = patch_skills(tex, skills)
        assert r"REST \& gRPC" in result

    def test_raises_when_tabularx_not_found(self):
        tex = r"\begin{table}\n\nsome rows\n\n\end{table}"
        with pytest.raises(RuntimeError, match="Skills tabularx block not found"):
            patch_skills(tex, {"Languages": "Python"})

    def test_preserves_tabularx_delimiters(self):
        old_rows = r"\textbf{Languages:} & Python \\"
        tex = make_tex_with_skills(old_rows)
        skills = {"Languages": "Go", "Frameworks": "Gin", "Architecture": "x",
                  "Data": "x", "Streaming / Messaging": "x", "Cloud & Infra": "x",
                  "Observability": "x", "AI Systems": "x", "Blockchain": "x", "Testing": "x"}
        result = patch_skills(tex, skills)
        assert r"\begin{tabularx}{\textwidth}{l X}" in result
        assert r"\end{tabularx}" in result
