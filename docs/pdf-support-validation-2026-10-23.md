# PDF support validation — 23 October 2026

## Supported inputs

- Telegram PDF documents, including captions and forwarding metadata.
- Direct public PDF links, including download URLs without a `.pdf` suffix when MIME/signature identify a PDF.
- Local file import through `python -m channels.importer path/to/file.pdf --vault PATH`.

## Processing and limits

Original PDF bytes and immutable capture receipts are retained in `raw/pdfs/`. Selectable page text is read locally with PDFium; pages with little or no usable text are rendered and read by the existing Token Factory NVIDIA vision model. Completed page readings are cached in `documents/pdf-pages/`, keyed by file hash and page. Damaged derived caches are rebuilt from the original. The first successful derived text capture is immutable.

Limits: **20 MB, 20 pages, 50,000 extracted characters**. Over-limit, damaged, password-protected or unreadable documents return explicit errors and retain saved originals. No document is silently truncated. Blank pages are recorded without fabricating content; entirely blank PDFs do not enter extraction. Passwords are not requested or bypassed; send an unlocked copy.

Page numbers, extraction method, PDF/receipt hashes and character ranges survive the combined handoff. Readable result notes link the original PDF and show PDF page numbers beside claims. Ordinary text-support review checks the derived text, not the original page pixels. Charts/figures on pages already containing selectable text are not automatically interpreted; complex layouts/tables may need better parsing later. Multi-page documents still use the current whole-text extraction stage; no new semantic graph relations or claim-level date schema were added.

Public link downloads retain DNS/public-address pinning, TLS verification, redirect limits and size limits (2 MB HTML / 20 MB PDF). Other media links still have separate routing limits; Telegram image uploads remain supported. `WEB_FETCH=off` disables linked downloads, not PDF uploads. The existing `NEBIUS_API_KEY`/vision configuration is reused; native extraction needs no additional service key.

## Verification

**327 tests and 104 subtests passed.** The bot startup/import check, dependency lock check and diff-format check passed. Regression cases cover native/scanned/blank PDFs, original integrity, page boundaries, captions, ownership, file/page/text limits, malformed PDFs, failed model retries, page-cache corruption, MIME-based link routing, public-download limits, repeated deliveries and claim-to-page handoffs.

Synthetic fixtures were rendered and visually inspected. Telegram download/reply boundaries were mocked; PDF extraction and Nebius text/vision analysis used the live implementations. No real Telegram messages were sent and no additional polling receiver was started.

| Fixture | Pages | Methods | Claims | Entities | Review | Original unchanged |
|---|---:|---|---:|---:|---|---|
| native.pdf | 2 | native, native | 2 | 0 | accepted | Yes |
| scanned.pdf | 1 | vision | 5 | 1 | accepted | Yes |

A [public W3C PDF test file](https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf) was also downloaded through the production HTTP path, stored as a one-page PDF child, linked to its input URL/parent, and reused without another download. This checks routing and caching, not a retrieval/quality benchmark.

Full local evidence: `.local-vault/pdf-smoke-2026-10-23/validation.json` and `.local-vault/pdf-web-smoke-2026-10-23/validation.json`. Synthetic input fixtures live in `tests/fixtures/pdf/`; no private PDFs were uploaded for these tests. Actual Telegram delivery remains to verify after restart.
