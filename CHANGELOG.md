# Changelog

All notable changes to this project will be documented here.

This project does not yet follow strict semantic versioning. Releases are dated.

---

## April 2026 — Initial Public Release

This is the first public commit of the Second Brain repository. Prior versions existed locally but were never published.

### Architecture
- Three-level knowledge base: Source Notes, Investment Notes, Entity Notes
- Entity Note frontmatter schema with `sector`, `peers`, `themes`, `signal`, `conviction`
- Dual-gate conviction system (evidence floor + quality assessment)
- Corpus-aware ingest — watcher reads up to 50 related notes before generating output

### Automation
- Drop Zone watcher (`dropzone_watcher.py`) running as macOS LaunchAgent
- HTTP listener bound to `127.0.0.1:7337` for browser extension integration
- Relevance gate to filter low-signal inputs
- Automatic Entity Note updates after every successful source ingest

### Companion Tools
- BASECAMP Clipper Chrome extension — one-click webpage capture with ticker detection
- Dedicated For Her watcher using Opus for voice fidelity
- YouTube watcher with chunked transcript synthesis for videos over 60 minutes
- kepano/obsidian-skills integration at the Claude Code level

### Documentation
- Full README describing the project's design philosophy and current state
- ARCHITECTURE.md walking through data flow and watcher internals
- System architecture diagram

### Known Issues
- Test suite (`test_dropzone_watcher.py`) is from an earlier version of the watcher and is being updated against the current `dropzone_watcher.py`
