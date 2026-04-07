# Architecture

A technical walkthrough of how the Second Brain system fits together. For the high-level project overview, see `README.md`.

---

## Design Principles

**Local-first.** All processing happens on the user's Mac. The only external service is the Anthropic API. There is no cloud database, no managed service, no vendor lock-in. The vault is a folder of markdown files that survives any tool failure.

**Markdown as the data layer.** Every persistent artifact is a `.md` file with YAML frontmatter. This makes the entire system inspectable in any text editor, version-controllable in git, and immediately usable by any AI tool that exists or will exist.

**Watchers, not pipelines.** Each capture flow is a small Python script running as a macOS LaunchAgent. There is no orchestration layer, no message bus, no scheduler. Each watcher polls or listens, processes one input at a time, and writes its output. Simplicity is the feature.

**Compounding artifacts.** Source Notes are immutable archives. Investment Notes are decision-focused snapshots. Entity Notes accumulate every source that touches a company and regenerate their Synthesis on every update. The longer the system runs, the more valuable the Entity Notes become.

---

## Data Flow

A new source enters the system through one of three paths:

1. **Browser** — User clicks the BASECAMP Clipper extension on any webpage. The extension extracts readable content, detects tickers from the page title, and POSTs the payload to `127.0.0.1:7337` where the dropzone watcher's HTTP listener receives it.
2. **File system** — User drops a `.md` or `.txt` file into `01-Drop Zone/`. The watcher's poll loop detects the new file on its next cycle.
3. **YouTube** — User drops a `.md` file containing a YouTube URL into `YT Drop Zone/`. The YouTube watcher fetches the transcript via `youtube-transcript-api`, chunks it for long videos, and feeds the result into the same downstream pipeline.

Once an input is received, the processing sequence is identical regardless of source:

1. **Relevance gate.** A lightweight Claude call decides whether the input is worth processing or should be discarded as low-signal noise. Failed inputs are logged and removed.
2. **Corpus retrieval.** The watcher scans the vault for up to 50 existing notes that share tags, tickers, or sectors with the new input. These become context for the generation step.
3. **Generation.** Full input + corpus context is sent to the Claude API with a structured prompt. Claude returns two outputs: a Source Note (raw archive with metadata) and an Investment Note (decision-focused take).
4. **Persistence.** Both notes are written to the vault with frontmatter, wikilinks, and timestamps. The source file is moved to `_processed/`.
5. **Entity update.** For every ticker mentioned in the new input, the watcher invokes `entity_note_manager.py` to either create a new Entity Note or update the existing one. The update writes a new Evidence Log row, regenerates the Synthesis section, and re-evaluates the `signal` and `conviction` frontmatter fields.

---

## Entity Note Update Logic

Entity Notes are the most strategically important artifact in the system. They are also the most carefully constrained.

**Synthesis regeneration.** Every time a new source touches an Entity Note, Claude regenerates the entire Synthesis section from scratch using all sources in the Evidence Log. This means the current view always reflects the full evidence base, not a stale snapshot from the first source.

**Dual-gate conviction.** The `conviction` field is governed by two independent constraints:

- **Evidence floor (Option A):** 1–2 sources cap conviction at LOW, 3–5 sources cap it at MEDIUM, and 6+ sources are required for HIGH.
- **Quality assessment (Option B):** Within the floor, Claude evaluates source diversity, angle consistency, and thesis strength.

Final conviction is the *minimum* of the two. A single high-quality source can never produce HIGH conviction — the floor blocks it. This is intentional: it prevents the system from manufacturing false confidence from limited data, which is the failure mode that hurts investors most.

**Signal evaluation.** The `signal` field (BUY / HOLD / SELL / NEUTRAL) is regenerated alongside Synthesis. Claude is instructed to weight recent sources more heavily but to flag any contradictions with older evidence.

---

## Watcher Stack

Four watchers run continuously as macOS LaunchAgents:

| Watcher | Vault | Model | Trigger |
|---|---|---|---|
| Dropzone Watcher | BASECAMP | Sonnet | File drop or HTTP POST |
| YouTube Watcher | BASECAMP / For Her | Sonnet | `.md` file with YouTube URL |
| For Her Watcher | For Her | Opus | `.md` or `.txt` file |
| StockTwits Ingester | (database) | — | Polling, passive collection |

Model selection is deliberate. Sonnet handles research accuracy for BASECAMP — fast, precise, structured. Opus handles voice fidelity for For Her — slower but materially better at preserving tone and avoiding generic AI register.

Each watcher has its own dedicated API key (`amp-dropzone-watcher`, `amp-youtube-watcher`, `amp-forher-watcher`) so a key revocation in one pipeline never affects the others.

---

## What's Not in This Repo

The vault contents themselves — the Source Notes, Investment Notes, and Entity Notes that the system produces — are not in this repository. They live in a separate Obsidian vault on the author's machine. This repo contains the architecture, the watcher code, and the documentation needed to understand or rebuild the system. The output is private; the infrastructure is public.
