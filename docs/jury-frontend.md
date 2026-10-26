# First jury frontend

The browser replaces Telegram as an input channel. It uses the same durable raw
capture, parsing, triage, extraction, source review, entity and handoff functions.
There is no separate mock AI response path.

## Requirements read from Obsidian

Source folder: `01_PROJECTS/Hackaton Nebius & Nvidia` in Olga's Obsidian vault.

- **01 Project Brief**: capture mixed saved content, preserve originals, extract
  atomic claims, retain the source passage and distinguish original authors.
- **06 Ingestion Pipeline**: acknowledge capture before background processing;
  show failure states without losing originals; preserve linked articles as
  children; retain exact evidence and bounded model review behavior.
- **08 Retrieval and Refresher**: cited topic refreshers are the eventual main
  product. Retrieval is not implemented in this checkout and is not simulated.
- **11 Demo and Submission Draft**: demonstrate source-to-claim evidence, distinguish
  delivered behavior from plans, provide an accessible application entry point.

The notes include image/PDF modules from another development state. This checkout
has text and HTML ingestion, a terminal viewer and graph tools, but no browser API,
image reader, PDF reader, or topic-refresher endpoint. The new adapter adds native
PDF text reading; the browser explicitly labels other formats as retained/pending.

## UI/UX Pro Max application

Ran `--design-system` for `personal knowledge workspace minimal` and one narrower
`knowledge dashboard workspace` query. Both returned Minimalism & Swiss Style,
which fits a functional knowledge workspace. Their landing-page/conversion
patterns did not fit the task, so they were not applied. A targeted
`file upload progress feedback --domain ux` search returned **Progress Indicators**;
the UI reports actual server stages rather than estimating progress percentages.

The revised interface takes direction from the user-supplied https://lexanon.fr/
reference: warm cream and cobalt blue, editorial serif headings, halftone texture,
and yellow/coral accents. UI/UX Pro Max `editorial serif asymmetric --domain style`
returned Editorial Grid / Magazine, which fits this direction. The hero depicts the planned Source/Claim/Idea/Topic/Person/Organization model
as an illustrative, organic SVG network in cobalt on cream, with restrained
pixel halos and two animated packet trails. It is not live graph data. Motion
can be paused and is disabled by reduced-motion preferences.
The oversized explainer was removed; saved sources sit beside capture on desktop
and stack below it on smaller screens. Native form controls, visible keyboard focus,
a responsive single-column layout, reduced-motion support, and the evidence dialog
remain in place. System fonts
avoid external font requests. No existing JavaScript application stack was found;
plain HTML/CSS/JavaScript and Python's HTTP server add no frontend build toolchain.

## Observable acceptance

- Text or URL submitted in the browser is durably saved before acknowledgment.
- File uploads preserve exact bytes under a SHA-256 address and allow download.
- Background jobs expose queued/processing/complete/pending states on disk.
- Supported inputs run existing pipeline functions; unsupported formats preserve
  their original and explain how to continue with text.
- Source details display actual claims, exact Unicode-aware citation excerpts,
  review status, named entities and the original text. Source content is rendered
  as text, never injected HTML.
- Retry reuses the same submission/raw identity and existing model caches.
- Server startup marks interrupted jobs pending. The browser can reconnect.

## Scope and verification limits

This is a shared **local** demo workspace on `127.0.0.1`, isolated from Telegram's
vault. Public hosting and authentication are not implemented. Twenty-megabyte
uploads, twenty PDF pages and bounded extracted text keep the first version
small. It processes one job at a time. Image OCR, scanned PDF OCR, audio/video
transcription, graph browsing and topic refreshers need their actual backend
integrations before the UI can claim those features.

Tests in `tests/test_workspace.py` cover real local capture/extraction/packaging
with synthetic model replies, preserved file bytes, unsupported and AI-disabled
states, input boundaries, skip behavior and HTTP routes/origin checks. These do
not establish live Nebius quality or successful OCR/transcription. The initial version had no browser visual verification. The editorial redesign
was subsequently inspected in native Chrome through the computer-use tool.


## UI self-review

Kept the enlarged organic graph and blue-on-cream palette. Improved the actual
entry flow with an always-available Add source shortcut (including on mobile),
removed the redundant breadcrumb, replaced first-source-only wording, tightened
saved-source rows, and corrected the second packet trail's loop distance.
Checked the capture shortcut and form at a confirmed 375px responsive viewport
in Chrome. Tablet typography is scaled separately to prevent four-line headlines.

## Entry-page hero review

The previous hero had a memorable slogan but a weak statement of purpose, a low
emphasis capture link, and a graph separated from the copy by a hard panel divide.

Guidance consulted:
- [NN/g: Homepage Design, 5 Fundamental Principles](https://www.nngroup.com/articles/homepage-design-principles/): communicate purpose, use informative imagery, prioritize the user's main task.
- [GOV.UK: Button](https://design-system.service.gov.uk/components/button/): give the main action clear priority and keep secondary actions subordinate.
- UI/UX Pro Max `product demo --domain landing`: Product Demo + Features;
  relevant guidance applied to a sample prefill and pausable illustration, without
  adding a video, pricing, or unsupported features.

Kept the headline, enlarged organic graph and cobalt/cream palette. Added a plain
language explanation of capture and evidence, a filled Add a source link and a
quieter Try a sample button. Unified the card background, removed the internal
vertical divider, and labeled the graph illustrative. The sample prefills the
existing form; it does not send a submission or call a model. Graph animation
pauses when the illustration is offscreen or the browser tab is hidden, in
addition to manual pause and reduced-motion behavior.

Desktop layout and sample prefill were verified in Chrome; the source count
remained unchanged after the sample action. JavaScript syntax and whitespace
checks passed. These are design improvements based on guidance and inspection,
not measured conversion or user-testing results.


## Pixel brain hero

Replaced the abstract graph with generated cobalt halftone brain artwork
(`refresher/static/brain.png`) and an independent SVG network inside the brain.
Fourteen nodes and three travelling information packets keep the image legible.
A shared animation clock triggers a brief halo and pixel spark at the destination
when a packet arrives. Manual pause, offscreen/tab visibility, and reduced-motion
preferences stop the clock; resume does not jump forward over paused time.
This is illustrative animation, not telemetry from user submissions.

Verified the composed image and pause/resume control in Chrome. The image route
passes the existing eight frontend integration tests. A dependency-free Node
check verifies arrival sparks, pause/resume continuity and reduced-motion stopping:
`node --test tests/test_brain_animation.cjs`.

The brain artwork was simplified to broader cerebral folds and enlarged; the
brainstem and cerebellum were removed. The caption and standalone motion button
were removed. The artwork itself is a semantic button: click, Enter or Space
pauses/resumes its animation, with an accessible action label and hover hint.
Mouse pause and keyboard resume were checked in Chrome; the animation clock
check still passes.

Added a compact node legend for Sources, Claims, Ideas and People. Outline,
small solid, large solid and ring-with-dot markers match those categories in the
illustrative network. Arrival events now briefly tint the receiving node yellow
and emit a soft yellow halo with small gold pixel fragments; timing and pause
behavior reuse the same animation clock.
