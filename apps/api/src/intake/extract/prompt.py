"""Versioned prompt files. A change to a prompt is a new file (v2.md), never an edit in place,
so the version stored on every llm_calls row identifies exactly what was sent."""

import re
from pathlib import Path

_DIR = Path(__file__).parent / "prompts"
_VERSION = re.compile(r"^v\d+$")


def load_prompt(version: str) -> str:
    if not _VERSION.match(version):
        raise ValueError(f"bad prompt version: {version!r}")
    path = _DIR / f"{version}.md"
    if not path.is_file():
        raise ValueError(f"unknown prompt version: {version!r}")
    return path.read_text(encoding="utf-8")
