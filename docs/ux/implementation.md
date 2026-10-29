# UX implementation on olga/ux

29 October 2026. Implements the navigation, capture, reading and return flows in [the proposal](../user-journey.md), reusing the existing Python server and browser components.

## Delivered behavior

- Shared **Home / Library / Explore / Add source** navigation; maintenance and suggestions are in **More**. Subject Overview and Sources preserve their subject and selected source. Legacy URLs remain available.
- Empty Home prioritizes a first source and an explicitly fictional example. Populated Home prioritizes Continue reading when available, plus recent and followed subjects. The example saves nothing and makes no model calls.
- Continue reading, recently visited subjects and scroll positions are remembered in this browser, separately for each vault. Reading decisions and followed subjects/checkpoints remain in the existing vault records. Unsent capture drafts are session-only; selected files must be chosen again after a reload.
- `/library/` indexes saved browser, Telegram and imported sources, including unanalyzed text, unparsed media and uploads without extracted text. Browser receipts are linked to their original source. Linked article children remain separate. Media receipt rows collapse into the derived source once its readable text exists.
- `/library/?source=…` opens original text, source review status and exact claim excerpts. Saved Telegram PDFs/images can be downloaded using the existing channel identity/hash checks. It does not run OCR or claim extraction.
- Capture opens a stable `/capture/?receipt=…` link after storage. Recovery distinguishes disabled analysis, unsupported format, unreadable link and retryable failure. Repeat capture keeps the input close to the top instead of repeating the large hero.
- Source reading never implicitly marks a source read or skimmed. Mark read is explicit, reversible, and reports save failures. Follow subject and Save checkpoint are distinct actions using the existing thread/snapshot contracts.
- Overview has one Start/Continue reading action and a source reader with Next. Source detours remain in history. Evidence exploration selects the source in Explore and offers a validated return link.
- Mobile uses four bottom destinations, a compact reading header and accessible context controls with Escape dismissal and focus return. Cream/cobalt typography remains; the brain artwork introduces Home.
- Suggestions retain the existing stale-decision protections, with clearer Create topic / Merge / Dismiss labels and the correct section link.
- `REFRESHER_APP_URL` optionally adds reachable source links to Telegram receipts/results. It must be an origin such as `https://knowledge.example`, serving the same vault. Blank means no links; it neither publishes localhost nor adds authentication.
- Brief paragraphs are generated only when **Write the brief** is clicked. Opening a subject still displays the existing source-backed material and cached paragraphs.

## Routes and storage

New read-only APIs: `/api/library`, `/api/library/source?id=…`, `/api/library/original?id=…&kind=pdfs|images`. They use the existing local Host checks and do not expose arbitrary filesystem paths. Library source identifiers and original media kinds are validated. Original downloads retain their file extension and use attachment responses.

The server adds an opaque vault identifier to page markup to namespace browser navigation memory. No new knowledge store, frontend framework, dependency, auth system or schema migration is introduced. Pending browser jobs may contain an additive `recovery` field; existing jobs remain readable.

## Verification

- Full Python regression suite: **656 passed, 128 subtests passed** after collection/media integration. Final vault-namespace, download and legacy-receipt changes passed the focused app/capture/Telegram/UX suites: **53 passed, 7 subtests passed**.
- Node checks cover Home states, shared footer rendering, source/subject resume and scroll restoration, unsafe return URLs, timeline filters, and isolation between vaults.
- Python compilation, JavaScript syntax checks and `git diff --check` pass.
- Chrome desktop: Home → subject → source → Home/Continue; Library listing; sample capture → durable receipt → analysis-disabled recovery. Processing/model providers and web fetching were disabled on a synthetic vault under `/tmp`.
- Chrome responsive mode at **390px width**: four bottom destinations, compact returning-user Home, source reader, subject/source URLs, context opening, Escape dismissal and focus return.

## Practical limits

The five-person usability study is still a proposed validation activity, not a completed study. No live Nebius quality test, real Telegram delivery or public deployment was performed. The Docker image and user's running production/demo container were not rebuilt or replaced.

Reading continuity is local to this browser and vault, rather than synchronized across devices. A subject/source removed from the vault may leave an old recent link, whose reader reports that it is unavailable. Following/checkpoint semantics retain the existing backend behavior; there is no new unfollow or undo-organization backend. The optional review destination still lives inside Explore. Existing subject/graph ranking and source trust calculations are unchanged.

Use the unified `refresher serve` command on the desired vault; `python -m refresher.web` remains its compatibility entry point. A localhost URL sent to a different device cannot reach the laptop's server. Browser uploads still support their existing formats; this UX work does not add OCR, audio/video parsing or a public sharing service.

## Separate Timeline

Timeline now lives at `/timeline/`, opened with **Open timeline** from Explore.
The Atlas sidebar list has been removed; map filters sit in a compact disclosure above the map.
Timeline shows connected sources grouped by month, with publication/capture labels, search,
topic/idea/review/freshness/date filters and a repost toggle. Unanalyzed originals remain in Library.

The selected source and filters travel between Explore and Timeline in the URL. A source opens
in the existing reader with **Back to timeline**; returning highlights and scrolls to that source.
Timeline uses the existing `/api/data` projection and loads no graph canvas or new model calls.
Explore remains the active main navigation destination for this secondary page.

Verification: 30 focused UX/app/graph tests and seven Node checks passed. Chrome verified
Atlas → Timeline → source → Timeline and a filtered return to Atlas with the selected source intact.

## First-visit Home

An empty collection now opens on the existing cobalt brain artwork and the
“Collect the sparks. Connect the dots.” introduction. The primary action goes
directly to the capture form; a secondary action opens the prepared example.
Three short steps explain saving, connections and returning to a subject.

Home waits for both the topic index and the source index before choosing its
state. Saved sources with no topics receive a Library next step; existing topics
keep the dashboard. A connection failure is not presented as an empty collection.
The welcome artwork reuses the brain asset with animated network packets, annotations and a Pause/Resume control. Animation respects reduced motion and stops offscreen or while the tab is hidden.

Verified in Chrome on desktop and at 390px, including navigation into the capture
form and Back. Eleven Node checks pass, including empty, awaiting-topic,
populated and connection-error Home states and delayed source-index responses.

## Capture cleanup and consistent footers

Capture now opens directly on its source form. Its promotional hero, brain SVG,
animation listeners and unused artwork styles were removed; Home retains the
brain illustration. Its animation and focused check now belong to Home.

Every page now receives one footer from the shared shell, using Capture's
wordmark, slash divider and “Collected with purpose” treatment, plus the
single “Built for the Nebius × NVIDIA Global AI Hackathon” credit. Reader
action bars are preserved, and embedded maps hide the page footer.

Verified Capture's form and sample prefill, Home's artwork and footer in Chrome.
The focused app/workspace/UX/graph tests pass: 37 tests and five subtests. Eleven
Node checks pass, including shared footer rendering and duplicate prevention.

## Shared page layout

All eight page templates now use the shared shell's 1120px centered frame,
28px desktop / 18px mobile gutters, 32px desktop / 28px mobile page headings,
and consistent header and footer alignment. Home retains its brain illustration;
Atlas's empty introduction aligns left with the shared content frame.
Capture keeps equal, compact input-mode heights. Library and Timeline use
responsive filter grids, with restrained empty states. Subject overviews and
Sources views share the same outer frame while preserving reading controls.
Technical Library help and its obsolete menu links were removed.

## Focused Sources reader

Opening a source now uses a single reading column on desktop and mobile instead
of splitting the screen with the map. Back to sources restores browsing.
The duplicate opening-lines gist is hidden; real summaries start collapsed.
Evidence marks stay interactive but underline only when Highlight evidence is
enabled. Source details are an on-demand drawer. Reading/skim controls remain.
Verified the reported source URL, evidence toggle and drawer in Chrome; 32
dossier, reading and context tests pass.

Timeline entry titles and shared collection/card headings now use 20px desktop /
18px mobile type. Month and section headings use 22px. Timeline metadata uses
13px with 11px dates; filter labels use 12px. Atlas and Timeline use the saved
web article title, preserving both distinct source IDs and all evidence.
Verified the selected-source Timeline in Chrome; seven graph tests and three
Timeline checks pass.
