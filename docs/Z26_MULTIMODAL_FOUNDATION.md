# Z26 — Multimodal Foundation

## Scope

Phase Z26 establishes ingestion foundations for:
- **Image** (PNG, JPEG, GIF, WebP, BMP)
- **PDF** documents

NOT implemented in this phase:
- Audio / speech
- Video
- Full multimodal orchestration
- Streaming multimodal pipelines

## Image Ingestion Foundation

### Upload Routing

Images are routed through the existing `/api/upload` endpoint (or equivalent) with MIME validation before any processing occurs.

**Accepted MIME types:**
```
image/png, image/jpeg, image/gif, image/webp, image/bmp, image/tiff
```

**Validation rules:**
- File size: max 20 MB
- Magic-byte validation (don't trust Content-Type alone)
- Filename sanitization (no path traversal)

### Ingestion Metadata

Every ingested image produces an `ImageIngestionRecord`:
```python
{
  "asset_id": str,          # unique stable ID
  "original_filename": str,
  "mime_type": str,
  "size_bytes": int,
  "width_px": int | None,
  "height_px": int | None,
  "ingested_at": float,
  "provider_capability": str,  # "vision" | "none"
  "stored_path": str,
}
```

### Provider Capability Detection

Before dispatching an image to an LLM provider, the system checks the provider's declared capability:

```python
VISION_CAPABLE_PROVIDERS = {
    "openai": ["gpt-4o", "gpt-4-vision-preview"],
    "anthropic": ["claude-3-opus", "claude-3-sonnet", "claude-3-haiku"],
    "gemini": ["gemini-pro-vision", "gemini-1.5-pro"],
}
```

If the current provider does not support vision, the system either:
1. Falls back to a vision-capable provider, or
2. Extracts alt-text / filename as a text-only substitute

### Extensibility Hooks

```python
# FUTURE_RUNTIME_MULTIMODAL: register additional media handlers here
_image_processors: dict[str, Callable] = {}

def register_image_processor(mime_type: str, fn: Callable):
    _image_processors[mime_type] = fn
```

---

## PDF Ingestion Foundation

### Upload Routing

PDFs are routed separately from images with their own MIME validation.

**Accepted MIME types:**
```
application/pdf
```

**Validation rules:**
- File size: max 50 MB
- PDF magic bytes: `%PDF-`
- Max pages: 500 (configurable)

### Text Extraction Pipeline

PDF text extraction uses a simple tiered approach:

```
Tier 1: pdfminer / pypdf (text-based PDFs)
Tier 2: OCR hint stored in metadata (image-based PDFs)
```

For image-based PDFs, OCR is not executed in this phase — only a flag is set in metadata for future processing.

### Ingestion Metadata

Every ingested PDF produces a `PDFIngestionRecord`:
```python
{
  "asset_id": str,
  "original_filename": str,
  "size_bytes": int,
  "page_count": int,
  "is_text_extractable": bool,
  "requires_ocr": bool,
  "extracted_char_count": int,
  "ingested_at": float,
  "stored_path": str,
}
```

### Extensibility Hooks

```python
# FUTURE_RUNTIME_OCR: register OCR processor here
_ocr_processor: Callable | None = None

def register_ocr_processor(fn: Callable):
    global _ocr_processor
    _ocr_processor = fn
```

---

## Security Considerations

- All uploads are stored in session-scoped workspace directories
- Filenames are sanitized before filesystem write
- MIME type is validated against magic bytes, not just extension
- Maximum file sizes enforced at ingestion boundary
- No execution of uploaded content

## FUTURE_RUNTIME markers

- `FUTURE_RUNTIME_AUDIO_INGESTION`: audio → transcript pipeline (Deepgram, Whisper) — deferred
- `FUTURE_RUNTIME_VIDEO_INGESTION`: video → frame extraction + transcript — deferred
- `FUTURE_RUNTIME_OCR`: full OCR pipeline for image-based PDFs — deferred
- `FUTURE_RUNTIME_MULTIMODAL_ORCHESTRATION`: unified cross-modal routing and context building — deferred to v2
