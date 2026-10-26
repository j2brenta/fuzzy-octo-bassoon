# Picture support validation — 23 October 2026

## Implemented behavior

Telegram photos and still JPEG/PNG/WebP image documents, including captions, are saved before Token Factory vision reading. Originals remain unchanged. Derived OCR, visual descriptions and sender captions stay distinct, then enter the existing triage/claims/entities/review path. Direct uploaders are retained as sharers, not assumed image authors. Voice handling, settings and Whisper/PyAV dependencies have been removed; legacy source files and model caches were not deleted.

Limits: 10 MB / 20 megapixels. Model inputs are correctly oriented, re-encoded without EXIF and capped at 2048 pixels per side; original bytes are retained. Settings: `NEBIUS_VISION_MODEL=nvidia/Nemotron-3.5-Super-VL`, existing `NEBIUS_API_KEY`. Request format: [Token Factory vision API](https://docs.tokenfactory.nebius.com/api-reference/examples/vision-capabilities).

## Validation

**311 tests and 104 subtests pass**. Coverage includes owner checks, highest-resolution selection, captions/image documents, invalid/oversized media, disabled voice, retained sources, failure/retry behavior, cache reuse, source tampering, copied-header timezone/author separation, handoff timing and metadata-only cache preservation.

A synthetic screenshot was read by the real VLM with its speaker/date/time preserved. Its shared pipeline smoke test produced 4 claims and 1 entity with an accepted handoff.

With explicit approval, two images from `Desktop/images to test` were uploaded to Nebius and processed:

| Image | Type | Claims | Entities | Review | Original unchanged |
|---|---|---:|---:|---|---|
| IMG_3723.PNG | Room photo | 8 | 0 | accepted | Yes |
| IMG_3719.PNG | Math screenshot | 23 | 0 | needs_revision | Yes |

The math screenshot remains `needs_revision`: one claim mislabels an inner expression as the outer function; another lacks the passage supporting its tangent-line wording. These findings were retained rather than accepted automatically. This test validates the image pipeline, not perfect OCR/claim accuracy. Downstream claim review checks derived text, not original pixels.

Telegram download/reply boundaries were mocked; VLM and text-analysis calls used live Nebius. No Telegram messages were sent to the user and no bot polling process was started. Real Telegram delivery remains to verify after restarting the bot.

All original image files were hash-checked unchanged. Private images, derived text and full replies are retained only in ignored `.local-vault/picture-tests-2026-10-23/`; this summary contains filenames and counts, not the images or extracted personal content. Only the two approved supplied images were uploaded.

## Related completed review follow-ups

- [Nine-seed triage report](triage-v4-seed-evaluation-2026-10-23.md): 18/18 labels, 9/9 kept, including `demo-original`.
- [Reviewer usage](reviewer-token-usage-2026-10-23.md): actual input/output usage and evidence-payload overhead for the people list and essay.
- Copied Telegram headers reuse Kirill’s parser; exact source text stays intact, copied chat time is separate from publication/capture time, and unknown timezone is not guessed. Source timeline ordering now uses publication → timezone-qualified copy → capture time. This does not implement claim-level temporal relations or replace the graph adapter.
