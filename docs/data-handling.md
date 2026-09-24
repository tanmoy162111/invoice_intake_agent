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

## Model provider terms
To be recorded here in M3 from the provider's current documentation.
