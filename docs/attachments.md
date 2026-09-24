# Attachments

Research runs can include local files, passed as `ResearchConstraints.attachment_paths` or supplied by GAIA-style benchmark cases. They become a provider-neutral attachment corpus (`attachments.py`) that the planner, scouts, and deep dives read through tools.

## Two lanes

### `normalized` (default)

The application extracts local file contents before any model sees them and exposes the same three function tools to every provider:

- `list_attachments()`
- `search_attachments(query, attachment_id=None, top_k=8)`
- `read_attachment(attachment_id, start_chunk=0, count=5)`

This is the preferred comparative benchmark lane because OpenAI, Anthropic, Google, xAI, and Z.AI receive the same extracted representation instead of provider-specific file APIs.

Supported deterministic extractors:

| Type | Representation |
|---|---|
| TXT/Markdown/JSON/XML/YAML/TOML | decoded/chunked text |
| HTML | visible text with script/style removed |
| PDF | page-aware text via pypdf |
| DOCX | paragraphs + tables |
| CSV | row-range table chunks |
| XLSX/XLSM | sheet + row-range table chunks |
| Images | dimensions/format metadata only |

Every attachment gets a SHA-256 hash, a stable per-run attachment ID, a media type, extraction metadata, chunk locators, and chunk hashes. Search is deterministic TF-IDF over the extracted chunks.

### `multimodal`

The normalized tools remain available, but local images are also sent as PydanticAI `BinaryContent`. PDFs are sent as binary only when deterministic extraction indicates that visual understanding is required (for example a scanned PDF with little/no extractable text).

Use this lane when vision/document understanding is intentionally part of the benchmark. It is **not** as clean a model-only comparison because provider support and file handling may differ.

```bash
research-bench examples/benchmark_suite_full.example.toml \
  --policies quality breadth glm-heavy \
  --attachment-mode multimodal
```

## Provenance

Attachment evidence is represented without fake local URLs:

```python
SourceRef(
    attachment_id="att-1-abc123...",
    locator="page 4",
    title="report.pdf",
    source_type="attachment",
)
```

Web evidence continues to use `url=...`.

This allows the evidence ledger and verifier to distinguish web sources from local materials and preserve page/sheet/row-level provenance. A verbatim `quote` from an attachment is checked against the chunk text that `read_attachment` or `search_attachments` returned in the same research run. Text visible only in images sent in the multimodal lane cannot be matched, so such quotes are marked `not_found`.

## Privacy / reproducibility

Host filesystem paths are intentionally kept inside the application process:

- paths are not rendered into benchmark prompts;
- paths are not stored in `research_attachments`;
- Postgres stores filename, hash, media type, extractor, size, chunk count, and extraction metadata;
- file bytes are not copied into Postgres by this layer.

The `research_attachments` table comes from `src/research_loop/migrations/002_research_attachments.sql`; `research-db migrate` applies it with the others (see [setup.md](setup.md#postgres)).

## Limits

`AttachmentLimits` bounds file size, extracted text, chunks, CSV rows, workbook rows, and sheet count. Benchmark runs default to strict ingestion: a missing/oversized required attachment fails the run instead of silently changing the task.

Extraction runs in a worker thread, so parsing a large document does not stall the run's other work. In the multimodal lane, the bytes sent to the model are checked against the hash recorded at ingestion; if the file changed since, the call fails instead of sending content the manifest does not describe.

Images are **not OCRed** in normalized mode. That is deliberate: OCR quality would become another uncontrolled dependency. Use the multimodal lane when pixels matter.
