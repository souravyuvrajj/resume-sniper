import json
import pytest
from unittest.mock import patch, MagicMock

from agents.research import _parse_json, research_company


VALID_PROFILE = {
    "company_name": "Stripe",
    "role_title": "Senior Backend Engineer",
    "team_mission": "Build financial infrastructure for the internet.",
    "domain_problems": ["payment latency", "settlement correctness"],
    "tech_values": ["correctness over speed"],
    "culture_signals": ["high ownership"],
    "key_vocabulary": ["idempotent", "exactly-once"],
    "candidate_story": "Strong distributed systems background.",
}


# ── _parse_json ───────────────────────────────────────────────────────────────

class TestParseJson:
    def test_plain_json(self):
        result = _parse_json(json.dumps(VALID_PROFILE))
        assert result["company_name"] == "Stripe"

    def test_markdown_json_block(self):
        raw = f"```json\n{json.dumps(VALID_PROFILE)}\n```"
        result = _parse_json(raw)
        assert result["role_title"] == "Senior Backend Engineer"

    def test_markdown_no_lang_tag(self):
        raw = f"```\n{json.dumps(VALID_PROFILE)}\n```"
        result = _parse_json(raw)
        assert result["company_name"] == "Stripe"

    def test_whitespace_stripped(self):
        raw = f"\n\n  {json.dumps(VALID_PROFILE)}  \n"
        result = _parse_json(raw)
        assert result["company_name"] == "Stripe"

    def test_invalid_json_raises_runtime_error(self):
        with pytest.raises(RuntimeError, match="invalid JSON"):
            _parse_json("{not valid}")

    def test_all_expected_keys_present(self):
        result = _parse_json(json.dumps(VALID_PROFILE))
        for key in ("company_name", "role_title", "team_mission", "domain_problems",
                    "tech_values", "culture_signals", "key_vocabulary", "candidate_story"):
            assert key in result


# ── research_company ──────────────────────────────────────────────────────────

class TestResearchCompany:
    def test_returns_parsed_profile(self):
        with patch("agents.research.call_messages", return_value=json.dumps(VALID_PROFILE)):
            result = research_company("some JD text")
        assert result["company_name"] == "Stripe"

    def test_includes_company_context_in_prompt(self):
        captured = {}

        def fake_call(system, user, **kwargs):
            captured["user"] = user
            return json.dumps(VALID_PROFILE)

        with patch("agents.research.call_messages", side_effect=fake_call):
            research_company("JD text", company_context="We value transparency.")

        assert "We value transparency." in captured["user"]

    def test_skips_empty_company_context(self):
        captured = {}

        def fake_call(system, user, **kwargs):
            captured["user"] = user
            return json.dumps(VALID_PROFILE)

        with patch("agents.research.call_messages", side_effect=fake_call):
            research_company("JD text", company_context="")

        assert "COMPANY WEBSITE CONTEXT" not in captured["user"]

    def test_retries_once_on_failure(self):
        # SDK raises on error; research agent has no retry — one call expected
        with patch("agents.research.call_messages", return_value=json.dumps(VALID_PROFILE)):
            result = research_company("JD text")
        assert result["company_name"] == "Stripe"

    def test_raises_after_two_failures(self):
        with patch("agents.research.call_messages", side_effect=RuntimeError("API error")):
            with pytest.raises(RuntimeError):
                research_company("JD text")

    def test_jd_text_truncated_at_12k(self):
        captured = {}

        def fake_call(system, user, **kwargs):
            captured["user"] = user
            return json.dumps(VALID_PROFILE)

        big_jd = "x" * 20_000
        with patch("agents.research.call_messages", side_effect=fake_call):
            research_company(big_jd)

        assert "x" * 12_000 in captured["user"]
        assert "x" * 12_001 not in captured["user"]
