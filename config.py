from pathlib import Path

BASE_DIR       = Path(__file__).parent
TEMPLATE_TEX   = BASE_DIR / "resume" / "resume.tex"
PROJECTS_DIR   = BASE_DIR / "projects"
OUTPUT_DIR     = BASE_DIR / "output"
PROFILE_CONFIG = BASE_DIR / "config" / "profile.yml"
TRACKER_PATH   = BASE_DIR / "applications.md"
HEATMAP_PATH   = BASE_DIR / "skills_heatmap.json"
EVAL_GATE      = 3.5   # min interview_likelihood to proceed without warning
