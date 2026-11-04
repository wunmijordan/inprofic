"""Product help drawn only from reviewed, user-facing documentation.

Deliberately a small allowlist: deployment, architecture and internal engineering
documents are never searchable by the assistant. No embeddings and no copy of any
business data; amounts always come from live tools.
"""
import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings

HELP_DOCS = ("FINANCE_AND_INSIGHTS.md", "BOOKS_TAX_AND_WHATSAPP.md", "LEDGER_CUTOVER.md", "COMMERCE_INTEGRATION.md")
_WORD = re.compile(r"[a-z0-9]{3,}")
EXCERPT = 700


@lru_cache(maxsize=1)
def _sections():
    base = Path(settings.BASE_DIR) / "docs"
    rows = []
    for name in HELP_DOCS:
        path = base / name
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        title, heading, buffer = name.rsplit(".", 1)[0].replace("_", " ").title(), "Overview", []
        for line in text.splitlines():
            if line.startswith("# ") and title == name.rsplit(".", 1)[0].replace("_", " ").title():
                title = line[2:].strip()
            elif line.startswith("## "):
                if buffer:
                    rows.append((title, heading, "\n".join(buffer).strip()))
                heading, buffer = line[3:].strip(), []
            else:
                buffer.append(line)
        if buffer:
            rows.append((title, heading, "\n".join(buffer).strip()))
    return tuple(r for r in rows if r[2])


def search(query, limit=3):
    terms = set(_WORD.findall((query or "").lower()))
    scored = []
    for title, heading, body in _sections():
        head_words, body_words = _WORD.findall(heading.lower()), _WORD.findall(body.lower())
        score = sum(3 for t in terms if t in head_words) + sum(min(body_words.count(t), 4) for t in terms)
        if score:
            scored.append((score, title, heading, body))
    scored.sort(key=lambda row: -row[0])
    return [{"document": t, "section": h, "excerpt": b[:EXCERPT]} for _s, t, h, b in scored[:limit]]
