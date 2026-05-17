# Z63C — Artifact Experience

**Phase:** Z63  
**Date:** 2026-05-17  
**Status:** Completed

---

## Current Artifact System Architecture

Artifacts are persistent, database-backed outputs tracked by `artifact_registry.py` and surfaced via:
- `GET /api/artifacts/list` — all artifacts for the workspace
- `GET /api/artifacts/{id}/files` — files inside a specific artifact
- `GET /api/artifacts/download` — bundle download
- The Files panel in the left sidebar renders artifacts via `_enrichArtifactSection()` in `nx-z47.js`

---

## What Works

### File Preview (Complete)
`_openFilePreview(path, sid)` provides inline preview for all common file types:
- **Markdown** (`.md`, `.markdown`): rendered via `_renderMarkdown()` with headers, bold, italic, code blocks, lists, blockquotes, links
- **JSON** (`.json`): formatted via `_renderJson()` with syntax coloring
- **Code** (`.py`, `.js`, `.ts`, `.jsx`, `.tsx`, `.html`, `.css`, `.sh`, `.yaml`, `.toml`, etc.): rendered via `_renderCode()` with language badge
- **Plain text**: shown as plain preformatted text
- **Binary files**: shown as "Binary file — download instead"

### Download Support
Every file preview header includes a `↓ Download` link pointing to `/api/download/{sid}/{path}`. Truncated previews also show a "Download full file" link.

### Inline Preview Breadcrumb
`← Files` back button returns to the file list. Filename, file size, and download link are shown in the breadcrumb row.

---

## What Was Fixed

### _openArtifact Was Broken
**Before:** `_openArtifact(id)` simply called `window.nxSetTab?.('code')` — it opened the code tab but showed whatever was last open, completely ignoring the artifact ID.

**After:** `_openArtifact(id)` now:
1. Fetches `/api/artifacts/{id}/files` to get the file list
2. Picks the most meaningful file to preview (priority: `.md` → `.html` → `.py` → `.js` → `.ts` → `.txt` → `.json`, then any file)
3. Calls `_openFilePreview(path, sid)` to render it inline in the Files panel
4. Falls back to the code tab if the artifact has no files

This means clicking any artifact row in the Files panel now opens a real, readable preview of its contents.

---

## Remaining Limitations

### No Artifact Grouping in UI
Artifacts are listed in a flat list sorted by recency. Grouping by type (Website, Code, Report, API) is not yet implemented in the Files panel sidebar. The `artifact_registry.py` tracks types, but the UI doesn't filter or group by them.

### No Side-by-Side Artifact Comparison
Only one artifact can be previewed at a time (single preview pane model).

### Replay Linking
Artifacts show their linked `session_id` (first 8 chars) in the metadata row. Clicking the session ID does not navigate to the session replay — this is a future improvement.

### Execution-Linked Artifacts
Real-time file changes appear as `.nx-artifact-card` entries in the orchestrator log during execution. These are transient and not permanently linked to the artifact registry entry unless the backend emits an `artifact.registered` event.

---

## Fake UX Removed

- `_openArtifact` previously was silent theater — opened a tab but showed nothing related to the artifact. Now it opens the actual artifact content.

---

## Readiness Score: 7/10

The file preview system is genuinely functional for all text-based file types. The critical `_openArtifact` broken interaction is fixed. Remaining gaps are UX polish (grouping, comparison, replay linking) rather than broken functionality.
