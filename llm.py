"""
Unified LLM call via CLI subprocesses.

Providers (LLM_PROVIDER env var):
  claude    Claude CLI (default) — uses `claude` CLI auth, no API key needed
  kiro-cli  Kiro CLI             — uses `kiro-cli` auth, no API key needed
  codex     Codex CLI            — uses `codex` CLI auth, no API key needed

Model shortcuts:
  claude/kiro-cli:
    "haiku"  -> claude-haiku-4-5 / claude-haiku-4.5
    "sonnet" -> claude-sonnet-4-6 / claude-sonnet-4.5
    "opus"   -> claude-opus-4-6   / claude-sonnet-4 (no opus on kiro)
  codex:
    "gpt-4.5" -> gpt-5.4 (default on your account)
    any other string passed as-is
"""

import os
import re
import subprocess
import tempfile
from typing import Optional

PROVIDER = os.environ.get("LLM_PROVIDER", "claude")

_MODEL_MAP = {
    "haiku":  "claude-haiku-4-5",
    "sonnet": "claude-sonnet-4-6",
    "opus":   "claude-opus-4-6",
}

_KIRO_MODEL_MAP = {
    "haiku":  "claude-haiku-4.5",
    "sonnet": "claude-sonnet-4.5",
    "opus":   "claude-sonnet-4",
}

_CODEX_MODEL_MAP = {
    "haiku":   "gpt-5.4",
    "sonnet":  "gpt-5.4",
    "opus":    "gpt-5.4",
    "gpt-4.5": "gpt-5.4",
    "gpt-5.4": "gpt-5.4",
}


def _resolve_model(model: Optional[str], provider: str = "claude") -> Optional[str]:
    """Return resolved model name for the given provider, or None to use CLI default."""
    if model is None:
        return None
    if provider == "kiro-cli":
        return _KIRO_MODEL_MAP.get(model, model)
    if provider == "codex":
        return "gpt-5.4"
    return _MODEL_MAP.get(model, model)


def _call_claude_cli(prompt: str, model: Optional[str], timeout: int) -> str:
    model_id = _resolve_model(model, "claude")
    cmd = ["claude", "-p", prompt]
    if model_id:
        cmd += ["--model", model_id]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI error: {result.stderr.strip()}")
    return result.stdout.strip()


def _call_kiro_cli(prompt: str, model: Optional[str], timeout: int) -> str:
    model_id = _resolve_model(model, "kiro-cli")
    cmd = ["kiro-cli", "chat", "--no-interactive"]
    if model_id:
        cmd += ["--model", model_id]
    result = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"kiro-cli error: {result.stderr.strip()}")
    clean = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", result.stdout)
    lines = [l for l in clean.splitlines()
             if not l.startswith("> ") and not l.strip().startswith("▸ Credits")]
    return "\n".join(lines).strip()


def _call_codex(prompt: str, model: Optional[str], timeout: int) -> str:
    model_id = _resolve_model(model, "codex")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        out_path = f.name
    try:
        cmd = [
            "codex", "exec", prompt,
            "--output-last-message", out_path,
            "--ephemeral", "--skip-git-repo-check",
        ]
        if model_id:
            cmd += ["-m", model_id]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            raise RuntimeError(f"codex CLI error: {result.stderr.strip()}")
        return open(out_path, encoding="utf-8").read().strip()
    finally:
        os.unlink(out_path)


def _dispatch(prompt: str, model: Optional[str], timeout: int) -> str:
    if PROVIDER == "kiro-cli":
        return _call_kiro_cli(prompt, model, timeout)
    if PROVIDER == "codex":
        return _call_codex(prompt, model, timeout)
    return _call_claude_cli(prompt, model, timeout)  # default: claude


# ── Public interface ───────────────────────────────────────────────────────────

def call_messages(
    system: str,
    user: str,
    model: Optional[str] = None,
    max_tokens: int = 4096,
    timeout: int = 120,
) -> str:
    prompt = f"{system}\n\n{user}" if system else user
    return _dispatch(prompt, model, timeout)


def call_cached(
    system: str,
    content_blocks: list,
    model: Optional[str] = None,
    max_tokens: int = 4096,
    timeout: int = 120,
) -> str:
    """CLI providers concatenate all blocks — prompt caching not applicable."""
    combined = "\n\n".join(b["text"] for b in content_blocks)
    return call_messages(system, combined, model, max_tokens, timeout)


def call_raw(
    prompt: str,
    model: Optional[str] = None,
    max_tokens: int = 4096,
    timeout: int = 120,
) -> str:
    return _dispatch(prompt, model, timeout)
