# Data handling

Demo A uses only synthetic data, but it is built as if it held real client data (playbook §10).
This page grows as features land.

## Uploaded documents (M2)
- Originals are stored on disk, named by SHA-256 (`storage/<tenant>/originals/aa/<sha>`), never by the
  client's filename. The display filename is sanitised (no paths, no control characters).
- File type is decided from the file's content, not its name or declared type. HTML, archives and
  other types are rejected; uploaded content is never executed or rendered as HTML.
- Bounds: size, page count and pixel count are limited before rendering, to contain decompression
  bombs and huge pages.
- Worker memory is capped (`mem_limit` in Compose) and pages are rendered one at a time. Job errors keep
  only the exception class, never its message.
- Rendered page images and the extracted text layer are stored under `storage/<tenant>/pages/<doc>/`.
  Document text is never written to logs; errors log only an exception class or short message.

## Bank details (M1)
Encrypted at rest (Fernet, `BANK_ENCRYPTION_KEY`); comparisons use a keyed hash; masked in the UI.

## Extraction and the model provider (M3)

### What is sent to the model
For each invoice: the rendered page images (PNG, or JPEG when a page is too large), the document's
own text layer when it has one, the versioned prompt, and the answer schema (field names only).
Bank details printed on the page are visible to the model; that cannot be avoided when reading the
page. Blank pages are never sent. The prompt and the images are **not** stored in our database.

### Provider terms (Anthropic, recorded 2026-09-25 from the current documentation)
Source: [API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)
and the [vision FAQ](https://platform.claude.com/docs/en/build-with-claude/vision). Re-check before any
real-data use; terms change.
- **Training.** Retained data is never used for model training without express permission. The vision
  FAQ states that Anthropic does not use uploaded images to train models.
- **Retention.** Conversation content (prompts and outputs) is not retained by default. Image uploads
  are ephemeral: not stored beyond the request.
- **Strict tool schemas.** With `strict: true` the API caches the *schema* (not prompts or answers) for
  up to 24 hours since last use. Our schema holds field names only; never put client data in it.
- **Zero data retention (ZDR)** is available per organisation on request (contact Anthropic sales).
- **Exception: "Covered Models".** Claude Fable 5.1, Fable 5, Mythos 5.1 and Mythos 5 require 30-day
  data retention and are not available under ZDR unless Anthropic authorises it. The default,
  `claude-sonnet-5`, is not one of them. **Choosing `claude-fable-5-1` for real data means Anthropic
  retains requests for 30 days;** decide that deliberately.
- **Other backends.** `LLM_PROVIDER=ollama` sends pages to the machine at `OLLAMA_BASE_URL` (default:
  your own computer; inside Docker, `host.docker.internal`). Point it only at hardware you control. It
  is demo-only. Do not use hosted free tiers with real data: they may train on inputs.

### What the extraction stores, and where
| Where | What | Protection |
|---|---|---|
| `field_extractions` | Raw and normalized value, confidence and signals for every field | Plain text, **except** the bank account: encrypted (raw) and keyed hash (normalized) |
| `invoices`, `invoice_lines` | Supplier name, invoice number, dates, amounts, lines | Plain text; business data |
| `llm_calls.response` | The validated model answer (the cache) | Supplier name, address, tax id, lines and amounts in plain text; the bank account is sealed (`enc:` + Fernet token). No purge policy yet, so treat it as business data and purge it before sharing a database |
| `llm_calls` (other columns) | Model, prompt version, token counts, cost, latency, status, request hash | No document content |
| `audit_events` | Codes, counts, model, prompt version, cost. Never values | No document content |
| `storage/.../pages/` | Page PNGs and `text.json` (from M2) | **Plain files on disk**; protect the volume |

### Rules the code follows
- Model output is untrusted: it is forced into a strict schema, validated again in Python (no NUL
  characters, bounded length, page numbers inside the document), then normalized. Nothing the model
  writes decides a status, a route or a query. Text inside an invoice that looks like an instruction
  is treated as data (prompt rule 9).
- The cache key is (file hash, model, prompt version, a fingerprint of the prompt text and schema),
  scoped to the tenant. Editing a prompt in place cannot reuse an old answer.
- Errors and logs carry codes and exception class names only. The Anthropic and HTTP client loggers are
  held at WARNING because their DEBUG output can include request bodies.
- Tests never call a model: an autouse fixture blanks `ANTHROPIC_API_KEY`, and recorded fixtures
  replace the model.
- Model spend is capped per UTC day and every call is logged. The cap is checked before each call, so
  concurrent workers can overshoot it by at most one call each.
