"""
Tests for dropzone_watcher.py — Phase 1 + Phase 2 + Phase 3.

Covers:
  - parse_frontmatter: happy path, malformed YAML, missing delimiters, edge cases
  - render_note: roundtrip consistency, empty frontmatter
  - inject_related_link: replaces placeholder, preserves trailing ---, appends if missing
  - cross-link injection: both frontmatter fields populated correctly
  - slugify / extract_title: regression guards
  - Phase 3: detect_source_tags, parse_index, parse_log_recency, rank_notes,
             load_corpus, build_corpus_block, add_related_link, update_backlinks,
             build_article_prompt / build_investment_prompt corpus_block param,
             process_file() graceful-degrade integration test

Run: pytest test_dropzone_watcher.py -v
"""

import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from dropzone_watcher import (
    # Phase 1
    parse_frontmatter,
    render_note,
    inject_related_link,
    slugify,
    extract_title,
    # Phase 2
    extract_context,
    find_domain_tag,
    append_log,
    upsert_index,
    DOMAIN_TAGS,
    # Phase 3
    detect_source_tags,
    parse_index,
    parse_log_recency,
    rank_notes,
    load_corpus,
    build_corpus_block,
    add_related_link,
    update_backlinks,
    build_article_prompt,
    build_investment_prompt,
    process_file,
    ARTICLES_DIR,
    CORPUS_INDEX,
    INGEST_LOG,
)

# ─── Fixtures ─────────────────────────────────────────────────────────────────

VALID_NOTE = """\
---
tags:
- finance
- technology
date: '2026-04-04'
source: Bloomberg
---

# Fed Holds Rates Steady Amid Tech Sector Volatility

## Key Takeaways
- Point one
- Point two
"""

MALFORMED_YAML = """\
---
tags: [unclosed
date: 2026-04-04
---

# Title
"""

NO_FRONTMATTER = """\
# Just a title

Some body text.
"""

UNCLOSED_BLOCK = """\
---
tags: [finance]

# Title with no closing delimiter
"""

BODY_WITH_RELATED = """\
## Key Takeaways
- Point one

## Related
- [[]] ← leave blank, to be filled in Obsidian

---"""

BODY_WITH_RELATED_AND_NEXT_SECTION = """\
## Key Takeaways
- Point one

## Related
- [[]] ← leave blank

## Appendix
- extra
"""

BODY_WITHOUT_RELATED = """\
## Key Takeaways
- Point one
"""

# ─── parse_frontmatter ────────────────────────────────────────────────────────

def test_parse_frontmatter_valid():
    fm, body = parse_frontmatter(VALID_NOTE)
    assert fm["tags"] == ["finance", "technology"]
    assert fm["date"] == "2026-04-04"
    assert fm["source"] == "Bloomberg"
    assert "# Fed Holds Rates Steady" in body
    assert "---" not in body.splitlines()[0]  # frontmatter stripped


def test_parse_frontmatter_malformed_yaml():
    fm, body = parse_frontmatter(MALFORMED_YAML)
    assert fm == {}
    assert body == MALFORMED_YAML  # raw fallback, untouched


def test_parse_frontmatter_no_delimiters():
    fm, body = parse_frontmatter(NO_FRONTMATTER)
    assert fm == {}
    assert body == NO_FRONTMATTER


def test_parse_frontmatter_unclosed_block():
    fm, body = parse_frontmatter(UNCLOSED_BLOCK)
    assert fm == {}
    assert body == UNCLOSED_BLOCK


def test_parse_frontmatter_empty_frontmatter():
    md = "---\n---\n\n# Title"
    fm, body = parse_frontmatter(md)
    assert fm == {}
    assert "# Title" in body


def test_parse_frontmatter_strips_body_leading_newlines():
    md = "---\ntags: [finance]\n---\n\n\n# Title"
    fm, body = parse_frontmatter(md)
    assert body.startswith("# Title")


# ─── render_note ──────────────────────────────────────────────────────────────

def test_render_note_roundtrip():
    fm, body = parse_frontmatter(VALID_NOTE)
    rendered = render_note(fm, body)
    fm2, body2 = parse_frontmatter(rendered)
    assert fm2["tags"] == fm["tags"]
    assert fm2["date"] == fm["date"]
    assert body2.strip() == body.strip()


def test_render_note_empty_fm_returns_body():
    result = render_note({}, "just body text")
    assert result == "just body text"


def test_render_note_produces_valid_yaml_delimiter():
    fm = {"tags": ["investing"], "date": "2026-04-04"}
    result = render_note(fm, "## Body")
    lines = result.splitlines()
    assert lines[0] == "---"
    # second --- closes the YAML block
    assert "---" in lines[1:]


# ─── inject_related_link ──────────────────────────────────────────────────────

def test_inject_related_replaces_placeholder():
    result = inject_related_link(BODY_WITH_RELATED, "Investment Notes/Fed-Rate-Hold-Investment")
    assert "- [[Investment Notes/Fed-Rate-Hold-Investment]]" in result
    assert "← leave blank" not in result


def test_inject_related_preserves_trailing_separator():
    result = inject_related_link(BODY_WITH_RELATED, "Investment Notes/Some-Note")
    # The trailing --- horizontal rule must be preserved
    assert result.strip().endswith("---")


def test_inject_related_stops_at_next_section():
    result = inject_related_link(BODY_WITH_RELATED_AND_NEXT_SECTION, "Investment Notes/X")
    assert "## Appendix" in result
    assert "- extra" in result
    assert "← leave blank" not in result


def test_inject_related_appends_if_missing():
    result = inject_related_link(BODY_WITHOUT_RELATED, "Articles/Some-Article")
    assert "## Related" in result
    assert "- [[Articles/Some-Article]]" in result


# ─── cross-link injection integration ─────────────────────────────────────────

def test_cross_link_article_frontmatter_points_to_investment():
    article_slug = "Fed-Rate-Hold"
    invest_slug  = "Fed-Rate-Hold-Investment-Angle"

    fm, body = parse_frontmatter(VALID_NOTE)
    fm["related"] = [f"[[Investment Notes/{invest_slug}]]"]
    rendered = render_note(fm, body)

    fm2, _ = parse_frontmatter(rendered)
    assert fm2["related"] == ["[[Investment Notes/Fed-Rate-Hold-Investment-Angle]]"]


def test_cross_link_invest_frontmatter_points_to_article():
    article_slug = "Fed-Rate-Hold"

    fm = {"tags": ["investing"], "date": "2026-04-04"}
    fm["related"] = [f"[[Articles/{article_slug}]]"]
    rendered = render_note(fm, "## Body")

    fm2, _ = parse_frontmatter(rendered)
    assert fm2["related"] == ["[[Articles/Fed-Rate-Hold]]"]


def test_cross_link_body_article_contains_invest_link():
    invest_slug = "Fed-Rate-Hold-Investment-Angle"
    result = inject_related_link(BODY_WITH_RELATED, f"Investment Notes/{invest_slug}")
    assert f"[[Investment Notes/{invest_slug}]]" in result


def test_cross_link_body_invest_contains_article_link():
    article_slug = "Fed-Rate-Hold"
    result = inject_related_link(BODY_WITH_RELATED, f"Articles/{article_slug}")
    assert f"[[Articles/{article_slug}]]" in result


# ─── slugify (regression) ─────────────────────────────────────────────────────

def test_slugify_replaces_spaces():
    assert slugify("Fed Holds Rates") == "Fed-Holds-Rates"


def test_slugify_strips_special_chars():
    # colon and other punctuation are removed
    result = slugify("Fed Holds Rates: A New Era")
    assert ":" not in result
    assert result == "Fed-Holds-Rates-A-New-Era"


def test_slugify_caps_at_80_chars():
    long_title = "A" * 100
    assert len(slugify(long_title)) <= 80


# ─── extract_title (regression) ───────────────────────────────────────────────

def test_extract_title_finds_h1():
    md = "---\ntags: []\n---\n\n# My Title\n\n## Section"
    assert extract_title(md) == "My Title"


def test_extract_title_ignores_h2():
    md = "## Not a title\n# Real Title"
    assert extract_title(md) == "Real Title"


def test_extract_title_fallback_when_no_h1():
    title = extract_title("no heading here")
    assert title.startswith("note-")


# ─── extract_context ──────────────────────────────────────────────────────────

BODY_WITH_CONTEXT = """\
## Key Takeaways
- Point one

## Context
The Fed held rates steady for the third consecutive meeting. Markets interpreted this as a signal of caution.

## So What
Equities rallied on the news.
"""

BODY_CONTEXT_EMPTY = """\
## Key Takeaways
- Point one

## Context

## So What
Something.
"""

BODY_NO_CONTEXT = """\
## Key Takeaways
- Point one

## So What
Something.
"""

BODY_CONTEXT_SINGLE_SENTENCE = """\
## Context
Only one sentence here.
"""


def test_extract_context_returns_first_sentence():
    result = extract_context(BODY_WITH_CONTEXT)
    assert result == "The Fed held rates steady for the third consecutive meeting"


def test_extract_context_empty_section_returns_empty():
    result = extract_context(BODY_CONTEXT_EMPTY)
    assert result == ""


def test_extract_context_missing_section_returns_empty():
    result = extract_context(BODY_NO_CONTEXT)
    assert result == ""


def test_extract_context_single_sentence():
    result = extract_context(BODY_CONTEXT_SINGLE_SENTENCE)
    assert result == "Only one sentence here"


def test_extract_context_stops_at_next_section():
    body = "## Context\nFirst sentence. Second sentence.\n\n## So What\nFoo."
    result = extract_context(body)
    assert result == "First sentence"


# ─── find_domain_tag ──────────────────────────────────────────────────────────

def test_find_domain_tag_returns_first_match():
    assert find_domain_tag(["ai", "finance", "rates"]) == "finance"


def test_find_domain_tag_returns_other_when_no_match():
    assert find_domain_tag(["ai", "supply-chain", "semiconductors"]) == "other"


def test_find_domain_tag_empty_list():
    assert find_domain_tag([]) == "other"


def test_find_domain_tag_none():
    assert find_domain_tag(None) == "other"


def test_find_domain_tag_all_known_domains_recognized():
    for domain in ["finance", "technology", "markets", "strategy", "policy", "macro", "investing"]:
        assert find_domain_tag([domain]) == domain


# ─── append_log ───────────────────────────────────────────────────────────────

def test_append_log_creates_file_if_missing(tmp_path):
    log_path = tmp_path / "log.md"
    append_log(log_path, "My Title", ["finance", "ai"], "2026-04-04", "My-Title")
    assert log_path.exists()
    content = log_path.read_text()
    assert "## [2026-04-04] ingest | My Title | finance, ai" in content


def test_append_log_appends_to_existing(tmp_path):
    log_path = tmp_path / "log.md"
    log_path.write_text("## [2026-04-03] ingest | Old Note | macro\n")
    append_log(log_path, "New Note", ["finance"], "2026-04-04", "New-Note")
    lines = log_path.read_text().splitlines()
    assert len(lines) == 2
    assert "Old Note" in lines[0]
    assert "New Note" in lines[1]


def test_append_log_skips_duplicate_title(tmp_path):
    log_path = tmp_path / "log.md"
    append_log(log_path, "My Title", ["finance"], "2026-04-04", "My-Title")
    append_log(log_path, "My Title", ["finance"], "2026-04-04", "My-Title")
    lines = [l for l in log_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 1


def test_append_log_correct_format(tmp_path):
    log_path = tmp_path / "log.md"
    append_log(log_path, "Fed Rate Decision", ["finance", "macro"], "2026-04-04", "Fed-Rate-Decision")
    content = log_path.read_text()
    assert content.startswith("## [2026-04-04] ingest | Fed Rate Decision | finance, macro")


# ─── upsert_index ─────────────────────────────────────────────────────────────

def test_upsert_index_creates_file_with_domain_header(tmp_path):
    idx = tmp_path / "index.md"
    upsert_index(idx, "Fed-Rate-Hold", "Fed Rate Hold", "The Fed held rates.", ["finance"], "2026-04-04")
    content = idx.read_text()
    assert "## finance" in content
    assert "[[Articles/Fed-Rate-Hold|Fed Rate Hold]]" in content
    assert "The Fed held rates" in content
    assert "2026-04-04" in content


def test_upsert_index_inserts_under_existing_domain(tmp_path):
    idx = tmp_path / "index.md"
    idx.write_text("## finance\n- [[Articles/Old-Note|Old Note]] — summary — finance — 2026-04-01\n")
    upsert_index(idx, "New-Note", "New Note", "Summary.", ["finance"], "2026-04-04")
    lines = idx.read_text().splitlines()
    # Both entries should be under ## finance
    assert any("Old-Note" in l for l in lines)
    assert any("New-Note" in l for l in lines)
    header_idx = next(i for i, l in enumerate(lines) if l == "## finance")
    old_idx = next(i for i, l in enumerate(lines) if "Old-Note" in l)
    new_idx = next(i for i, l in enumerate(lines) if "New-Note" in l)
    assert header_idx < old_idx < new_idx  # new entry appended after existing


def test_upsert_index_creates_new_domain_section(tmp_path):
    idx = tmp_path / "index.md"
    idx.write_text("## finance\n- [[Articles/Old-Note|Old Note]] — summary — finance — 2026-04-01\n")
    upsert_index(idx, "AI-Note", "AI Note", "About AI.", ["technology"], "2026-04-04")
    content = idx.read_text()
    assert "## finance" in content
    assert "## technology" in content
    assert "AI-Note" in content


def test_upsert_index_updates_existing_entry_in_place(tmp_path):
    idx = tmp_path / "index.md"
    idx.write_text("## finance\n- [[Articles/Fed-Rate-Hold|Old Title]] — old summary — finance — 2026-04-01\n")
    upsert_index(idx, "Fed-Rate-Hold", "New Title", "New summary.", ["finance"], "2026-04-04")
    content = idx.read_text()
    assert "Old Title" not in content
    assert "New Title" in content
    assert "New summary" in content
    # Only one entry for this slug
    assert content.count("Fed-Rate-Hold") == 1


def test_upsert_index_other_domain_fallback(tmp_path):
    idx = tmp_path / "index.md"
    upsert_index(idx, "Some-Note", "Some Note", "Summary.", ["ai", "chips"], "2026-04-04")
    content = idx.read_text()
    assert "## other" in content


def test_upsert_index_uses_atomic_write(tmp_path):
    """Verify no .tmp file is left behind after a successful write."""
    idx = tmp_path / "index.md"
    upsert_index(idx, "Test-Note", "Test Note", "Summary.", ["finance"], "2026-04-04")
    tmp = idx.with_suffix(".tmp")
    assert not tmp.exists()


def test_upsert_index_entry_format(tmp_path):
    idx = tmp_path / "index.md"
    upsert_index(idx, "My-Slug", "My Title", "My summary", ["markets", "macro"], "2026-04-04")
    content = idx.read_text()
    expected = "- [[Articles/My-Slug|My Title]] — My summary — markets, macro — 2026-04-04"
    assert expected in content


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 3 tests
# ═══════════════════════════════════════════════════════════════════════════════

# ─── detect_source_tags ───────────────────────────────────────────────────────

def test_detect_source_tags_returns_matched_tags():
    # Content with strong finance signal (federal reserve, interest rate, inflation, bond)
    content = "The Federal Reserve held interest rates steady. Bond yields fell and inflation eased."
    tags = detect_source_tags(content)
    assert "finance" in tags


def test_detect_source_tags_ranks_by_hit_count():
    # Load with finance keywords — should rank finance first
    content = ("The Federal Reserve raised interest rates. Bond yields rose. "
               "Bank credit tightened. Treasury yields hit 5%. Inflation persisted.")
    tags = detect_source_tags(content)
    assert tags[0] in ("finance", "macro")  # heavy finance/macro language


def test_detect_source_tags_empty_content():
    assert detect_source_tags("") == []


def test_detect_source_tags_no_keyword_match():
    assert detect_source_tags("A recipe for chocolate cake with vanilla frosting.") == []


def test_detect_source_tags_returns_only_known_tags():
    tags = detect_source_tags("AI chips semiconductors cloud SaaS platform startup tech compute.")
    for tag in tags:
        assert tag in DOMAIN_TAGS or tag in {"technology"}  # all known tags


# ─── parse_index ──────────────────────────────────────────────────────────────

SAMPLE_INDEX = """\
## finance
- [[Articles/Fed-Rate-Hold|Fed Rate Hold]] — The Fed held rates steady. — finance, macro — 2026-04-04
- [[Articles/Bank-Credit-Crunch|Bank Credit Crunch]] — Credit tightened sharply. — finance, credit — 2026-03-15

## technology
- [[Articles/AI-Chip-Shortage|AI Chip Shortage]] — Demand for AI chips outpaced supply. — technology, ai — 2026-04-01
"""

SAMPLE_INDEX_WITH_EMDASH_IN_SUMMARY = """\
## finance
- [[Articles/Complex-Note|Complex Note]] — Summary with — em dash inside — finance — 2026-04-04
"""


def test_parse_index_valid_entries():
    records = parse_index(SAMPLE_INDEX)
    assert len(records) == 3
    slugs = [r["slug"] for r in records]
    assert "Fed-Rate-Hold" in slugs
    assert "Bank-Credit-Crunch" in slugs
    assert "AI-Chip-Shortage" in slugs


def test_parse_index_extracts_tags_as_list():
    records = parse_index(SAMPLE_INDEX)
    fed = next(r for r in records if r["slug"] == "Fed-Rate-Hold")
    assert fed["tags"] == ["finance", "macro"]
    assert fed["date"] == "2026-04-04"
    assert fed["title"] == "Fed Rate Hold"


def test_parse_index_emdash_in_summary():
    """rsplit from right must handle em dashes inside summary text."""
    records = parse_index(SAMPLE_INDEX_WITH_EMDASH_IN_SUMMARY)
    assert len(records) == 1
    assert records[0]["slug"] == "Complex-Note"
    assert records[0]["tags"] == ["finance"]
    assert records[0]["date"] == "2026-04-04"
    # Summary contains the em dash
    assert "—" in records[0]["summary"] or "em dash" in records[0]["summary"]


def test_parse_index_skips_malformed_lines():
    bad = "## finance\nnot a wikilink line\n- [[Articles/Good-Note|Good]] — s — finance — 2026-04-04\n"
    records = parse_index(bad)
    assert len(records) == 1
    assert records[0]["slug"] == "Good-Note"


def test_parse_index_empty():
    assert parse_index("") == []


def test_parse_index_skips_header_lines():
    records = parse_index("## finance\n## technology\n")
    assert records == []


# ─── parse_log_recency ────────────────────────────────────────────────────────

SAMPLE_LOG = """\
## [2026-03-15] ingest | Bank Credit Crunch | finance, credit
## [2026-04-01] ingest | AI Chip Shortage | technology, ai
## [2026-04-04] ingest | Fed Rate Hold | finance, macro
"""


def test_parse_log_recency_returns_title_date_map():
    recency = parse_log_recency(SAMPLE_LOG)
    assert recency["Fed Rate Hold"] == "2026-04-04"
    assert recency["AI Chip Shortage"] == "2026-04-01"
    assert recency["Bank Credit Crunch"] == "2026-03-15"


def test_parse_log_recency_empty():
    assert parse_log_recency("") == {}


def test_parse_log_recency_skips_malformed():
    log_text = "## [2026-04-04] ingest | Good Title | tags\nnot a log line\n"
    recency = parse_log_recency(log_text)
    assert "Good Title" in recency
    assert len(recency) == 1


def test_parse_log_recency_later_entry_wins():
    """If same title appears twice, the later date should win."""
    log_text = (
        "## [2026-03-01] ingest | Repeat Note | finance\n"
        "## [2026-04-01] ingest | Repeat Note | finance\n"
    )
    recency = parse_log_recency(log_text)
    assert recency["Repeat Note"] == "2026-04-01"


# ─── rank_notes ───────────────────────────────────────────────────────────────

RECORDS = [
    {"slug": "A", "title": "A", "tags": ["finance", "macro"], "date": "2026-04-01"},
    {"slug": "B", "title": "B", "tags": ["finance", "credit"], "date": "2026-04-03"},
    {"slug": "C", "title": "C", "tags": ["technology", "ai"],  "date": "2026-04-04"},
    {"slug": "D", "title": "D", "tags": ["macro"],             "date": "2026-03-01"},
]
LOG_RECENCY = {"A": "2026-04-01", "B": "2026-04-03", "C": "2026-04-04", "D": "2026-03-01"}


def test_rank_notes_domain_match_first():
    # source is finance domain → finance notes rank above non-finance
    ranked = rank_notes(RECORDS, ["finance", "macro"], LOG_RECENCY)
    slugs = [r["slug"] for r in ranked]
    # A and B are finance domain; D is macro only (no domain match)
    assert slugs.index("A") < slugs.index("D") or "D" not in slugs
    assert all(r["slug"] in ("A", "B", "D") for r in ranked)  # C has no overlap
    assert "C" not in slugs  # technology/ai has no overlap with finance/macro source


def test_rank_notes_more_overlap_ranks_higher():
    # A has 2 overlapping tags (finance, macro), D has 1 (macro) — A should rank higher
    ranked = rank_notes(RECORDS, ["finance", "macro"], LOG_RECENCY)
    slugs = [r["slug"] for r in ranked]
    assert slugs.index("A") < slugs.index("D")


def test_rank_notes_recency_tiebreak():
    # B (finance, 2026-04-03) vs A (finance+macro, 2026-04-01) —
    # B has fewer overlaps but both are finance domain; tie on domain, A wins on overlap
    records_tied = [
        {"slug": "X", "title": "X", "tags": ["finance"], "date": "2026-03-01"},
        {"slug": "Y", "title": "Y", "tags": ["finance"], "date": "2026-04-01"},
    ]
    recency = {"X": "2026-03-01", "Y": "2026-04-01"}
    ranked = rank_notes(records_tied, ["finance"], recency)
    assert ranked[0]["slug"] == "Y"  # more recent wins the tiebreak


def test_rank_notes_no_overlap_returns_empty():
    ranked = rank_notes(RECORDS, ["geopolitics"], LOG_RECENCY)
    assert ranked == []


def test_rank_notes_empty_records():
    assert rank_notes([], ["finance"], {}) == []


# ─── load_corpus ──────────────────────────────────────────────────────────────

def test_load_corpus_loads_matching_files(tmp_path):
    (tmp_path / "Note-A.md").write_text("Content of Note A", encoding="utf-8")
    (tmp_path / "Note-B.md").write_text("Content of Note B", encoding="utf-8")
    records = [
        {"slug": "Note-A", "title": "Note A", "tags": ["finance"], "date": "2026-04-04"},
        {"slug": "Note-B", "title": "Note B", "tags": ["finance"], "date": "2026-04-03"},
    ]
    loaded = load_corpus(records, tmp_path, char_budget=10_000)
    assert len(loaded) == 2
    assert loaded[0]["text"] == "Content of Note A"


def test_load_corpus_skips_missing_files(tmp_path):
    records = [{"slug": "Missing-Note", "title": "Missing", "tags": [], "date": "2026-04-04"}]
    loaded = load_corpus(records, tmp_path, char_budget=10_000)
    assert loaded == []


def test_load_corpus_budget_skip_and_continue(tmp_path):
    """Skip a note that exceeds budget, include a smaller one that fits."""
    (tmp_path / "Big.md").write_text("B" * 5000, encoding="utf-8")
    (tmp_path / "Small.md").write_text("S" * 100, encoding="utf-8")
    records = [
        {"slug": "Big",   "title": "Big",   "tags": [], "date": "2026-04-04"},
        {"slug": "Small", "title": "Small", "tags": [], "date": "2026-04-03"},
    ]
    loaded = load_corpus(records, tmp_path, char_budget=2_000)
    slugs = [r["slug"] for r in loaded]
    assert "Big" not in slugs
    assert "Small" in slugs


def test_load_corpus_empty_records():
    loaded = load_corpus([], Path("/nonexistent"), char_budget=40_000)
    assert loaded == []


def test_load_corpus_respects_max_notes(tmp_path):
    for i in range(5):
        (tmp_path / f"Note-{i}.md").write_text(f"Content {i}", encoding="utf-8")
    records = [
        {"slug": f"Note-{i}", "title": f"Note {i}", "tags": [], "date": "2026-04-04"}
        for i in range(5)
    ]
    loaded = load_corpus(records, tmp_path, char_budget=100_000, max_notes=3)
    assert len(loaded) == 3


# ─── build_corpus_block ───────────────────────────────────────────────────────

def test_build_corpus_block_non_empty():
    notes = [{"title": "Fed Rate Hold", "text": "Note content here."}]
    block = build_corpus_block(notes)
    assert "EXISTING VAULT NOTES" in block
    assert "Fed Rate Hold" in block
    assert "Note content here." in block


def test_build_corpus_block_empty_returns_empty_string():
    assert build_corpus_block([]) == ""


def test_build_corpus_block_multiple_notes_separated():
    notes = [
        {"title": "Note A", "text": "Content A"},
        {"title": "Note B", "text": "Content B"},
    ]
    block = build_corpus_block(notes)
    assert "Note A" in block
    assert "Note B" in block
    assert block.index("Note A") < block.index("Note B")


# ─── add_related_link ─────────────────────────────────────────────────────────

EXISTING_BODY_WITH_LINKS = """\
## Key Takeaways
- Point one

## Related
- [[Investment Notes/Some-Note]]
- [[Articles/Old-Article]]

---"""

EXISTING_BODY_NO_RELATED = """\
## Key Takeaways
- Point one
"""

EXISTING_BODY_EMPTY_RELATED = """\
## Key Takeaways
- Point one

## Related

---"""


def test_add_related_link_appends_without_replacing():
    result = add_related_link(EXISTING_BODY_WITH_LINKS, "Articles/New-Article")
    assert "[[Articles/New-Article]]" in result
    assert "[[Investment Notes/Some-Note]]" in result  # existing link preserved
    assert "[[Articles/Old-Article]]" in result         # existing link preserved


def test_add_related_link_idempotent():
    """Calling twice must not duplicate the link."""
    result1 = add_related_link(EXISTING_BODY_WITH_LINKS, "Articles/New-Article")
    result2 = add_related_link(result1, "Articles/New-Article")
    assert result2.count("[[Articles/New-Article]]") == 1


def test_add_related_link_creates_section_if_missing():
    result = add_related_link(EXISTING_BODY_NO_RELATED, "Articles/New-Article")
    assert "## Related" in result
    assert "[[Articles/New-Article]]" in result


def test_add_related_link_handles_empty_related_section():
    result = add_related_link(EXISTING_BODY_EMPTY_RELATED, "Articles/New-Article")
    assert "[[Articles/New-Article]]" in result


def test_add_related_link_preserves_trailing_separator():
    result = add_related_link(EXISTING_BODY_WITH_LINKS, "Articles/New-Article")
    assert result.strip().endswith("---")


# ─── update_backlinks ─────────────────────────────────────────────────────────

def test_update_backlinks_writes_link_to_existing_note(tmp_path):
    note_content = "---\ntags:\n- finance\n---\n\n## Key Takeaways\n- Point\n\n## Related\n- [[Articles/Old]]\n"
    (tmp_path / "Existing-Note.md").write_text(note_content, encoding="utf-8")
    records = [{"slug": "Existing-Note", "title": "Existing Note", "text": note_content}]
    update_backlinks(records, "New-Article-Slug", tmp_path)
    updated = (tmp_path / "Existing-Note.md").read_text()
    assert "[[Articles/New-Article-Slug]]" in updated
    assert "[[Articles/Old]]" in updated  # existing link preserved


def test_update_backlinks_idempotent(tmp_path):
    note_content = "---\ntags:\n- finance\n---\n\n## Related\n- [[Articles/Already-Here]]\n"
    (tmp_path / "Existing.md").write_text(note_content, encoding="utf-8")
    records = [{"slug": "Existing", "title": "Existing", "text": note_content}]
    update_backlinks(records, "Already-Here", tmp_path)
    result = (tmp_path / "Existing.md").read_text()
    assert result.count("[[Articles/Already-Here]]") == 1


def test_update_backlinks_skips_missing_file(tmp_path):
    records = [{"slug": "Ghost-Note", "title": "Ghost", "text": ""}]
    # Should not raise — just logs a warning
    update_backlinks(records, "New-Slug", tmp_path)


# ─── prompt builder regression (corpus_block param) ───────────────────────────

def test_build_article_prompt_no_corpus_unchanged():
    """corpus_block='' must produce same output as the original function signature."""
    result = build_article_prompt("Some article content.")
    assert "Transform this article" in result
    assert "EXISTING VAULT NOTES" not in result


def test_build_article_prompt_with_corpus_prepended():
    corpus = "EXISTING VAULT NOTES ON RELATED TOPICS:\n---\nNote content.\n---\n\n"
    result = build_article_prompt("Article.", corpus_block=corpus)
    assert result.startswith(corpus)
    assert "Transform this article" in result
    assert result.index("EXISTING VAULT NOTES") < result.index("Transform this article")


def test_build_investment_prompt_no_corpus_unchanged():
    result = build_investment_prompt("Some article.", "Article Title")
    assert "Generate an investment note" in result
    assert "EXISTING VAULT NOTES" not in result


def test_build_investment_prompt_with_corpus_prepended():
    corpus = "EXISTING VAULT NOTES ON RELATED TOPICS:\n---\nNote.\n---\n\n"
    result = build_investment_prompt("Article.", "Title", corpus_block=corpus)
    assert result.startswith(corpus)
    assert result.index("EXISTING VAULT NOTES") < result.index("Generate an investment note")


# ─── process_file() integration — graceful degrade (no index.md) ──────────────
#
# Test structure:
#   tmp_path acts as a mini-vault.
#   Claude client is mocked to return pre-baked article + investment note strings.
#   We verify: notes written, log + index updated, NO corpus block in prompts.
#

MOCK_ARTICLE_MD = """\
---
tags:
- finance
- macro
date: '2026-04-04'
source: test
---

# Test Article Title

## Key Takeaways
- Point one

## Context
This is the context sentence. And a second one.

## So What
Relevant.

## Related
- [[]] ← leave blank, to be filled in Obsidian

---"""

MOCK_INVEST_MD = """\
---
tags:
- investing
- macro
date: '2026-04-04'
source: test
---

# Test Investment Angle

## Investment Thesis
Buy the dip.

## Key Risks
- Risk one

## Relevant Tickers / Assets
None identified.

## Time Horizon
Short term.

## Confidence Level
Medium.

## Related
- [[]] ← leave blank, to be filled in Obsidian

---"""


def _make_mock_client(article_md=MOCK_ARTICLE_MD, invest_md=MOCK_INVEST_MD):
    """Return a mock Anthropic client whose messages.create returns canned responses."""
    mock_resp_article = MagicMock()
    mock_resp_article.content = [MagicMock(text=article_md)]

    mock_resp_invest = MagicMock()
    mock_resp_invest.content = [MagicMock(text=invest_md)]

    client = MagicMock()
    client.messages.create.side_effect = [mock_resp_article, mock_resp_invest]
    return client


def test_process_file_graceful_degrade_no_index(tmp_path):
    """
    When index.md does not exist, process_file must:
    - succeed (return True)
    - write article note to articles_dir
    - NOT include corpus block in Claude prompts
    - append to log.md
    - create index.md
    """
    # Set up a minimal vault in tmp_path
    articles_dir = tmp_path / "05-Resources" / "Articles"
    invest_dir   = tmp_path / "05-Resources" / "Investment Notes"
    drop_zone    = tmp_path / "01-Drop Zone"
    processed    = drop_zone / "_processed"
    log_md       = tmp_path / "log.md"
    index_md     = tmp_path / "index.md"

    for d in (articles_dir, invest_dir, drop_zone, processed):
        d.mkdir(parents=True)

    source = drop_zone / "test_article.txt"
    source.write_text("Some article about the Federal Reserve and interest rates.", encoding="utf-8")

    client = _make_mock_client()

    # Patch the module-level path constants to point at tmp_path
    import dropzone_watcher as dw
    with (
        patch.object(dw, "ARTICLES_DIR",         articles_dir),
        patch.object(dw, "INVESTMENT_NOTES_DIR", invest_dir),
        patch.object(dw, "PROCESSED",            processed),
        patch.object(dw, "CORPUS_INDEX",         index_md),
        patch.object(dw, "INGEST_LOG",           log_md),
    ):
        result = dw.process_file(source, client)

    assert result is True

    # Article note written
    written = list(articles_dir.glob("*.md"))
    assert len(written) == 1, f"Expected 1 article note, got {written}"

    # log.md created and has an entry
    assert log_md.exists()
    assert "Test Article Title" in log_md.read_text()

    # index.md created
    assert index_md.exists()
    assert "Test-Article-Title" in index_md.read_text()

    # No corpus block was injected (index didn't exist at start of call)
    calls = client.messages.create.call_args_list
    for call in calls:
        user_msg = call.kwargs["messages"][0]["content"]
        assert "EXISTING VAULT NOTES" not in user_msg


def test_process_file_returns_false_on_claude_error(tmp_path):
    """If the first Claude call fails, process_file returns False."""
    articles_dir = tmp_path / "articles"
    articles_dir.mkdir()
    invest_dir   = tmp_path / "invest"
    invest_dir.mkdir()
    processed    = tmp_path / "processed"
    processed.mkdir()
    log_md       = tmp_path / "log.md"
    index_md     = tmp_path / "index.md"

    source = tmp_path / "article.txt"
    source.write_text("content", encoding="utf-8")

    client = MagicMock()
    client.messages.create.side_effect = Exception("API down")

    import dropzone_watcher as dw
    with (
        patch.object(dw, "ARTICLES_DIR",         articles_dir),
        patch.object(dw, "INVESTMENT_NOTES_DIR", invest_dir),
        patch.object(dw, "PROCESSED",            processed),
        patch.object(dw, "CORPUS_INDEX",         index_md),
        patch.object(dw, "INGEST_LOG",           log_md),
    ):
        result = dw.process_file(source, client)

    assert result is False
    # Source file not moved — still in place for retry
    assert source.exists()
