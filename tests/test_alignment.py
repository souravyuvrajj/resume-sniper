import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from agents.alignment import _score_project, _fix_latex_escapes, _parse_json


# ── _score_project ────────────────────────────────────────────────────────────

class TestScoreProject:
    def test_exact_signal_word_match(self):
        content = "Built a distributed system handling millions of events per day."
        signals = ["distributed systems", "event streaming"]
        score = _score_project(content, signals)
        assert score > 0

    def test_no_match(self):
        content = "Designed a beautiful landing page."
        signals = ["distributed systems", "kafka pipeline", "dynamo"]
        assert _score_project(content, signals) == 0

    def test_empty_signals(self):
        assert _score_project("any content here", []) == 0

    def test_short_words_skipped(self):
        # Words with len <= 4 are skipped per the implementation
        content = "big data with the API and for."
        signals = ["big data with the API"]
        # "big", "data", "with", "the", "API", "and", "for" — all <= 4 chars, so score = 0
        assert _score_project(content, signals) == 0

    def test_longer_words_score(self):
        content = "distributed transactions with kafka"
        signals = ["distributed transactions"]
        score = _score_project(content, signals)
        assert score == 2  # "distributed" (11) and "transactions" (12) both match

    def test_case_insensitive_matching(self):
        content = "Kafka pipeline for event streaming"
        signals = ["kafka pipeline"]
        score = _score_project(content, signals)
        assert score > 0

    def test_higher_score_for_more_matches(self):
        content_relevant = "distributed kafka streaming settlement"
        content_irrelevant = "frontend react typescript css"
        signals = ["distributed kafka", "settlement engine", "streaming pipeline"]
        assert _score_project(content_relevant, signals) > _score_project(content_irrelevant, signals)


# ── _fix_latex_escapes ────────────────────────────────────────────────────────

class TestFixLatexEscapes:
    def test_tab_before_textbf_fixed(self):
        # \t is the tab character (0x09), not backslash-t
        broken = "\x09textbf{hello}"
        fixed = _fix_latex_escapes(broken)
        assert fixed == r"\textbf{hello}"

    def test_tab_before_textit_fixed(self):
        broken = "\x09textit{hello}"
        fixed = _fix_latex_escapes(broken)
        assert fixed == r"\textit{hello}"

    def test_emph_fixed(self):
        broken = r"\emph{word}"
        fixed = _fix_latex_escapes(broken)
        assert fixed == r"\emph{word}"

    def test_normal_string_unchanged(self):
        s = "No special characters here."
        assert _fix_latex_escapes(s) == s

    def test_dict_values_fixed(self):
        obj = {"summary": "\x09textbf{Backend Engineer}", "other": "normal"}
        result = _fix_latex_escapes(obj)
        assert result["summary"] == r"\textbf{Backend Engineer}"
        assert result["other"] == "normal"

    def test_list_items_fixed(self):
        obj = ["\x09textbf{Bullet one}", "normal bullet"]
        result = _fix_latex_escapes(obj)
        assert result[0] == r"\textbf{Bullet one}"
        assert result[1] == "normal bullet"

    def test_nested_structure(self):
        obj = {"experience": {"Amazon": ["\x09textbf{Built} settlement engine"]}}
        result = _fix_latex_escapes(obj)
        assert result["experience"]["Amazon"][0] == r"\textbf{Built} settlement engine"

    def test_non_string_passthrough(self):
        assert _fix_latex_escapes(42) == 42
        assert _fix_latex_escapes(None) is None
        assert _fix_latex_escapes(3.14) == 3.14


# ── _parse_json ───────────────────────────────────────────────────────────────

VALID_PROFILE = {
    "company_name": "Acme",
    "role_title": "Backend Engineer",
    "summary": "Six years experience.",
    "experience": {"Amazon": ["Built settlement engine."]},
    "skills": {"Languages": "Python, Go"},
}


class TestParseJsonAlignment:
    def test_plain_json(self):
        raw = json.dumps(VALID_PROFILE)
        result = _parse_json(raw)
        assert result["company_name"] == "Acme"

    def test_markdown_json_block(self):
        raw = f"```json\n{json.dumps(VALID_PROFILE)}\n```"
        result = _parse_json(raw)
        assert result["role_title"] == "Backend Engineer"

    def test_markdown_no_lang_tag(self):
        raw = f"```\n{json.dumps(VALID_PROFILE)}\n```"
        result = _parse_json(raw)
        assert result["company_name"] == "Acme"

    def test_invalid_json_raises_runtime_error(self):
        with pytest.raises(RuntimeError, match="invalid JSON"):
            _parse_json("not valid json at all {{{")

    def test_whitespace_stripped(self):
        raw = f"  \n  {json.dumps(VALID_PROFILE)}  \n  "
        result = _parse_json(raw)
        assert result["company_name"] == "Acme"
