#!/usr/bin/env python3
"""
Drop Zone Watcher — Basecamp
Watches a folder for .txt files, sends them to the Claude API,
and saves formatted markdown notes to the Obsidian Articles folder.
Also generates a linked Investment Note for each article.

Author: Andrew Parmentier
Version: 2.0
"""

import os
import re
import sys
import time
import json
import shutil
import logging
import subprocess
import threading
import http.server
import socketserver
import urllib.request
import urllib.error
from html.parser import HTMLParser
from pathlib import Path
from datetime import datetime
from typing import Optional

import anthropic
import yaml

# ─── Configuration ────────────────────────────────────────────────────────────

VAULT_ROOT           = Path("/Users/parmstar/Documents/OBSIDIAN MASTER/BASECAMP")
DROP_ZONE            = VAULT_ROOT / "01-Drop Zone"
YT_DROP_ZONE         = DROP_ZONE / "YT Drop Zone"
ARTICLES_DIR         = VAULT_ROOT / "05-Resources/Sources"
INVESTMENT_NOTES_DIR = VAULT_ROOT / "05-Resources/Knowledge"
PROCESSED            = DROP_ZONE / "_processed"
LOG_FILE             = Path.home() / "dropzone.log"

HTTP_PORT = 7337  # localhost only — browser extension ingest endpoint

INGEST_LOG    = VAULT_ROOT / "log.md"
CORPUS_INDEX  = VAULT_ROOT / "index.md"
ENTITY_MANAGER = Path("/Users/parmstar/Documents/Python Scripts/entity_note_manager.py")

DOMAIN_TAGS = {
    "finance", "technology", "markets", "strategy",
    "policy", "macro", "investing",
}

# Keywords used to guess domain + topic tags from raw source text.
# Each key is a tag; values are keyword strings to search for (lowercased).
# Extend this list as the vault grows into new topic areas.
KEYWORD_TAG_MAP: dict[str, list[str]] = {
    "finance":    ["interest rate", "bank", "loan", "credit", "bond", "yield",
                   "inflation", "fed ", "federal reserve", "treasury", "debt"],
    "technology": ["artificial intelligence", " ai ", "software", "hardware",
                   "chip", "semiconductor", "cloud", "saas", "platform",
                   "startup", "tech", "compute"],
    "markets":    ["stock", "equity", "s&p", "nasdaq", "dow", "ipo", "earnings",
                   "market cap", "share price", "bull", "bear", "rally"],
    "strategy":   ["competitive", "moat", "acquisition", "merger", "pivot",
                   "growth", "market share", "positioning", "differentiat"],
    "policy":     ["regulation", "legislation", "congress", "senate", "sec",
                   "ftc", "antitrust", "tariff", "executive order", "rule"],
    "macro":      ["gdp", "unemployment", "cpi", "pce", "recession", "expansion",
                   "monetary policy", "fiscal", "central bank", "rate hike"],
    "investing":  ["portfolio", "hedge fund", "venture capital", "valuation",
                   "multiple", "dcf", "irr", "fund", "lp", "gp", "returns"],
}

CORPUS_CHAR_BUDGET = 40_000
CORPUS_MAX_NOTES   = 50

MODEL      = "claude-sonnet-4-20250514"
GATE_MODEL = "claude-sonnet-4-20250514"
POLL_SECS = 5   # how often to check the drop zone

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-5s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ─── Article prompt ────────────────────────────────────────────────────────────

ARTICLE_SYSTEM_PROMPT = """You are a research note generator for a personal knowledge management system
built in Obsidian. Your job is to transform raw article text into a structured markdown note
that is immediately useful for future reference and AI retrieval.

The vault owner is a founder and investor with deep background in financial services,
technology, and markets. Prioritize clarity, signal over noise, and connections to
business, investing, and strategy themes where relevant."""

def build_article_prompt(content: str, corpus_block: str = "") -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    prefix = f"{corpus_block}\n" if corpus_block else ""
    return f"""{prefix}Transform this article into a structured Obsidian note.

---
ARTICLE:
{content}
---

Return ONLY the markdown note. No preamble, no commentary, no code fences.

Use this exact format:

---
tags: [tag1, tag2, tag3]
date: {today}
source: [publication or URL if identifiable, otherwise "unknown"]
tickers: [$TICK, $TICK]
---

# [Descriptive title as a phrase, not a question — max 10 words]

## Key Takeaways
- [Most important point]
- [Second most important point]
- [Third most important point — add more if genuinely warranted]

## Context
[2–3 sentences explaining what this is about and why it matters]

## Notable Details
[3–5 bullet points of specific facts, data points, quotes, or details worth preserving]

## So What
[1–2 sentences: the actionable implication or investment/business relevance]

## Related
- [[]] ← leave blank, to be filled in Obsidian

---

TAGGING RULES:
- Use 3–6 lowercase hyphenated tags
- Include at least one domain tag: finance, technology, markets, strategy, policy, macro, investing
- Add specific subtopic tags: ai, supply-chain, rates, equities, credit, geopolitics, etc.
- Only include tickers for explicitly named public companies you're confident about
- If no public companies are named, omit the tickers line entirely

TITLE RULES:
- Phrase form, not a question
- Specific enough to be findable later
- No clickbait"""

# ─── Investment Note prompt ────────────────────────────────────────────────────

INVESTMENT_SYSTEM_PROMPT = """You are an investment analyst generating structured investment notes
for a personal knowledge management system built in Obsidian.

The vault owner is a founder and investor with deep background in financial services,
technology, and markets. Be direct, specific, and honest about uncertainty.
Do not hedge everything — give a point of view."""

def build_investment_prompt(content: str, article_title: str, corpus_block: str = "") -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    prefix = f"{corpus_block}\n" if corpus_block else ""
    return f"""{prefix}Generate an investment note based on this article.

ARTICLE TITLE: {article_title}

---
ARTICLE:
{content}
---

Return ONLY the markdown note. No preamble, no commentary, no code fences.

Use this exact format:

---
title: [concise 5-8 word title for the investment angle]
tickers: [$TICK, $TICK]
tags: [tag1, tag2, tag3]
date: {today}
source: [publication or URL if identifiable, otherwise "unknown"]
related: []
---

# [Investment-focused title — max 10 words, phrase form]

## Investment Thesis
[2–3 sentences: the core investment idea or implication from this article]

## Key Risks
- [Risk 1]
- [Risk 2]
- [Risk 3 — add more if genuinely warranted]

## Relevant Tickers / Assets
[Bullet list of tickers or asset classes mentioned or implied. If none, write "None identified."]

## Time Horizon
[Short / Medium / Long term — and why]

## Confidence Level
[Low / Medium / High — and one sentence explaining why]

## Related
- [[]] ← leave blank, to be filled in Obsidian

---

TAGGING RULES:
- Use 3–6 lowercase hyphenated tags
- Include at least one: investing, equities, credit, macro, private-markets, venture
- Add specific subtopic tags matching the article domain

TICKERS RULES:
- Scan the entire source content for any ticker symbols or explicitly named public companies
- Output all found tickers as a YAML list: tickers: [NVDA, MSFT, GOOGL]
- If no tickers are mentioned anywhere, output: tickers: []
- Never omit the tickers field

TITLE RULES:
- Output a title: field in frontmatter — a concise 5-8 word title for the investment angle
- Not the source title — frame it as the investment thesis angle
- Phrase form, not a question
- Specific enough to be findable later"""

# ─── YAML frontmatter helpers ──────────────────────────────────────────────────
#
# Per-note pipeline:
#
#   raw_md (string from Claude)
#       │
#       ▼
#   parse_frontmatter()
#       ├── success → (dict, body_str)
#       └── failure → ({}, raw_md)   [log warning, keep original]
#       │
#       ▼
#   [caller injects related field into dict and body]
#       │
#       ▼
#   render_note(dict, body_str)
#       │
#       ▼
#   final_md written to disk
#

def parse_frontmatter(md: str) -> tuple[dict, str]:
    """
    Split a markdown string into (frontmatter_dict, body).
    Expects the note to begin with a --- delimited YAML block.
    On any parse failure, returns ({}, md) and logs a warning.
    """
    lines = md.splitlines()
    if not lines or lines[0].strip() != "---":
        log.warning("No frontmatter delimiter found; keeping raw output.")
        return {}, md

    try:
        end = next(i for i, l in enumerate(lines[1:], 1) if l.strip() == "---")
    except StopIteration:
        log.warning("Unclosed frontmatter block; keeping raw output.")
        return {}, md

    yaml_block = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1:]).lstrip("\n")

    try:
        fm = yaml.safe_load(yaml_block) or {}
    except yaml.YAMLError as exc:
        log.warning(f"YAML parse error in frontmatter: {exc}")
        return {}, md

    if not isinstance(fm, dict):
        log.warning(f"Frontmatter parsed to {type(fm).__name__}, not dict; keeping raw output.")
        return {}, md

    return fm, body


def render_note(fm: dict, body: str) -> str:
    """Re-serialize frontmatter dict + body string into a markdown note."""
    if not fm:
        return body
    yaml_str = yaml.dump(fm, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return f"---\n{yaml_str}---\n\n{body}"


def inject_related_link(body: str, link: str) -> str:
    """
    Replace the content of the ## Related section with the given Obsidian link.
    If no ## Related section exists, appends one at the end.
    Preserves any trailing --- horizontal rule immediately after the section.
    """
    lines = body.splitlines()
    result = []
    in_related = False
    inserted = False

    for line in lines:
        stripped = line.strip()
        if stripped == "## Related":
            in_related = True
            inserted = True
            result.append(line)
            result.append(f"- [[{link}]]")
        elif in_related and (stripped.startswith("## ") or stripped == "---"):
            in_related = False
            result.append(line)
        elif in_related:
            pass  # discard old placeholder content
        else:
            result.append(line)

    if not inserted:
        result.append("\n## Related")
        result.append(f"- [[{link}]]")

    return "\n".join(result)

# ─── Back-link helpers ────────────────────────────────────────────────────────

def add_related_link(body: str, link: str) -> str:
    """
    Append an Obsidian wikilink to the ## Related section of an existing note body
    WITHOUT replacing existing links (contrast: inject_related_link replaces all content).

    Behaviour:
      - If [[link]] already appears in the ## Related section → no-op (idempotent).
      - If ## Related exists → appends the new link after existing entries.
      - If ## Related is missing → appends a new ## Related section at the end.
      - Preserves any trailing --- horizontal rule immediately after the section.
    """
    check = f"[[{link}]]"
    lines = body.splitlines()

    # Check if link is already present anywhere in ## Related
    in_related = False
    for line in lines:
        stripped = line.strip()
        if stripped == "## Related":
            in_related = True
            continue
        if in_related:
            if stripped.startswith("## ") or stripped == "---":
                break
            if check in stripped:
                return body  # already present — no-op

    # Append link into ## Related, or create section
    result = []
    in_related = False
    inserted = False

    for line in lines:
        stripped = line.strip()
        if stripped == "## Related":
            in_related = True
            result.append(line)
        elif in_related and (stripped.startswith("## ") or stripped == "---"):
            # Reached end of ## Related section — insert before the boundary
            result.append(f"- {check}")
            inserted = True
            in_related = False
            result.append(line)
        else:
            result.append(line)

    if in_related and not inserted:
        # ## Related was the last section — append at end
        result.append(f"- {check}")
        inserted = True

    if not inserted:
        result.append("\n## Related")
        result.append(f"- {check}")

    return "\n".join(result)


def update_backlinks(
    loaded_notes: list[dict],
    new_article_slug: str,
    articles_dir: Path,
) -> None:
    """
    For each matched note that was loaded into the corpus, add a wikilink
    to the new article in the matched note's ## Related section.

    Uses add_related_link() (additive, idempotent) not inject_related_link().
    Failures per-note are logged as warnings and do not abort the loop.
    """
    link = f"Sources/{new_article_slug}"
    for rec in loaded_notes:
        note_path = articles_dir / (rec["slug"] + ".md")
        if not note_path.exists():
            log.warning(f"  backlink: note file gone for '{rec['slug']}', skipping.")
            continue
        try:
            existing_md = note_path.read_text(encoding="utf-8")
            fm, body    = parse_frontmatter(existing_md)
            updated_body = add_related_link(body, link)
            if updated_body == body:
                log.info(f"  backlink: '{rec['slug']}' already links to new note.")
                continue
            updated_md = render_note(fm, updated_body) if fm else updated_body
            _atomic_write(note_path, updated_md)
            log.info(f"  backlink: updated ## Related in '{rec['slug']}'")
        except Exception as e:
            log.warning(f"  backlink: could not update '{rec['slug']}': {e}")


# ─── Corpus retrieval helpers ─────────────────────────────────────────────────
#
# Pre-ingest pipeline (runs before any Claude call):
#
#   raw source text
#       │
#       ▼
#   detect_source_tags(content)
#       │  → list of likely tags, sorted by keyword hit count
#       ▼
#   parse_index(index.md text)          parse_log_recency(log.md text)
#       │  → [{slug,title,tags,date}]        │  → {title: date}
#       └──────────────┬─────────────────────┘
#                      ▼
#               rank_notes(records, source_tags, recency)
#                      │  → top-N records, domain first → overlap → recency
#                      ▼
#               load_corpus(ranked, articles_dir, budget)
#                      │  → combined note text (≤ CORPUS_CHAR_BUDGET chars)
#                      ▼
#               build_corpus_block(corpus_text)
#                      │  → formatted string injected into Claude prompts
#                      ▼
#              build_article_prompt(content, corpus_block)
#              build_investment_prompt(content, title, corpus_block)
#

def detect_source_tags(content: str) -> list[str]:
    """
    Return a ranked list of likely tags for the raw source text.
    Uses KEYWORD_TAG_MAP keyword counting — no Claude call.
    Tags with more keyword hits rank first.
    Returns [] if no keywords match.
    """
    lower = content.lower()
    hits: dict[str, int] = {}
    for tag, keywords in KEYWORD_TAG_MAP.items():
        count = sum(1 for kw in keywords if kw in lower)
        if count:
            hits[tag] = count
    return sorted(hits, key=lambda t: hits[t], reverse=True)


def parse_index(index_text: str) -> list[dict]:
    """
    Parse index.md content into a list of records.
    Each line format: - [[Sources/slug|Title]] — summary — tag1, tag2 — YYYY-MM-DD

    Parsing strategy: extract slug+title from wikilink anchor, then rsplit(" — ", 2)
    on the remainder to get [summary, tags_str, date] — robust to em dashes in summaries.

    Skips malformed lines silently.
    """
    records = []
    for line in index_text.splitlines():
        line = line.strip()
        if not line.startswith("- [[Sources/"):
            continue
        try:
            # Extract slug and title from wikilink: [[Sources/SLUG|TITLE]]
            inner_start = len("- [[Sources/")
            inner_end   = line.index("]]", inner_start)
            inner       = line[inner_start:inner_end]        # "slug|Title"
            slug, title = inner.split("|", 1)

            # Everything after "]] — " is summary — tags — date
            remainder = line[inner_end + len("]] — "):]
            parts = remainder.rsplit(" — ", 2)
            if len(parts) != 3:
                continue
            summary, tags_str, date = parts
            tags = [t.strip() for t in tags_str.split(",") if t.strip()]

            records.append({
                "slug":    slug.strip(),
                "title":   title.strip(),
                "summary": summary.strip(),
                "tags":    tags,
                "date":    date.strip(),
            })
        except (ValueError, IndexError):
            continue  # malformed line — skip silently
    return records


def parse_log_recency(log_text: str) -> dict[str, str]:
    """
    Parse log.md into {title: date} for recency ranking.
    Line format: ## [YYYY-MM-DD] ingest | Title | tags
    Later entries for the same title overwrite earlier ones (keeps most recent).
    Skips malformed lines silently.
    """
    recency: dict[str, str] = {}
    for line in log_text.splitlines():
        line = line.strip()
        if not line.startswith("## ["):
            continue
        try:
            # "## [YYYY-MM-DD] ingest | Title | tags"
            date_end = line.index("]")
            date     = line[4:date_end]          # "YYYY-MM-DD" (skip leading "[")
            parts    = line[date_end + 1:].split(" | ")
            if len(parts) < 2:
                continue
            title = parts[1].strip()
            recency[title] = date
        except (ValueError, IndexError):
            continue
    return recency


def rank_notes(
    index_records: list[dict],
    source_tags: list[str],
    log_recency: dict[str, str],
) -> list[dict]:
    """
    Rank index records by relevance to source_tags:
      1. Domain tag exact match (bool, descending)
      2. Topic tag overlap count (descending)
      3. Recency from log.md (descending — most recent first)

    Returns only records with at least one overlapping tag.
    """
    source_set    = set(source_tags)
    source_domain = next((t for t in source_tags if t in DOMAIN_TAGS), None)

    scored = []
    for rec in index_records:
        rec_tags = set(rec["tags"])
        overlap  = len(source_set & rec_tags)
        if overlap == 0:
            continue
        domain_match = int(
            source_domain is not None and source_domain in rec_tags
        )
        date = log_recency.get(rec["title"], rec.get("date", "0000-00-00"))
        scored.append((rec, domain_match, overlap, date))

    scored.sort(key=lambda x: (x[1], x[2], x[3]), reverse=True)
    return [rec for rec, *_ in scored]


def load_corpus(
    ranked_records: list[dict],
    articles_dir: Path,
    char_budget: int = CORPUS_CHAR_BUDGET,
    max_notes: int = CORPUS_MAX_NOTES,
) -> list[dict]:
    """
    Load note files for ranked records up to char_budget total characters.

    Budget policy: skip any note that would exceed the remaining budget;
    continue scanning for smaller notes that still fit (no partial notes).

    Returns list of records that were successfully loaded, each augmented
    with a "text" key containing the note content.
    """
    loaded  = []
    used    = 0
    candidates = ranked_records[:max_notes * 2]  # oversample to fill budget

    for rec in candidates:
        if len(loaded) >= max_notes:
            break
        note_path = articles_dir / (rec["slug"] + ".md")
        if not note_path.exists():
            log.warning(f"  corpus: note file missing for '{rec['slug']}', skipping.")
            continue
        try:
            text = note_path.read_text(encoding="utf-8")
        except Exception as e:
            log.warning(f"  corpus: could not read '{rec['slug']}': {e}, skipping.")
            continue
        if used + len(text) > char_budget:
            continue  # skip this note; keep scanning for smaller ones
        loaded.append({**rec, "text": text})
        used += len(text)

    log.info(f"  corpus: loaded {len(loaded)} note(s), {used:,} chars")
    return loaded


def build_corpus_block(loaded_notes: list[dict]) -> str:
    """
    Format loaded corpus notes into a prompt-ready block.
    Returns "" if loaded_notes is empty.
    """
    if not loaded_notes:
        return ""
    sections = []
    for note in loaded_notes:
        sections.append(f"NOTE: {note['title']}\n---\n{note['text'].strip()}\n---")
    body = "\n\n".join(sections)
    return (
        "EXISTING VAULT NOTES ON RELATED TOPICS:\n"
        "Use these to cite agreements, flag contradictions, and note new evidence "
        "compared to what is already in the vault.\n\n"
        + body
        + "\n\n"
    )


# ─── Index + log helpers ──────────────────────────────────────────────────────

def extract_context(body: str) -> str:
    """
    Return the first sentence of the ## Context section of a note body.
    Returns "" if the section is missing or has no text.
    """
    in_context = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped == "## Context":
            in_context = True
            continue
        if in_context:
            if stripped.startswith("## ") or stripped == "---":
                break
            if stripped:
                # First non-empty line: return up to the first sentence boundary
                return stripped.split(". ")[0].rstrip(".")
    return ""


def find_domain_tag(tags: list) -> str:
    """
    Return the first tag from `tags` that is a known domain tag.
    Falls back to "other" if none match or tags is empty/None.
    """
    for tag in (tags or []):
        if tag in DOMAIN_TAGS:
            return tag
    return "other"


def append_log(log_path: Path, title: str, tags: list, date: str, slug: str) -> None:
    """
    Append one ingest record to log.md.
    Skips if an entry for this slug already exists (dedup on restart).
    Format: ## [YYYY-MM-DD] ingest | Title | tag1, tag2
    """
    tag_str = ", ".join(tags or [])
    line = f"## [{date}] ingest | {title} | {tag_str}\n"
    try:
        existing = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        # Dedup: slug appears in wikilinks we write in index, but log lines carry the title.
        # Use title as the dedup key — good enough for a personal tool.
        if f"| {title} |" in existing:
            log.info(f"  log.md: entry for '{title}' already exists, skipping.")
            return
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line)
        log.info(f"  log.md: appended entry for '{title}'")
    except Exception as e:
        log.warning(f"Could not write to log.md: {e}")


def upsert_index(
    index_path: Path,
    slug: str,
    title: str,
    summary: str,
    tags: list,
    date: str,
) -> None:
    """
    Insert or update an article entry in index.md.

    index.md structure:
      ## <domain>
      - [[Sources/slug|Title]] — summary — tag1, tag2 — YYYY-MM-DD
      ...

    Upsert logic (line-by-line scan):
      1. If a line containing [[Sources/slug| is found → replace it in-place.
      2. If the domain ## header exists → append entry after the last entry in that section.
      3. If the domain ## header is missing → append header + entry at end of file.

    Writes via a .tmp file + os.replace() for crash-safe atomicity.
    """
    domain  = find_domain_tag(tags)
    tag_str = ", ".join(tags or [])
    entry   = f"- [[Sources/{slug}|{title}]] — {summary} — {tag_str} — {date}"
    header  = f"## {domain}"

    existing = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    lines    = existing.splitlines(keepends=True)

    # ── Pass 1: upsert in-place if slug already exists ────────────────────────
    slug_marker = f"[[Sources/{slug}|"
    for i, line in enumerate(lines):
        if slug_marker in line:
            lines[i] = entry + "\n"
            _atomic_write(index_path, "".join(lines))
            log.info(f"  index.md: updated entry for '{title}'")
            return

    # ── Pass 2: find domain header and insert after its last entry ────────────
    header_idx = None
    insert_idx = None
    for i, line in enumerate(lines):
        stripped = line.rstrip("\n")
        if stripped == header:
            header_idx = i
            insert_idx = i + 1  # default: right after header
        elif header_idx is not None and stripped.startswith("## "):
            # Next header found — insert before it
            break
        elif header_idx is not None and stripped.startswith("- "):
            insert_idx = i + 1  # move insertion point after each entry

    if header_idx is not None:
        lines.insert(insert_idx, entry + "\n")
        _atomic_write(index_path, "".join(lines))
        log.info(f"  index.md: inserted entry for '{title}' under {header}")
        return

    # ── Pass 3: domain header missing — append at end ─────────────────────────
    suffix = "\n" if existing and not existing.endswith("\n") else ""
    new_block = f"{suffix}{header}\n{entry}\n"
    _atomic_write(index_path, existing + new_block)
    log.info(f"  index.md: created section {header} and added '{title}'")


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path via a .tmp file, using os.replace() for atomicity."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


# ─── Core logic ────────────────────────────────────────────────────────────────

def slugify(title: str) -> str:
    """Convert a title to a safe filename."""
    slug = title.strip().replace(" ", "-")
    safe = "".join(c for c in slug if c.isalnum() or c in "-_")
    return safe[:80]  # cap at 80 chars


def extract_title(markdown: str) -> str:
    """Pull the H1 title from the generated markdown."""
    for line in markdown.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return f"note-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def _safe_write(content: str, out_dir: Path, slug: str) -> Path:
    """Write content to out_dir/slug.md, avoiding overwrites with a timestamp suffix."""
    out_path = out_dir / (slug + ".md")
    if out_path.exists():
        out_path = out_dir / f"{slug}-{datetime.now().strftime('%H%M%S')}.md"
    out_path.write_text(content, encoding="utf-8")
    return out_path


def process_file(path: Path, client: anthropic.Anthropic) -> bool:
    """
    Full ingest pipeline for one .txt file.

    Flow:
      1. Claude call 1  → article note markdown
      2. Claude call 2  → investment note markdown  (graceful degrade on failure)
      3. Parse + reserialize YAML frontmatter for each note
      4. Inject cross-links into both frontmatters and ## Related body sections
      5. Write both notes to disk
      6. Move source file to _processed/

    Returns True on full success so the caller can add the filename to `seen`.
    Returns False if the file should remain available for retry.

    Flow:
      0. Read source .txt
      1. [Phase 3] Corpus retrieval — detect likely tags, rank matching notes from
         index.md, load up to CORPUS_MAX_NOTES note files within CORPUS_CHAR_BUDGET.
         Graceful degrade: if index.md missing, corpus_block = "" (Phase 1/2 behavior).
      2. Claude call 1  → article note markdown  (corpus injected if available)
      3. Claude call 2  → investment note markdown  (corpus injected; graceful degrade)
      4. Parse + reserialize YAML frontmatter for each note
      5. Inject cross-links into both frontmatters and ## Related body sections
      6. Write both notes to disk
      7. [Phase 3] update_backlinks — add wikilink to new article in each matched note
      8. Append to log.md  (always, even if investment note failed)
      9. Upsert into index.md
     10. Move source file to _processed/
    """
    log.info(f"Processing: {path.name}")

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        log.error(f"Could not read {path.name}: {e}")
        return False

    if not content.strip():
        log.warning(f"Skipping empty file: {path.name}")
        _move_to_processed(path)
        return True

    # ── Content type detection ────────────────────────────────────────────────
    content_type = detect_content_type(content)
    if content_type == "youtube":
        log.warning(
            f"Skipping {path.name} — bare YouTube URL detected. "
            "Drop a transcript or article text instead."
        )
        _move_to_processed(path)
        return True
    if content_type == "url":
        url = content.strip()
        log.info(f"Bare URL detected — fetching: {url}")
        fetched = fetch_url(url)
        if fetched:
            content = fetched
            log.info(f"Fetched {len(fetched)} chars from {url}")
        else:
            log.warning(f"Could not fetch {url} — skipping")
            _move_to_processed(path)
            return True
        # fall through to pipeline
    # content_type == "text" — fall through to normal pipeline

    # ── Relevance gate ────────────────────────────────────────────────────────
    log.info("Relevance gate …")
    relevant, gate_reason = relevance_gate(content, client)
    if not relevant:
        log.info(f"Relevance gate: skipped Investment Note — {gate_reason}")
    run_investment = relevant

    # ── Step 1: Corpus retrieval ──────────────────────────────────────────────
    corpus_block  = ""
    loaded_notes: list[dict] = []
    if CORPUS_INDEX.exists() and INGEST_LOG.exists():
        log.info("Building corpus context …")
        source_tags  = detect_source_tags(content)
        log.info(f"  detected tags: {source_tags or '(none)'}")
        index_records = parse_index(CORPUS_INDEX.read_text(encoding="utf-8"))
        log_recency   = parse_log_recency(INGEST_LOG.read_text(encoding="utf-8"))
        ranked        = rank_notes(index_records, source_tags, log_recency)
        loaded_notes  = load_corpus(ranked, ARTICLES_DIR)
        corpus_block  = build_corpus_block(loaded_notes)
    elif not CORPUS_INDEX.exists():
        log.info("index.md not found — running without corpus context.")

    # ── Step 2: Article note ──────────────────────────────────────────────────
    log.info("Claude call 1 — article note …")
    try:
        resp1 = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=ARTICLE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_article_prompt(content, corpus_block)}],
        )
        article_md = resp1.content[0].text.strip()
    except Exception as e:
        log.error(f"Claude API error (article): {e}")
        return False

    article_title = extract_title(article_md)
    article_slug  = slugify(article_title)
    article_fm, article_body = parse_frontmatter(article_md)
    if article_fm:
        article_fm["date"] = datetime.now().strftime("%Y-%m-%d")

    # ── Step 3: Investment note ───────────────────────────────────────────────
    invest_md    = None
    invest_fm: dict = {}
    invest_body  = ""
    invest_slug  = ""
    if run_investment:
        log.info("Claude call 2 — investment note …")
        try:
            resp2 = client.messages.create(
                model=MODEL,
                max_tokens=2048,
                system=INVESTMENT_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_investment_prompt(content, article_title, corpus_block)}],
            )
            invest_md    = resp2.content[0].text.strip()
            invest_title = extract_title(invest_md)
            invest_slug  = slugify(invest_title)
            invest_fm, invest_body = parse_frontmatter(invest_md)
            if invest_fm:
                invest_fm["date"] = datetime.now().strftime("%Y-%m-%d")
        except Exception as e:
            log.warning(
                f"Claude API error (investment note): {e} — "
                "article note will be saved without cross-link."
            )
    else:
        log.info("Claude call 2 — skipped (relevance gate)")

    # ── Step 3: Cross-link injection ──────────────────────────────────────────
    if invest_md is not None:
        if article_fm:
            article_fm["related"] = [f"[[Knowledge/{invest_slug}]]"]
        if invest_fm:
            invest_fm["related"] = [f"[[Sources/{article_slug}]]"]
        article_body = inject_related_link(article_body, f"Knowledge/{invest_slug}")
        invest_body  = inject_related_link(invest_body,  f"Sources/{article_slug}")

    # ── Step 4: Render and write ──────────────────────────────────────────────
    final_article = render_note(article_fm, article_body) if article_fm else article_md
    try:
        article_path = _safe_write(final_article, ARTICLES_DIR, article_slug)
        log.info(f"✓ Article note:    {article_path.name}")
    except Exception as e:
        log.error(f"Could not write article note: {e}")
        return False

    if invest_md is not None:
        if invest_fm:
            _INVEST_FIELD_ORDER = ["title", "tickers", "tags", "date", "source", "related"]
            invest_fm = {k: invest_fm[k] for k in _INVEST_FIELD_ORDER if k in invest_fm} | \
                        {k: v for k, v in invest_fm.items() if k not in _INVEST_FIELD_ORDER}
        final_invest = render_note(invest_fm, invest_body) if invest_fm else invest_md
        try:
            invest_path = _safe_write(final_invest, INVESTMENT_NOTES_DIR, invest_slug)
            log.info(f"✓ Investment note: {invest_path.name}")
        except Exception as e:
            log.warning(f"Could not write investment note: {e}")

    # ── Step 5: Entity Note update ────────────────────────────────────────────
    if invest_md is not None and invest_fm:
        tickers = invest_fm.get("tickers") or []
        if isinstance(tickers, str):
            tickers = [tickers]
        tickers = [str(t).strip().upper() for t in tickers if str(t).strip()]
        if tickers:
            try:
                result = subprocess.run(
                    [sys.executable, str(ENTITY_MANAGER), "--tickers"] + tickers,
                    capture_output=True, text=True,
                    env={**os.environ, "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", "")},
                )
                for line in result.stdout.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("[CREATED]"):
                        log.info(f"✓ Entity note created: {stripped[len('[CREATED]'):].strip()}")
                    elif stripped.startswith("[UPDATED]"):
                        log.info(f"✓ Entity note updated: {stripped[len('[UPDATED]'):].strip()}")
                    elif stripped.startswith("[WARN]") or stripped.startswith("[SKIP]"):
                        log.warning(f"Entity manager: {stripped}")
                if result.returncode != 0:
                    log.warning(f"entity_note_manager exited {result.returncode}: {result.stderr.strip()}")
            except Exception as e:
                log.warning(f"Could not run entity_note_manager: {e}")
        else:
            log.info("Investment note has no tickers — skipping entity note update")

    # ── Step 7: Back-link update ──────────────────────────────────────────────
    if loaded_notes:
        update_backlinks(loaded_notes, article_slug, ARTICLES_DIR)

    # ── Step 8–9: Log + index (always, even if investment note failed) ────────
    date    = article_fm.get("date", datetime.now().strftime("%Y-%m-%d")) if article_fm else datetime.now().strftime("%Y-%m-%d")
    tags    = article_fm.get("tags", []) if article_fm else []
    summary = extract_context(article_body)

    append_log(INGEST_LOG, article_title, tags, date, article_slug)
    try:
        upsert_index(CORPUS_INDEX, article_slug, article_title, summary, tags, date)
    except Exception as e:
        log.warning(f"Could not update index.md: {e}")

    _move_to_processed(path)
    return True


def relevance_gate(content: str, client: anthropic.Anthropic) -> tuple:
    """
    Ask Claude Sonnet whether the content has meaningful investment implications.
    Returns (relevant: bool, reason: str).
    Fails open — if the API call or JSON parse fails, returns (True, "gate error").
    """
    prompt = (
        "Determine whether the following content has meaningful investment implications "
        "(i.e. it discusses companies, markets, economic trends, assets, valuations, "
        "risks, or anything a serious investor would track).\n\n"
        "Return a JSON object only — no preamble, no explanation:\n"
        '{"relevant": true, "reason": "...", "tickers": ["TICK", "TICK"]}\n\n'
        f"CONTENT:\n{content}"
    )
    try:
        resp = client.messages.create(
            model=GATE_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        # Strip markdown code fences if Claude wraps the JSON
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        data = json.loads(raw)
        relevant = bool(data.get("relevant", True))
        reason   = str(data.get("reason", ""))
        return relevant, reason
    except Exception as e:
        log.warning(f"Relevance gate error ({e}) — defaulting to relevant=True")
        return True, "gate error"


class _TextExtractor(HTMLParser):
    """Collect visible text from HTML, skipping script and style tags."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip  = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag.lower() in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in ("script", "style"):
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            text = data.strip()
            if text:
                self._parts.append(text)

    def get_text(self) -> str:
        return "\n".join(self._parts)


def fetch_url(url: str) -> Optional[str]:
    """
    Fetch a URL and return its visible text content using only stdlib.
    Returns None on any error, with a descriptive log message explaining why.
    """
    _HTTP_REASONS = {
        401: "authentication required",
        403: "likely paywalled or bot-protected",
        404: "page not found",
        410: "page permanently removed",
        429: "rate limited",
        500: "server error",
        503: "site unavailable",
    }
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            charset   = resp.headers.get_content_charset() or "utf-8"
            raw_bytes = resp.read()

        html = raw_bytes.decode(charset, errors="replace")
        parser = _TextExtractor()
        parser.feed(html)
        return parser.get_text() or None

    except urllib.error.HTTPError as exc:
        reason = _HTTP_REASONS.get(exc.code, "unexpected HTTP error")
        log.warning(f"Fetch failed: {exc.code} {reason} — drop article text manually.")
        return None
    except urllib.error.URLError as exc:
        reason_str = str(exc.reason).lower()
        if "timed out" in reason_str or "timeout" in reason_str:
            log.warning("Fetch failed: timeout — site too slow or blocking — drop article text manually.")
        else:
            log.warning(f"Fetch failed: connection error ({exc.reason}) — drop article text manually.")
        return None
    except TimeoutError:
        log.warning("Fetch failed: timeout — site too slow or blocking — drop article text manually.")
        return None
    except Exception as exc:
        log.warning(f"Fetch failed: {type(exc).__name__} — drop article text manually.")
        return None


def detect_content_type(content: str) -> str:
    """
    Classify the raw content of a dropped file.

    Returns:
        "youtube"  — content is a bare YouTube URL (no processable text)
        "url"      — content is a bare URL (non-YouTube)
        "text"     — processable text content (articles, notes, transcripts, etc.)

    A file is "bare URL" only when the entire meaningful content is a single URL
    with no surrounding text of substance. URLs embedded within larger text are
    treated as "text" and passed through normally.
    """
    stripped = content.strip()
    lines    = [l.strip() for l in stripped.splitlines() if l.strip()]

    if len(lines) == 1:
        line = lines[0]
        if re.match(r"https?://", line, re.IGNORECASE):
            if re.search(r"(youtube\.com/watch|youtu\.be/)", line, re.IGNORECASE):
                return "youtube"
            return "url"

    return "text"


def _move_to_processed(path: Path) -> None:
    PROCESSED.mkdir(exist_ok=True)
    dest = PROCESSED / path.name
    if dest.exists():
        dest = PROCESSED / f"{path.stem}-{datetime.now().strftime('%Y%m%d%H%M%S')}{path.suffix}"
    try:
        shutil.move(str(path), str(dest))
        log.info(f"  Moved source to _processed/")
    except Exception as e:
        log.warning(f"Could not move source file: {e}")

# ─── HTTP Ingest Listener ─────────────────────────────────────────────────────
#
# Accepts POST requests from the BASECAMP browser extension.
# Binds to 127.0.0.1 only — never exposed to the network.
#
# Endpoints:
#   POST /ingest      {text, title, source_url}  → writes .txt to Drop Zone
#   POST /ingest-yt   {url}                       → writes .md to YT Drop Zone
#   GET  /status                                  → {"running": true, ...}
#   OPTIONS *                                     → CORS preflight

_CORS_HEADERS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


class _IngestHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        log.debug(f"HTTP {self.address_string()} — {fmt % args}")

    def _send_cors(self):
        for k, v in _CORS_HEADERS.items():
            self.send_header(k, v)

    def _send_json(self, status: int, body: dict):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._send_cors()
        self.end_headers()
        self.wfile.write(payload)

    def _read_json(self) -> Optional[dict]:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return None
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"Invalid JSON: {exc}") from exc

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/status":
            self._send_json(200, {"running": True, "watcher": "v3.1", "port": HTTP_PORT})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        try:
            body = self._read_json()
        except ValueError as exc:
            log.warning(f"HTTP ingest: bad request body — {exc}")
            self._send_json(400, {"error": str(exc)})
            return

        if self.path == "/ingest":
            self._handle_article(body)
        elif self.path == "/ingest-yt":
            self._handle_youtube(body)
        else:
            self._send_json(404, {"error": "not found"})

    def _handle_article(self, body: Optional[dict]):
        if not body:
            self._send_json(400, {"error": "empty body"})
            return
        text = (body.get("text") or "").strip()
        if not text:
            self._send_json(400, {"error": "'text' field is required and must not be empty"})
            return
        title      = str(body.get("title") or "").strip()
        source_url = str(body.get("source_url") or "").strip()

        slug    = slugify(title) if title else f"capture-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        content = f"Source: {source_url}\n\n{text}" if source_url else text
        dest    = DROP_ZONE / f"{slug}.txt"

        try:
            _atomic_write(dest, content)
            log.info(
                f"HTTP ingest: wrote {dest.name} to Drop Zone "
                f"({len(text):,} chars — {source_url or 'no url'})"
            )
            self._send_json(200, {"ok": True, "filename": dest.name})
        except OSError as exc:
            log.error(f"HTTP ingest: file write failed — {exc}")
            self._send_json(500, {"error": str(exc)})

    def _handle_youtube(self, body: Optional[dict]):
        if not body:
            self._send_json(400, {"error": "empty body"})
            return
        url = (body.get("url") or "").strip()
        if not url:
            self._send_json(400, {"error": "'url' field is required"})
            return
        if not re.search(r"(youtube\.com/watch|youtu\.be/)", url, re.IGNORECASE):
            self._send_json(400, {"error": "url does not appear to be a YouTube URL"})
            return

        YT_DROP_ZONE.mkdir(exist_ok=True)
        dest = YT_DROP_ZONE / f"yt-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
        try:
            _atomic_write(dest, url)
            log.info(f"HTTP ingest-yt: wrote {dest.name} to YT Drop Zone ({url})")
            self._send_json(200, {"ok": True, "filename": dest.name})
        except OSError as exc:
            log.error(f"HTTP ingest-yt: file write failed — {exc}")
            self._send_json(500, {"error": str(exc)})


def start_http_listener(port: int = HTTP_PORT) -> None:
    """
    Start the HTTP ingest listener on 127.0.0.1:{port} in a daemon thread.
    Daemon=True means it exits automatically when the main process exits.
    Crashes are logged; the main polling loop is never affected.
    """
    def _run():
        try:
            class _Server(socketserver.TCPServer):
                allow_reuse_address = True  # avoids "address in use" on restart

            server = _Server(("127.0.0.1", port), _IngestHandler)
            log.info(f"HTTP ingest listener started — http://127.0.0.1:{port}")
            server.serve_forever()
        except OSError as exc:
            log.error(f"HTTP listener could not start on port {port}: {exc}")
        except Exception as exc:
            log.error(f"HTTP listener crashed: {exc}")

    threading.Thread(target=_run, name="http-ingest", daemon=True).start()


# ─── Main loop ─────────────────────────────────────────────────────────────────

def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY environment variable not set. Exiting.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    for d in (DROP_ZONE, YT_DROP_ZONE, ARTICLES_DIR, INVESTMENT_NOTES_DIR, PROCESSED):
        d.mkdir(parents=True, exist_ok=True)

    start_http_listener()

    log.info("─" * 60)
    log.info("Drop Zone Watcher — v3.1")
    log.info(f"Watching: {DROP_ZONE}")
    log.info(f"Articles: {ARTICLES_DIR}")
    log.info(f"Invest.:  {INVESTMENT_NOTES_DIR}")
    log.info(f"Log:      {INGEST_LOG}")
    log.info(f"Index:    {CORPUS_INDEX}")
    log.info(f"Model:    {MODEL}")
    log.info("Drop any text file to process it. Ctrl+C to stop.")
    log.info("─" * 60)

    seen: set[str] = set()

    while True:
        try:
            txt_files = [
                f for f in DROP_ZONE.iterdir()
                if f.is_file()
                and not f.name.startswith(".")
                and f.suffix.lower() != ".tmp"
                and f.name not in seen
            ]

            for f in txt_files:
                success = process_file(f, client)
                if success:
                    seen.add(f.name)  # only mark seen after successful processing

            time.sleep(POLL_SECS)

        except KeyboardInterrupt:
            log.info("Watcher stopped.")
            break
        except Exception as e:
            log.error(f"Unexpected error: {e}")
            time.sleep(POLL_SECS)


if __name__ == "__main__":
    main()
