import json
import pytest
from pathlib import Path
from unittest.mock import patch

from agents.evaluator import (
    _parse_eval_json,
    _parse_tracker,
    dedup_tracker,
    generate_report,
    register_tracker,
)


VALID_EVAL = {
    "role_match": 4,
    "role_match_reasoning": "Title and scope align with target roles",
    "skills_alignment": {
        "score": 5,
        "matched": ["Python", "Kafka", "AWS"],
        "missing": [],
        "dealbreakers": [],
    },
    "seniority_fit": "right_level",
    "seniority_reasoning": "Requires 5-7 YOE, candidate has 6",
    "interview_likelihood": 4.5,
    "interview_likelihood_reasoning": "Strong match across all dimensions",
    "timeline": {
        "urgency": "normal",
        "urgency_signals": "No ASAP language",
        "process_weeks": 6,
        "trajectory_fit": "Staff-track ownership role",
    },
    "overall_recommendation": "apply",
    "warnings": [],
}

VALID_COMPANY_PROFILE = {
    "company_name": "Stripe",
    "role_title": "Senior Backend Engineer",
    "team_mission": "Build financial infrastructure.",
    "domain_problems": ["payment latency"],
    "tech_values": ["correctness"],
    "culture_signals": ["high ownership"],
    "key_vocabulary": ["idempotent"],
    "candidate_story": "Strong distributed systems background.",
}


# ── _parse_eval_json ──────────────────────────────────────────────────────────

class TestParseEvalJson:
    def test_plain_json(self):
        result = _parse_eval_json(json.dumps(VALID_EVAL))
        assert result["role_match"] == 4
        assert result["interview_likelihood"] == 4.5

    def test_markdown_fenced(self):
        raw = f"```json\n{json.dumps(VALID_EVAL)}\n```"
        result = _parse_eval_json(raw)
        assert result["overall_recommendation"] == "apply"

    def test_seniority_normalisation_too_junior(self):
        data = {**VALID_EVAL, "seniority_fit": "candidate is too junior for this role"}
        result = _parse_eval_json(json.dumps(data))
        assert result["seniority_fit"] == "too_junior"

    def test_seniority_normalisation_too_senior(self):
        data = {**VALID_EVAL, "seniority_fit": "too senior / overqualified"}
        result = _parse_eval_json(json.dumps(data))
        assert result["seniority_fit"] == "too_senior"

    def test_seniority_normalisation_right_level(self):
        data = {**VALID_EVAL, "seniority_fit": "right_level"}
        result = _parse_eval_json(json.dumps(data))
        assert result["seniority_fit"] == "right_level"

    def test_missing_skills_alignment_defaults(self):
        data = {**VALID_EVAL}
        del data["skills_alignment"]
        result = _parse_eval_json(json.dumps(data))
        assert result["skills_alignment"]["score"] == 3
        assert result["skills_alignment"]["matched"] == []

    def test_missing_timeline_defaults(self):
        data = {**VALID_EVAL}
        del data["timeline"]
        result = _parse_eval_json(json.dumps(data))
        assert result["timeline"]["urgency"] == "normal"
        assert result["timeline"]["process_weeks"] == 6

    def test_invalid_json_raises(self):
        with pytest.raises(RuntimeError, match="invalid JSON"):
            _parse_eval_json("{not valid json}")


# ── _parse_tracker ────────────────────────────────────────────────────────────

TRACKER_CONTENT = """# Job Applications

| Date | Company | Role | Match | Skills | Seniority | Likelihood | Status | URL | Notes |
|------|---------|------|-------|--------|-----------|------------|--------|-----|-------|
| 2026-04-01 | Stripe | Senior Backend Engineer | 5 | 4 | right_level | 4.8 | applied | https://stripe.com | stripe_sbe_20260401 |
| 2026-04-02 | Brex | Staff Engineer | 4 | 3 | right_level | 3.7 | interview | https://brex.com | brex_staff_20260402 |
| 2026-04-03 | Stripe | Backend Engineer | 4 | 4 | right_level | 4.2 | applied | https://stripe.com/2 | stripe_be_20260403 |
"""


class TestParseTracker:
    def _write_tracker(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "applications.md"
        p.write_text(content, encoding="utf-8")
        return p

    def test_parses_rows(self, tmp_path):
        p = self._write_tracker(tmp_path, TRACKER_CONTENT)
        _, rows = _parse_tracker(p)
        assert len(rows) == 3

    def test_row_fields(self, tmp_path):
        p = self._write_tracker(tmp_path, TRACKER_CONTENT)
        _, rows = _parse_tracker(p)
        assert rows[0]["company"] == "Stripe"
        assert rows[0]["role"] == "Senior Backend Engineer"
        assert rows[0]["status"] == "applied"
        assert rows[1]["status"] == "interview"

    def test_header_preserved(self, tmp_path):
        p = self._write_tracker(tmp_path, TRACKER_CONTENT)
        headers, _ = _parse_tracker(p)
        assert any("Job Applications" in h for h in headers)


# ── dedup_tracker ─────────────────────────────────────────────────────────────

class TestDedupTracker:
    def _write_tracker(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "applications.md"
        p.write_text(content, encoding="utf-8")
        return p

    def test_no_duplicates(self, tmp_path):
        p = self._write_tracker(tmp_path, TRACKER_CONTENT)
        report = dedup_tracker(p, dry_run=True)
        # Stripe x2 are duplicates; Brex is unique
        assert len(report) == 1

    def test_keeps_higher_status(self, tmp_path):
        content = """# Job Applications

| Date | Company | Role | Match | Skills | Seniority | Likelihood | Status | URL | Notes |
|------|---------|------|-------|--------|-----------|------------|--------|-----|-------|
| 2026-04-01 | Acme Corp | Backend Engineer | 4 | 4 | right_level | 4.0 | applied | | acme1 |
| 2026-04-02 | Acme Corp | Backend Engineer | 4 | 4 | right_level | 4.0 | interview | | acme2 |
"""
        p = self._write_tracker(tmp_path, content)
        report = dedup_tracker(p, dry_run=True)
        assert len(report) == 1
        assert report[0]["kept"]["status"] == "interview"
        assert len(report[0]["dropped"]) == 1

    def test_dry_run_does_not_modify_file(self, tmp_path):
        content = """# Job Applications

| Date | Company | Role | Match | Skills | Seniority | Likelihood | Status | URL | Notes |
|------|---------|------|-------|--------|-----------|------------|--------|-----|-------|
| 2026-04-01 | Acme Corp | Backend Engineer | 4 | 4 | right_level | 4.0 | applied | | acme1 |
| 2026-04-02 | Acme Corp | Backend Engineer | 4 | 4 | right_level | 4.0 | applied | | acme2 |
"""
        p = self._write_tracker(tmp_path, content)
        original = p.read_text()
        dedup_tracker(p, dry_run=True)
        assert p.read_text() == original

    def test_rewrites_file_when_not_dry_run(self, tmp_path):
        content = """# Job Applications

| Date | Company | Role | Match | Skills | Seniority | Likelihood | Status | URL | Notes |
|------|---------|------|-------|--------|-----------|------------|--------|-----|-------|
| 2026-04-01 | Acme Corp | Backend Engineer | 4 | 4 | right_level | 4.0 | applied | | acme1 |
| 2026-04-02 | Acme Corp | Backend Engineer | 4 | 4 | right_level | 4.0 | applied | | acme2 |
"""
        p = self._write_tracker(tmp_path, content)
        dedup_tracker(p, dry_run=False)
        _, rows = _parse_tracker(p)
        assert len(rows) == 1

    def test_empty_tracker_returns_empty(self, tmp_path):
        p = tmp_path / "applications.md"
        p.write_text("# Job Applications\n", encoding="utf-8")
        report = dedup_tracker(p)
        assert report == []

    def test_nonexistent_file_returns_empty(self, tmp_path):
        p = tmp_path / "nonexistent.md"
        report = dedup_tracker(p)
        assert report == []


# ── register_tracker ─────────────────────────────────────────────────────────

class TestRegisterTracker:
    def test_creates_file_with_header(self, tmp_path):
        p = tmp_path / "applications.md"
        register_tracker(VALID_COMPANY_PROFILE, VALID_EVAL, "https://stripe.com", "stripe_20260410", p)
        content = p.read_text()
        assert "# Job Applications" in content
        assert "Stripe" in content
        assert "applied" in content

    def test_appends_to_existing_file(self, tmp_path):
        p = tmp_path / "applications.md"
        register_tracker(VALID_COMPANY_PROFILE, VALID_EVAL, None, "stripe1", p)
        register_tracker(VALID_COMPANY_PROFILE, VALID_EVAL, None, "stripe2", p)
        _, rows = _parse_tracker(p)
        assert len(rows) == 2

    def test_row_contains_expected_fields(self, tmp_path):
        p = tmp_path / "applications.md"
        register_tracker(VALID_COMPANY_PROFILE, VALID_EVAL, "https://stripe.com/job", "stripe_20260410", p)
        _, rows = _parse_tracker(p)
        row = rows[0]
        assert row["company"] == "Stripe"
        assert row["role"] == "Senior Backend Engineer"
        assert row["status"] == "applied"
        assert row["likelihood"] == "4.5"
        assert row["url"] == "https://stripe.com/job"


# ── generate_report ───────────────────────────────────────────────────────────

class TestGenerateReport:
    def test_creates_markdown_file(self, tmp_path):
        with patch("agents.evaluator.OUTPUT_DIR", tmp_path):
            path = generate_report(VALID_COMPANY_PROFILE, VALID_EVAL, "stripe_sbe_20260410")
        assert path.exists()
        assert path.suffix == ".md"

    def test_report_contains_company_name(self, tmp_path):
        with patch("agents.evaluator.OUTPUT_DIR", tmp_path):
            path = generate_report(VALID_COMPANY_PROFILE, VALID_EVAL, "stripe_sbe_20260410")
        content = path.read_text()
        assert "Stripe" in content
        assert "4.5" in content
        assert "APPLY" in content
