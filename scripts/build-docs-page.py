#!/usr/bin/env python3
"""Build the shareable handbook page from docs/report.md and docs/manual.md.

The Markdown files stay the single source of truth; the page renders them in the browser.
Output: build/handbook.html (git-ignored). Publish it as a private page after each milestone.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def js_string(text: str) -> str:
    # JSON is a valid JS string literal; escape "</" so the text cannot close the script tag.
    return json.dumps(text, ensure_ascii=False).replace("</", "<\\/")


def main() -> None:
    template = (ROOT / "scripts" / "docs-page.template.html").read_text()
    page = template.replace("__REPORT_MD__", js_string((ROOT / "docs" / "report.md").read_text()))
    page = page.replace("__MANUAL_MD__", js_string((ROOT / "docs" / "manual.md").read_text()))
    out = ROOT / "build" / "handbook.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(page)
    print(f"wrote {out} ({len(page) // 1024} KB)")


if __name__ == "__main__":
    main()
