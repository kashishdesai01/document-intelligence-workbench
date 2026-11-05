# HTTP API

Routes under `/api/v1` require `Authorization: Bearer <key>`. Each key maps to one workspace. A document ID from another workspace returns 404 for authenticated callers. Problems use `application/problem+json` with a stable `code`. The local API publishes its OpenAPI contract at `http://127.0.0.1:8010/openapi.json` when started directly.

| Method | Path under `/api/v1` | Behavior |
| --- | --- | --- |
| GET | `/schemas` | List the versioned schema and its JSON Schema |
| POST | `/documents` | Upload a `file` multipart field and optional `schema_id` |
| GET | `/documents` | List by status or filename with keyset pagination |
| GET | `/documents/{id}` | Read status, page count, generation, and revision |
| GET | `/documents/{id}/result` | Read current fields, transactions, evidence, and issues |
| GET | `/documents/{id}/pages/{n}` | Read display dimensions, orientation, and extraction source |
| GET | `/documents/{id}/pages/{n}/image` | Read the authenticated, non-cacheable JPEG |
| POST | `/documents/{id}/review/{field_key}` | Correct, approve, reject, or discard a saved decision |
| GET | `/documents/{id}/history` | Read review events, newest first, with pagination |
| POST | `/documents/{id}/reprocess` | Requeue a terminal document and preserve review decisions |
| GET | `/documents/{id}/export?format=json\|csv` | Download a completed statement |
| DELETE | `/documents/{id}` | Delete the document, uploads, jobs, pages, and review records |

Upload returns 202. Use the returned document ID to poll status with backoff. Identical files deduplicate within one organization, schema version, and pipeline version. A retry with the same `Idempotency-Key` and a different payload returns 409. Input type, encryption, page count, and decoding are checked asynchronously. A file that fails parsing appears as `failed` with a stable `error_code`.

Listing accepts `status`, `search`, `limit`, and `order=newest|oldest`. Pass the returned `next.before_time` and `next.before_id` with the same filters for the next page. History uses its `next` event ID as the `before` parameter.

## Review a field

Read the current result before review. Submit the field's key and the document revision. To resolve missing evidence, include a page and normalized top-left box. A correction can retain the original printed evidence and explain an inferred value in the review note. The audit record retains both values.

```sh
curl --fail-with-body "$WORKBENCH_URL/api/v1/documents/$DOCUMENT_ID/review/$FIELD_KEY" \
  -H "Authorization: Bearer $WORKBENCH_API_KEY" \
  -H "Content-Type: application/json" \
  --data '{"action":"correct","revision":2,"value":"-170.27","note":"Reconciled adjacent printed balances."}'
```

A stale revision returns 409. Re-read the result and check the current value before retrying. Approvals still run reconciliation and required-field checks. Unrecognized or incomplete document structure can require a better source file.

```sh
curl --fail-with-body "$WORKBENCH_URL/api/v1/documents/$DOCUMENT_ID/export?format=json" \
  -H "Authorization: Bearer $WORKBENCH_API_KEY" \
  -o statement.json
```

JSON includes the versioned business data under `data`, plus field-level provenance. `score` is a raw evidence score, with `null` on human-reviewed values. CSV contains the transaction table. Formula-like descriptions are prefixed with an apostrophe for spreadsheet import. Exports require completed status.

## Limits

Uploads allow one PDF, PNG, JPEG, or TIFF up to 50 MiB and 200 pages. Pages have a 40-megapixel ceiling, with additional extraction limits of 5,000 words per page, 60,000 words per document, and 2,500 transactions. Each organization can have up to 20 active documents and submit 15 new uploads per one-minute window. When all four API upload slots are busy, retry a 429 using backoff.
