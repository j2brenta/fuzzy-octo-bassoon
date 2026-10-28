# Thread Navigator — Step 0 findings

28 Oct 2026 · discovery only, no code changed · measured on `.local-vault/telegram` (13 sources, 222 claims, 105 Ideas) with the Qwen3-Embedding-8B vectors cached in the vault, on branch `kirill/reading-map` with its uncommitted work.

The prototype `docs/prototypes/thread-navigator.html` is not in the repo yet; nothing below depends on it.

## Three findings that change the plan

> Corrected after review. A first draft said Olga's person scoring did not exist. It does: PR #37 `olga/people-scoring`, merged into `origin/main` as 70b7696. This branch (`kirill/reading-map`) is 7 commits behind it, which is why the first search missed it. The figures below were rerun with PR #37's code on the same vault.

1. **Olga's person scoring is PR #37 (`people-v3`), and it has two reads.** `store.trust(person_id)` is kept for compatibility but redefined: contradictions no longer lower it, and attributed claims earn credit only from reviewer-checked primary origins. `store.experts(at, subject_id, idea_ids=, subject_title=)` is new and ranks people per subject. Both stay pure and offline on replay. Details are in §3 below and in `docs/people-scoring.md` on main.
2. **On the real corpus every person is unscored and every trust value is 0.50.** All 13 relation records in `.local-vault/telegram` are `relate-v2`, so no claim has an attribution, and `experts()` returns `importance: None` for all 16 people on both test subjects. Two steps would change that:
   - **Speakers:** the paid upgrade (`refresher relate --vault …` to `relate-v3`, or the Atlas People-mode backfill of at most 3 documents per batch with a 120k-token budget) would fill in who said each claim.
   - **Track record:** it still stays at 0.50 until you review speakers and check primary origins in the Atlas, because only checked distinct origins and your votes move it.

   Separately, 9 of 13 sources have no author: Telegram channel posts carry only the forwarder. "Top quarter reads trusted" cannot be calibrated on this data in any case.
3. **There are no relations between sources to type.** Of 222 relation verdicts, 221 are NONE and 1 is ADDS_DETAIL, which never became an edge. Every Idea has exactly one source (105 of 105). So `disputes`, `supports`, `builds_on` and `cites` have no evidence today. What does exist: 1 repost pair (the two CNBC Microsoft items), 1 extraction link (the Telegram note links Krestnikov's arXiv URL, which is the paper's own URL), the same author on the repost pair, and near-duplicate Ideas by embedding (cosine ≥ 0.55, which the subject code already uses for "connected"). Phase E would ship with `based_on`, `retells`, `same_author` and an untyped "shares an Idea" link, and the other types would stay empty until relate finds relations.

## Term map

| Plan term | Real thing | Where |
| --- | --- | --- |
| source | `ItemNode` (item_id, `source: Source`, title, text, author_id, source_key, duplicate_of, hidden, linked_from) | `graph/store.py:20` |
| outlet / channel | `SourceNode` (key `source:<channel>:<name>`, name, channel, items); has no score | `graph/store.py:107` |
| claim | `ClaimNode` wrapping `schemas.Claim` (statement, kind, durability, confidence, `evidence: Span`) plus idea_id, subject_id, duplicate_rank, superseded_by, votes | `graph/store.py:34`, `refresher/schemas.py:66` |
| idea | `IdeaNode` (canonical_claim_id, title, members = claim ids, status provisional / firm). World claims only: claims about a person or org get `subject_id` and no Idea (116 of 222 here) | `graph/store.py:50` |
| cluster | `ClusterNode` (ideas, sources, items, centroid, title). Recomputed on every replay, never stored. **Clusters do not overlap:** every Idea is in exactly one cluster. `cluster:theme-*` are model-named themes | `graph/store.py:144`, `graph/cluster.py` |
| idea-to-cluster membership | `ClusterNode.ideas`; `refresher/subject.py` folds each theme into its best-linked real cluster | `subject.py` (`cluster_of`) |
| topic, accepted / provisional | `TopicNode.ideas` (accepted) / `.attached` (provisional) / `.clusters` | `graph/store.py:134` |
| relation between claims | `Edge` with type SUPPORTS, DUPLICATES, SUPERSEDES, CONTRADICTS, ELABORATES, from the stored `Relation` verdicts (SAME, UPDATES, CONFLICTS, ADDS_DETAIL). Direction is later → earlier for all but ELABORATES, which goes from the new claim to its target | `graph/memory.py:395-435` |
| shared-idea relation between clusters | `Link` (level, a, b, weight, relations, shared) and, for a subject, near-duplicate Idea pairs ≥ `SHARED` | `graph/store.py:157`, `subject.py` |
| person | `PersonNode` (authored, mentioned_in, facts, confirmations, contradictions, up, down, trust_pinned, enrichment_status) | `graph/store.py:61` |
| organisation | `OrgNode` (types, mentioned_in, facts); `Affiliation` ties | `graph/store.py:81` |
| mention with role | `MENTIONS` edge item → person/org; author = `ItemNode.author_id` / `AUTHORED_BY`; "discussed" = a claim in `person.facts`. Assembled per source in `subject.context()` as wrote this / discussed here / mentioned here | `subject.py:context` |
| passage anchor | `Claim.evidence: Span` (section_id, start_char, end_char). `dossier.document()` returns spans per Idea, `subject.context()` returns `passages` per Idea | `schemas.py:58`, `dossier.py:578` |
| person score / trust | PR #37: `store.experts(at, subject_id, …)` rows (`importance`, `track_record`, `subject_relevance`, `professional_relevance`, `evidence_level`, `attribution_status`), and `store.trust(pid)` (pinned, else `trust_learned` over checked-origin confirmations and votes) | `refresher/people_scoring.py`, `graph/memory.py` (`experts`, `trust`), `refresher/scoring.py` (all on main) |
| speaker / stance input | PR #37: `ClaimNode.attribution` (`author` / `quoted` / `unknown`, with passages), `speaker_id`, `attribution_reviewed`, `evidence_origin`, from `Verdict.attribution` in `relations/` (relate-v3) and `people-reviews/` | `graph/attribution.py`, `refresher/schemas.py` (`Attribution`) |
| subject | `dossier.Scope` from `dossier.resolve(store, key, query)` | `dossier.py:57,80` |
| subject scoring, roles, plan | `subject.subject()`, `subject.plan()`, `subject.context()` | `refresher/subject.py` (uncommitted) |
| repost / retelling | `ItemNode.duplicate_of`; `DUPLICATES` item edge | `graph/memory.py:189`, `:770` |
| "tables", `computed_at` | none: see below | |

## Storage: there are no tables

Decided on 19 Oct and still true: no SQLite. Vault files are the truth, and `MemoryStore` is rebuilt from them in memory (`vault.replay`, 0.08 s here). Clusters, topics, links, trust and the subject payload are all recomputed, never stored. `app.State` caches the store and every scored subject until a watched vault file changes (`app.py:73`, `WATCHED`).

So "own tables or columns with a `computed_at`" becomes: pure functions over the replayed store, cached in `State` beside `_subjects`, stamped with the subject payload's `at`. They are never written to `processed/` or `relations/`. If a stored copy is wanted (for the bot, or for a wiki note), it goes under `.cache/` like `last-consolidate.json`, as a cache, not as truth. "Migration" and "alters an existing table" do not apply. The equivalent rule is: ask before changing a schema in `refresher/schemas.py` or a vault stage.

## Step 0 answers

### 1. Data model

See the term map. Points that matter for Phase A:

- **Home and also-here are well defined.** An Idea has exactly one cluster, so a source's placements are the clusters its Ideas fall in. Measured: 4 sources have no Ideas, 3 sit in 1 cluster, and 6 sit in 2 to 6 clusters ("Reporting bias" in 6, the Telegram note in 6).
- **Home already exists** as `sources[id]["cluster"]` in `subject()`: the cluster holding most of the source's Ideas, where a cluster only this source makes counts half and a repost lives with its original. The plan's rule 5 (best role first, then stance, then most Ideas) is a different rule. Today home is decided first and the role inside home after it, which keeps a source in one place across subjects (a stated goal of the reading-first work). Role-first would let home move with the subject. **Decision needed.**
- **Roles today are per home cluster only.** A source has one role, in its home. Per-cluster roles (plan rule 4) are new.

### 2. Passage anchors

They exist. Every claim has character offsets into the item text (`Span.start_char`, `end_char`, `section_id`), and `subject.context()` already returns `passages: [[start, end], …]` per Idea, which the reader marks. Phase E's "scroll to the passage that put it there" needs only the first passage of the first Idea in that cluster. No extraction work.

### 3. Person scoring: Olga's PR #37, `people-v3` (on `origin/main`, not yet on this branch)

- **Where:**
  - `refresher/people_scoring.py:score_people`, exposed as `GraphStore.experts(at, subject_id=None, *, idea_ids=None, subject_title=None)`.
  - Helpers: `graph/attribution.py` (who said a claim, rechecked against exact passages on every replay) and `graph/corroboration.py` (assertion groups, checked-origin credit).
  - Computed on request from the replayed store. Nothing is stored except your reviews in `people-reviews/` and the paid backfill jobs in `people-jobs/`.
  - Exported as `people_scores` and `people_subjects` in the graph JSON, and in person notes.
- **Formula:**
  - `subject_relevance` = attributed eligible Ideas ÷ eligible Ideas in the subject.
  - `track_record` = `(1 + checked distinct peer origins + up) / (2 + … + up + down)`, or your pinned trust instead.
  - `importance` = `subject_relevance × (0.6 · track_record + 0.4 · professional_relevance)`, where an unknown professional relevance counts as a neutral 0.5.
  - Eligible means accepted, current (not superseded or noise), and not a duplicate.
- **Range:**
  - `track_record` is in (0, 1) and starts at 0.5.
  - `importance` is in [0, 1], or **`None` = unscored** when the person has no attributed statements. That is never a low score.
  - `evidence_level`: limited (fewer than 3 source items), developing (3 or more), established (5 or more with 3 or more checked origins).
  - `rank` is shared on ties.
- **Global or per subject:**
  - `experts()` is per subject: an accepted topic, a cluster, or any idea set with `idea_ids=` (the dossier already passes a query's Ideas this way).
  - `store.trust()` is global.
- **"Named, no profile":**
  - `is_named_person` drops placeholder names ("Writer", "Author"), and entity review now rejects eponyms ("Newton's law").
  - Anyone without attributed statements is unscored, and missing profile domains count as unknown and neutral. That is the plan's "unknown, never low" rule, built in.
- **Channels and outlets:**
  - Still no score. A Telegram channel post has no named `author_hint`, so its claims can only get a `quoted` speaker or `unknown`.
  - The enriched person "Немецкий гастарбайтер" is still not linked as the author of its posts.
- **Contradictions:** they are now reported as `disagreements` and no longer lower anyone's trust. Supersessions are reported as `updates`.
- **Several speakers per source:** this question is real again. A source has one writer, but its claims can have several `quoted` speakers. Source trust should probably come from the claims whose speaker is the `author`, not from the people it quotes. Confirm with Olga.
- **Interface for the Thread Navigator:**
  - Read `store.experts(at, idea_ids=<subject Ideas>, subject_title=…)` once per subject.
  - Map each source to its author's row: `importance is None` gives "unknown", otherwise a word from `track_record` and `evidence_level`.
  - Do not compute anything new from `trust_learned`.
- **Tests:** `tests/test_people_scoring.py` (on main), `tests/test_scoring.py`, `tests/test_people.py`.
- **On the real vault:** every record is `relate-v2`, so there are 0 attributions, 16 of 16 people are unscored, and `trust()` is 0.50 for all (see finding 2).

### 4. Reading-first plan status

Phases 1 to 3 are implemented on `kirill/reading-map` and **uncommitted** (`refresher/subject.py`, `refresher/reading.py`, `refresher/summarize.py`, `static/dossier.{html,js}`, `static/reading.css`, tests). Phase 4 is partly done: Atlas `?item=`, `?idea=`, `?cluster=`. Not checked: whether the Ideas view's Why-line clean-up has landed.

Endpoints (`app.py:295-304`):

- `GET /api/subject?id=|q=` returns `scope, at, floor, clusters[{id, title, band, relevance, ideas, in_scope, anchor, perspectives, noise, shares_with_main}], edges[{a, b, count, label}], sources{id: {title, source, author, time, url, minutes, hook, gist, cluster, role, repost_of, ideas, other_ideas, trust, shares}}, no_ideas, ideas{id: {title, relevance, cluster, in_scope}}, weights`, merged with `plan()`: `coverage, plan[{source, cumulative}], share, minutes, reachable, marks`.
- `GET /api/source/<id>/context?id=|q=` returns the document plus gist, cluster, role, ideas (with `passages`, `new`, `also`), people, orgs and connected.
- `POST /api/source/<id>/state` and `POST /api/reading`.
- Note: the subject parameter is `id`/`q` on both routes, not `subject=` as the plan writes.

Measured on the two test subjects (subject() takes 0.05–0.07 s):

| | Compressibility… (topic) | hallucination (query) |
| --- | --- | --- |
| main | Truth as Compression Artifact | Truth as Compression Artifact |
| main anchor | the Telegram note «Чтобы LLM не галлюцинировала…» | same |
| Krestnikov's paper | perspective in main | perspective in main |
| adjacent | Mathematics Evolution (anchor Reporting bias), AI Nervous System | same two |
| far | German Vocational Training, Entrepreneurial Dilemma, Wealth and Ownership | same |
| plan | note → Reporting bias → paper → AI HAS A BRAIN | note → paper → AI HAS A BRAIN |
| sources with in-scope Ideas | 5 of 13 | 5 of 13 |
| sources holding a scope Idea | 5 | 4 |

### 5. Search

`dossier.resolve()` (`dossier.py:80`) does three things:

1. An exact title match on a topic or cluster becomes that scope.
2. Otherwise it embeds the phrase, takes every Idea at cosine ≥ `topic_link` (0.6 on Qwen), and takes whole clusters whose centroid is that close.
3. It adds Ideas whose claims contain all the query's words (`store.claims_for`, lexical).

`Scope.ideas` is the matched Idea list and `Scope.central` is each Idea's closeness, so matched Ideas are available. Matched *terms* are not recorded anywhere; only the lexical branch could produce them. `subject()` then re-scores every Idea against the subject vector with its own floor. "Matches" could therefore mean either of two sets: in `Scope.ideas` (4 sources for "hallucination") or above the floor (5). **Decision needed**, since Phase B's header count depends on it. The plan's example "9 of 13 match 'hallucination'" does not hold on this corpus.

### 6. Repost detection

The Atlas "Hide reposts" checkbox filters on `item.repost_of` = `ItemNode.duplicate_of`. Two rules set it:

- At placement: word 3-gram Jaccard of the whole text ≥ `REPOST_SIMILARITY` 0.8 against any item; the later one by event time is the repost (`memory.py:189`).
- In the review: ≥ 70 % of an item's claims match one other item, with at least 3 claims (`memory.py:770`, `REVIEW_DUPLICATE_SHARE`).

`retells` can be exactly `duplicate_of`. The plan's "80 % of Ideas near-duplicates" would be a third rule, and it fails on the only real repost: both CNBC items have 0 Ideas (all 23–25 of their claims are facts about Microsoft and people). Recommend: `retells` = `duplicate_of`, with no new threshold.

### 7. Frontend

- Vanilla JS, no framework or build step. Each page is one IIFE (`static/dossier.js`, about 580 lines), with `el()` and `svg()` helpers.
- Routing is by URL: `/topic/?id=|q=` plus `&read=<id>` for reading mode and `#ideas` for the Ideas view, with `history.pushState`/`replaceState` and a `popstate` → `route()` handler. State lives in module variables, and reading marks are on the server.
- The topic page's cluster graph is hand-drawn SVG with a small seeded force layout (`dossier.js:83`), not Cytoscape. Cytoscape is used only by the Atlas (`assets/map/app.js`, Victor's port: topics, timeline, map, evidence, comparison). "Timeline" and "evidence panel" are Atlas components, not part of the topic page.
- The CSP forbids inline scripts and external origins. The topic CSP allows inline styles.
- There is no browser test tool in the repo (no Playwright or Selenium in `pyproject.toml`). Phase checks at 1440 / 1024 / 390 px would be manual or through Claude in Chrome. Adding Playwright is a new dependency, so ask first.
- Fixtures: `tests/fixtures/make_vault.py` and `tests/fixtures/vault`, plus `fixture-vault/`. `tests/test_subject.py` has 4 tests on them. A 14-source prototype-shaped fixture would be new.

## Phase A checks against today's data

| Check | Today | Why |
| --- | --- | --- |
| One home per source; also-here for multi-cluster sources | home yes (9 sources; the 4 without Ideas have none), also-here not built | |
| One anchor per non-empty cluster, never a retelling | yes for home clusters; a repost cannot be anchor | `subject.py` (`first = max(… i not in reposts …)`) |
| Krestnikov's paper anchors its home cluster | **fails**: the Telegram note anchors | The note has 6 Ideas in that cluster and the paper 7, but the note holds more in-scope relevance (13 Ideas spread over 6 clusters, several close to the subject). The plan's stance rule ("states before discusses") would reverse it only if the note's Ideas count as "discusses". That needs near-duplicates between the two (none: the subject code finds no Idea pair between them even at 0.55) or attribution, which extraction does not record. Depth would favour the paper: 6 of its 9 Ideas carry figures, against 3 of 13 for the note, though the note is the longer text (271 words to 309). |
| The note's link to the paper is `based_on` | possible, through the **link**, not the name | The note links `arxiv.org/abs/2603.11749`, the paper's own URL. The name check fails: the note mentions «Кости Крестникова», which is resolved as a different person from "Konstantin Krestnikov" (diminutive plus genitive across scripts). The people dedup misses it. |
| Trust words only from the person score | yes, if words map from `experts()` rows | Today every person is unscored, so every source would read "unknown" |
| "Named, no profile" gives "unknown" | yes, through PR #37 | No attributed statements gives `importance: None` (unscored), never a low score |

## Gaps

1. No relations between sources: 3 of the 7 link types have no data (finding 3).
2. Every person is unscored until the paid relate-v3 upgrade runs, and track record stays at 0.50 until speakers and origins are reviewed (finding 2). Nothing gives a channel post an author.
3. "Specific claims" has no field. A claim has no entity list of its own (entities are per item). The proxy is a figure in the statement, or a known person or org name appearing in it.
4. Stance now has an input. PR #37's `ClaimNode.attribution.kind` gives `author` → states and `quoted` → discusses. It is `unknown` on every claim in the real vault until relate-v3 runs. Until then the fallback is "date order among near-duplicates", which rarely fires because every Idea has one source.
5. This branch is behind `origin/main` by 7 commits (PR #28 frontend, PR #37 people scoring). Ten of its uncommitted files are also changed there: `README.md`, `refresher/app.py`, `refresher/assets/map/app.js`, `refresher/assets/map/index.html`, `refresher/consolidate.py`, `refresher/dossier.py`, `refresher/static/dossier.html`, `refresher/static/dossier.js`, `refresher/vault.py`, `tests/test_app.py`. Done 28 Oct: committed as ffc3495 and merged with `origin/main` in 9849878 (606 tests and the Node checks pass).
6. Matched terms are not recorded (see Step 0 answer 5, Search).
7. People dedup misses "Кости Крестникова" = "Konstantin Krestnikov". That is extraction or people resolution, out of scope here, but it blocks the name-based `based_on`.
8. Depth is computable now: words from `ItemNode.text`, Ideas, claims with figures. Quintiles over 13 sources put 2–3 sources in each level. The 4 sources without Ideas would all sit in the bottom levels.
9. No browser test tool in Python. PR #37 added Node regression checks (`tests/test_people_filter.cjs` and others), which may serve for layout checks.
10. The prototype file is not in the repo.

## Questions for review

1. **Trust.** On this vault every person is unscored. Options:
   - (a) Run the paid relate-v3 upgrade on the 13 documents first (your call: it costs model usage), then review a few speakers and origins.
   - (b) Ship the word now and let it read "unknown" until then.
   - (c) Drop trust from Phase A and keep depth.

   Also, which `experts()` field gives the word: `track_record` alone, or `track_record` with `evidence_level`?
2. **Olga's questions.**
   - Should source trust come from the writer's row only, or from all speakers quoted in the source?
   - Should a Telegram channel post count its channel as author? The enriched person "Немецкий гастарбайтер" shows the data half exists, but that would change people resolution, which is Olga's area.
3. **Home.** Keep home as "where most of its Ideas are" (stable across subjects, as today), or the plan's role-first rule?
4. **The anchor check.** On this corpus the Telegram note out-covers the paper. Should the check change to "the paper is anchor or perspective in its home cluster", or should depth enter the anchor order before coverage? The latter would make the paper anchor: 6 of its 9 Ideas carry figures.
5. **Retelling.** `retells` = `duplicate_of` (existing rules), with the 80 % Idea rule dropped?
6. **Link types with no data.** Build `disputes / supports / builds_on / cites` now (empty on real data, tested on the fixture), or only `based_on / retells / same_author / shares an Idea`?
7. **What counts as a match** for the Phase B header: in `Scope.ideas`, or above the subject floor?
8. **Storage.** Confirm: in-memory derived values cached per vault state in `app.State`, no files written. Or also a `.cache/` copy?
9. **Browser checks.** Add Playwright (a new dependency), or check layouts by hand / with Claude in Chrome?
10. **Branching.** The reading-first work (Phases 1–3) is uncommitted on `kirill/reading-map`, which is 7 commits behind `origin/main` and overlaps PR #37 in 10 files. Committed and merged with `origin/main` (9849878). Still open: push it and open it as its own PR, then branch Phase A from it.
