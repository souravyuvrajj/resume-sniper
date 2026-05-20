import json
import pytest
from unittest.mock import patch

from agents.answerer import (
    parse_questions,
    classify_question,
    _build_questions_block,
    _parse_answers,
    answer_questions,
)


# ── parse_questions ───────────────────────────────────────────────────────────

class TestParseQuestions:
    def test_blank_line_separated(self):
        text = "What is your experience with Kafka?\n\nDescribe a challenge you overcame."
        qs = parse_questions(text)
        assert qs == [
            "What is your experience with Kafka?",
            "Describe a challenge you overcame.",
        ]

    def test_strips_type_here_placeholder(self):
        text = "Do you have experience with Python?\n\nType here...\n\nHow did you handle a production outage?"
        qs = parse_questions(text)
        assert len(qs) == 2
        assert "Type here..." not in qs

    def test_type_here_inline_after_question(self):
        text = "Do you have experience with distributed systems?\nType here...\n\nDescribe a challenge."
        qs = parse_questions(text)
        assert qs[0] == "Do you have experience with distributed systems?"

    def test_strips_numbered_prefix(self):
        text = "1. Do you have Go experience?\n\n2) Describe a difficult bug."
        qs = parse_questions(text)
        assert qs[0] == "Do you have Go experience?"
        assert qs[1] == "Describe a difficult bug."

    def test_strips_q_prefix(self):
        text = "Q1. How many years of Python?\n\nQ: Tell me about a time you led a project."
        qs = parse_questions(text)
        assert qs[0] == "How many years of Python?"
        assert qs[1] == "Tell me about a time you led a project."

    def test_single_question(self):
        text = "Why do you want to join us?"
        qs = parse_questions(text)
        assert qs == ["Why do you want to join us?"]

    def test_empty_input(self):
        assert parse_questions("") == []
        assert parse_questions("   \n\n   ") == []

    def test_only_placeholders(self):
        assert parse_questions("Type here...\n\nType here...") == []

    def test_windows_line_endings(self):
        text = "Question one?\r\n\r\nQuestion two?"
        qs = parse_questions(text)
        assert len(qs) == 2

    def test_multiline_question_joined(self):
        text = "This is a long question\nthat spans two lines."
        qs = parse_questions(text)
        assert qs == ["This is a long question that spans two lines."]

    def test_extra_blank_lines_between_questions(self):
        text = "Question one?\n\n\n\nQuestion two?"
        qs = parse_questions(text)
        assert len(qs) == 2


# ── classify_question ─────────────────────────────────────────────────────────

class TestClassifyQuestion:
    @pytest.mark.parametrize("q", [
        "Describe a challenge you faced.",
        "Tell me about a time you led a project.",
        "Walk me through how you handled a production incident.",
        "Share an example of when you disagreed with your team.",
        "How did you overcome a technical obstacle?",
        "Have you ever had to mentor someone?",
        "What was the hardest bug you fixed?",
        "Can you share a time you missed a deadline?",
        "Talk about an unexpected situation you encountered.",
    ])
    def test_behavioral(self, q):
        assert classify_question(q) == "behavioral"

    @pytest.mark.parametrize("q", [
        "Do you have experience with Kubernetes?",
        "How many years of Python do you have?",
        "Are you familiar with DynamoDB?",
        "What is your salary expectation?",
        "Do you require visa sponsorship?",
    ])
    def test_factual(self, q):
        assert classify_question(q) == "factual"

    def test_case_insensitive(self):
        assert classify_question("DESCRIBE a challenge") == "behavioral"
        assert classify_question("DESCRIBE A CHALLENGE") == "behavioral"


# ── _build_questions_block ────────────────────────────────────────────────────

class TestBuildQuestionsBlock:
    def test_includes_question_numbers(self):
        qs = ["Do you have Python experience?", "Describe a challenge."]
        block = _build_questions_block(qs)
        assert "[1]" in block
        assert "[2]" in block

    def test_includes_type_label(self):
        block = _build_questions_block(["Do you know Kafka?", "Tell me about a challenge."])
        assert "(factual)" in block
        assert "(behavioral)" in block

    def test_empty_returns_empty_string(self):
        assert _build_questions_block([]) == ""


# ── _parse_answers ────────────────────────────────────────────────────────────

VALID_ANSWERS_JSON = {
    "answers": [
        {"question": "Do you know Python?", "answer": "Yes, six years."},
        {"question": "Describe a challenge.", "answer": "In Q3 last year..."},
    ]
}


class TestParseAnswers:
    def test_plain_json(self):
        raw = json.dumps(VALID_ANSWERS_JSON)
        result = _parse_answers(raw)
        assert len(result) == 2
        assert result[0]["question"] == "Do you know Python?"

    def test_markdown_wrapped_json(self):
        raw = f"```json\n{json.dumps(VALID_ANSWERS_JSON)}\n```"
        result = _parse_answers(raw)
        assert len(result) == 2

    def test_markdown_no_lang_tag(self):
        raw = f"```\n{json.dumps(VALID_ANSWERS_JSON)}\n```"
        result = _parse_answers(raw)
        assert len(result) == 2

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_answers("not valid json")

    def test_missing_answers_key_raises(self):
        with pytest.raises(KeyError):
            _parse_answers('{"result": []}')


# ── answer_questions (with mock) ──────────────────────────────────────────────

MOCK_PROFILE = {
    "company_name": "Acme Corp",
    "role_title": "Senior Backend Engineer",
    "tech_values": ["correctness", "scale"],
    "key_vocabulary": ["idempotent", "distributed"],
    "candidate_story": "Strong backend background.",
}

MOCK_ANSWERS = {
    "answers": [
        {"question": "Do you have Python experience?", "answer": "Yes, six years of production Python."},
    ]
}


class TestAnswerQuestions:
    def test_returns_empty_for_no_questions(self):
        result = answer_questions(MOCK_PROFILE, [], "some content")
        assert result == []

    def test_happy_path(self):
        with patch("agents.answerer._call_claude", return_value=json.dumps(MOCK_ANSWERS)):
            result = answer_questions(
                MOCK_PROFILE,
                ["Do you have Python experience?"],
                "resume content",
            )
        assert len(result) == 1
        assert result[0]["answer"] == "Yes, six years of production Python."

    def test_retries_on_invalid_json(self):
        call_count = {"n": 0}

        def fake_call(prompt, timeout=120):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "invalid json garbage"
            return json.dumps(MOCK_ANSWERS)

        with patch("agents.answerer._call_claude", side_effect=fake_call):
            result = answer_questions(MOCK_PROFILE, ["Do you have Python?"], "resume")

        assert call_count["n"] == 2
        assert len(result) == 1

    def test_raises_after_two_failures(self):
        with patch("agents.answerer._call_claude", return_value="not json"):
            with pytest.raises(RuntimeError, match="invalid JSON after two attempts"):
                answer_questions(MOCK_PROFILE, ["Question?"], "resume")

    def test_max_words_instruction_included_in_prompt(self):
        captured = {}

        def fake_call(prompt, timeout=120):
            captured["prompt"] = prompt
            return json.dumps(MOCK_ANSWERS)

        with patch("agents.answerer._call_claude", side_effect=fake_call):
            answer_questions(MOCK_PROFILE, ["Question?"], "resume", max_words=150)

        assert "150 words" in captured["prompt"]

    def test_master_content_truncated_at_60k(self):
        captured = {}

        def fake_call(prompt, timeout=120):
            captured["prompt"] = prompt
            return json.dumps(MOCK_ANSWERS)

        big_content = "x" * 100_000
        with patch("agents.answerer._call_claude", side_effect=fake_call):
            answer_questions(MOCK_PROFILE, ["Question?"], big_content)

        # 60k x's should appear, but not the full 100k
        assert "x" * 60_000 in captured["prompt"]
        assert "x" * 60_001 not in captured["prompt"]
