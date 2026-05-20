from unittest.mock import patch

from agents.skills_heatmap import update_heatmap, load_heatmap, print_heatmap


PROFILE = {
    "key_vocabulary": ["idempotent", "exactly-once", "RAG pipelines"],
}

EVAL = {
    "skills_alignment": {
        "matched": ["Python", "Kafka"],
        "missing": ["LangChain", "LangGraph"],
    }
}


class TestUpdateHeatmap:
    def test_creates_file(self, tmp_path):
        heatmap_path = tmp_path / "skills_heatmap.json"
        with patch("agents.skills_heatmap.HEATMAP_PATH", heatmap_path):
            update_heatmap(PROFILE, EVAL)
        assert heatmap_path.exists()

    def test_key_vocabulary_recorded(self, tmp_path):
        heatmap_path = tmp_path / "skills_heatmap.json"
        with patch("agents.skills_heatmap.HEATMAP_PATH", heatmap_path):
            update_heatmap(PROFILE, EVAL)
            data = load_heatmap()
        assert "idempotent" in data
        assert data["idempotent"]["count"] == 1

    def test_missing_skills_recorded(self, tmp_path):
        heatmap_path = tmp_path / "skills_heatmap.json"
        with patch("agents.skills_heatmap.HEATMAP_PATH", heatmap_path):
            update_heatmap(PROFILE, EVAL)
            data = load_heatmap()
        assert "langchain" in data
        assert data["langchain"]["count"] == 1

    def test_counts_accumulate_across_calls(self, tmp_path):
        heatmap_path = tmp_path / "skills_heatmap.json"
        with patch("agents.skills_heatmap.HEATMAP_PATH", heatmap_path):
            update_heatmap(PROFILE, EVAL)
            update_heatmap(PROFILE, EVAL)
            data = load_heatmap()
        assert data["langchain"]["count"] == 2

    def test_none_evaluation_uses_vocab_only(self, tmp_path):
        heatmap_path = tmp_path / "skills_heatmap.json"
        with patch("agents.skills_heatmap.HEATMAP_PATH", heatmap_path):
            update_heatmap(PROFILE, None)
            data = load_heatmap()
        assert "idempotent" in data
        assert "langchain" not in data

    def test_preserves_display_case(self, tmp_path):
        heatmap_path = tmp_path / "skills_heatmap.json"
        with patch("agents.skills_heatmap.HEATMAP_PATH", heatmap_path):
            update_heatmap(PROFILE, EVAL)
            data = load_heatmap()
        assert data["rag pipelines"]["display"] == "RAG pipelines"


class TestLoadHeatmap:
    def test_returns_empty_if_missing(self, tmp_path):
        with patch("agents.skills_heatmap.HEATMAP_PATH", tmp_path / "nonexistent.json"):
            result = load_heatmap()
        assert result == {}

    def test_returns_empty_on_corrupt_json(self, tmp_path):
        heatmap_path = tmp_path / "skills_heatmap.json"
        heatmap_path.write_text("{not valid", encoding="utf-8")
        with patch("agents.skills_heatmap.HEATMAP_PATH", heatmap_path):
            result = load_heatmap()
        assert result == {}


class TestPrintHeatmap:
    def test_prints_without_error(self, tmp_path, capsys):
        heatmap_path = tmp_path / "skills_heatmap.json"
        with patch("agents.skills_heatmap.HEATMAP_PATH", heatmap_path):
            update_heatmap("Stripe", PROFILE, EVAL)
            print_heatmap()
        out = capsys.readouterr().out
        assert "LangChain" in out or "langchain" in out.lower()

    def test_empty_heatmap_message(self, tmp_path, capsys):
        with patch("agents.skills_heatmap.HEATMAP_PATH", tmp_path / "nonexistent.json"):
            print_heatmap()
        out = capsys.readouterr().out
        assert "No skill data" in out

    def test_min_count_filter(self, tmp_path, capsys):
        heatmap_path = tmp_path / "skills_heatmap.json"
        with patch("agents.skills_heatmap.HEATMAP_PATH", heatmap_path):
            update_heatmap("Stripe", PROFILE, EVAL)
            print_heatmap(min_count=2)
        out = capsys.readouterr().out
        # All skills have count=1, so nothing should print in the table
        assert "idempotent" not in out
