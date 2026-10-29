# Thread Navigator — Implementation Plan

Oct 28, 2026 · @Kirill

## How to use this plan

Hand this doc to Claude Code as a Markdown file and run it one phase at a time; it builds on the earlier plan Refresher: reading-first topic page — implementation plan, which must be in the repo too.

1. Export this doc as Markdown to `docs/plans/thread-navigator.md`.
2. Export the reading-first topic page plan to `docs/plans/reading-first-topic-page.md`.
3. Save the prototype page as `docs/prototypes/thread-navigator.html`. It shows the intended look and behaviour; its 14 sources and links are invented.
4. Start Claude Code with discovery only:

```
Read docs/plans/thread-navigator.md and docs/plans/reading-first-topic-page.md.
Do Step 0 (discovery) only. Write the findings to
docs/plans/thread-navigator-findings.md and stop. Do not change code.
```

5. After you review the findings, run each phase with: `Implement Phase <X> of docs/plans/thread-navigator.md. Stop when its checks pass and list every decision you had to make.`

### Ground rules for Claude Code

- **Reuse before building.** Ideas, clusters, shared-idea relations, people, organisations, subject scoring and the reading plan already exist or come from the reading-first plan. Trust comes only from Olga's person scoring; never add a second trust model.
- **Derived, not extracted.** Everything this plan adds is recomputable from existing data. Store it in its own tables or columns with a `computed_at`, never overwrite extraction output.
- **Same words as the reading-first plan:** subject, source, idea, cluster, anchor, perspective, noise. The prototype says core / adds / around for the same three roles.
- **One phase per branch and PR.** Each phase leaves the app working.
- **Ask before** a migration that alters an existing table, any change to Olga's scoring code, removing an existing view, or adding a dependency.
- **Checks run on real data.** Unit tests use the fixture corpus; acceptance checks run on the two test subjects, "Compressibility of Consistent Patterns" and "hallucination".

## Goal and scope

The topic page gets one complete, sliceable list of every source found for a subject, and it makes depth, trust and the connections between sources visible. Today the reader only ever sees one angle at a time and cannot see the whole result set.

| Feature | What the reader gets | Where it shows |
| --- | --- | --- |
| All results list | Every matching source in one table, grouped by cluster, role, trust, type or topic, with a column per cluster showing where each source sits | New view beside map mode |
| Home and also-here | A source with several ideas appears in each cluster its ideas belong to: solid in its home, hollow elsewhere | List, cluster blocks, cluster graph |
| Depth and trust | Depth as a 1–5 bar; trust as a word (trusted, known, unknown) from Olga's person scoring. Kept as two signals, never merged | List rows, cards |
| Roles per cluster | Anchor, perspective or noise computed per cluster, so one source can anchor one cluster and be noise in another | Cluster blocks, list |
| Typed links and a thread | Links named by kind (based on, disputes, supported by, builds on, cites, same author). Retellings fold into a count. The path taken shows as a strand you can step back along and branch from | Reading mode context panel, thread strand |
| Depth rail | All › Topic › Cluster › Source › Idea, with counts and position at each level | Under the header, in every view |

Reference: the prototype page Thread Navigator (`docs/prototypes/thread-navigator.html`).

Out of scope: changes to extraction, clustering or Olga's scoring logic, and Atlas changes beyond Phase 4 of the reading-first plan.

## Step 0 · Discovery

Before any code, Claude Code maps what exists and writes `docs/plans/thread-navigator-findings.md`. Every table and field name in this plan is a placeholder until then.

1. **Data model.** Find the models and tables for sources, ideas, claims, clusters, topics (accepted and provisional), idea-to-cluster membership, and relations between ideas or claims (their types, and the "later points to earlier" direction the map legend describes). Do the same for people, organisations and their mentions with roles.
2. **Passage anchors.** Check whether ideas or claims store the passage they came from (offsets or paragraph ids). If not, estimate the work to add them, since Phase E jumps to them.
3. **Olga's person scoring.** Find it with `git log --author=Olga` and a search for person score or trust. Record:
   - where it is computed and stored, and when it is recomputed
   - its inputs and its output range (the Ideas view shows "authors trusted 0.50", so 0.5 may be the default)
   - whether a score is global or per topic
   - what happens for people shown as "named, no profile", and for channels or outlets with no person
   - its public interface and existing tests
4. **Reading-first plan status.** Which phases have landed, whether `GET /api/subject` exists, and what it returns today (bands, roles, reading plan, edges).
5. **Search.** How a free query matches sources (embedding, keyword or both), and whether it can return the ideas that matched.
6. **Repost detection.** How the "Hide reposts" filter decides what a repost is. Retellings in this plan should reuse it.
7. **Frontend.** Framework, routing, state handling, the graph library, and the components behind the Topics page: timeline, cluster map, evidence panel.

The findings file opens with a table mapping each term in this plan to the real model and field, then lists gaps and questions. Claude Code stops there for review.

## Data model additions

Three new derived records plus one cache, all recomputed from existing data after each consolidation run. Names are suggestions; follow the repo's conventions.

| Record | One row per | Fields | Computed from |
| --- | --- | --- | --- |
| `source_placement` | source × cluster where the source has at least one idea | `source_id`, `cluster_id`, `role` (anchor / perspective / noise), `stance` (states / discusses), `idea_ids`, `passage_anchor` of the first idea, `new_idea_count`, `is_home`, `computed_at` | Idea-to-cluster membership; marginal coverage inside the cluster |
| `source_signals` | source | `depth` (1–5), `depth_inputs` (words, ideas, specific claims), `trust` (trusted / known / unknown), `trust_score` (raw value from person scoring), `trust_basis` (author / outlet / none), `computed_at` | Text length, extraction output, Olga's person scoring |
| `source_link` | ordered source pair with at least one relation | `from_source_id` (later), `to_source_id` (earlier), `type` (based\_on, cites, supports, disputes, builds\_on, retells, same\_author), `label` (one line of why), `evidence_ids` (ideas or claims), `strength` | Idea and claim relations aggregated to source pairs; people resolution |
| `subject_result` (cache) | subject × source | `subject_key`, `source_id`, `match_score`, `matched_idea_ids`, `matched_terms` | Subject scoring from the reading-first plan |

The stored roles are cluster-level and independent of any subject. When a subject is set, roles are recomputed on request over the in-scope ideas only, as in computation 4 of the reading-first plan. At 13 sources and 105 ideas this costs nothing; cache it per subject like the subject payload.

## Scoring

Every value traces back to existing data, and trust comes only from Olga's person scoring. Thresholds live in one config file so they can be tuned without code changes.

1. **Depth, 1–5.** Combine word count (log scale), number of extracted ideas, and number of specific claims (ones with figures or named entities). Map the result to corpus quintiles so the five levels are used. A retelling is capped at 1. Keep the inputs so the UI can explain the bar: "9,800 words · 14 ideas".
2. **Trust.** Read each author's score through the person scoring's public interface. Source trust is the highest author score; confirm with Olga whether the mean fits better. Use an outlet or channel score only if her scoring covers organisations. Map to words with two thresholds, starting so that roughly the top quarter of current sources reads "trusted". A person shown as "named, no profile" gives "unknown", never a low score. Show the word, never the number, in the reading UI.
3. **Stance per idea.** "States" when the source asserts the idea and is its earliest occurrence among near-duplicates. "Discusses" when the idea is attributed to someone else ("X says") or relates to an earlier claim in another source. If extraction does not record attribution, fall back to date order.
4. **Role inside a cluster.** Greedy marginal coverage over the cluster's ideas, the same method as computation 4 of the reading-first plan, run per cluster. Order candidates by stance (states first), coverage, depth, trust, then earliest date.
   - Anchor: the first source picked; exactly one per cluster.
   - Perspective: adds at least one idea not already covered.
   - Noise: adds none, or is a retelling.
   - Store `new_idea_count` and the uncovered ideas; they feed the "Adds: …" line.
5. **Home.** Exactly one placement per source: the best role (anchor, then perspective, then noise), then "states" over "discusses", then most ideas, then highest relevance to the subject when one is set.
6. **Order in lists.** Rows sort by role, then by position in the reading plan from the reading-first plan. That plan's tie-break becomes trust, then depth, then freshness.
7. **Typed links.** Aggregate idea and claim relations to source pairs, directed from the later source to the earlier one.
   - `disputes`, `supports`, `builds_on`, `cites`: from relation types, with disputes taking precedence when a pair has several.
   - `based_on`: the later source names the earlier source's author or links it, and shares at least one idea.
   - `retells`: at least 80% of the later source's ideas are near-duplicates of the earlier one's, and it adds no new idea. Reuse the repost detection.
   - `same_author`: one resolved person wrote both.
   - Label: the shared idea or the relation's evidence, cut to one line.
8. **"Deeper" flag on a link.** Set when the target's depth is higher, or its trust word is higher, than the current source's.

## API

Extend the reading-first endpoints rather than adding parallel ones; only the results list and the depth rail need new routes.

| Endpoint | New or extended | Returns |
| --- | --- | --- |
| `GET /api/subject?id=…` or `?q=…` | Extended | Adds per cluster: placements (source, role, stance, home or also-here). Adds per source: depth, trust word, home cluster |
| `GET /api/subject/results?id=…&group_by=…&scope=…&only_matches=…` | New | Header counts (matching and total); slices (group name, count, role counts); rows (source, its placement for that group, depth, trust, type, matched terms, and `places`: cluster, role and home flag for every cluster it sits in) |
| `GET /api/source/{id}/context?subject=…&cluster=…` | Extended | Adds ideas with cluster, role, stance and passage anchor; typed links with label, direction and deeper flag; retold-by count and list |
| `GET /api/path?topic=…&cluster=…&source=…&idea=…` | New | The depth rail: name, counts and position among siblings for each level |

- `group_by` takes `cluster_home`, `cluster_all`, `role`, `trust`, `type` or `topic`. With `cluster_all`, a source appears once per placement, and the header also reports the placement count.
- `scope` takes a topic id; empty means all topics.
- The thread lives in the URL only for now: `&thread=<source ids>&pos=<index>`. No server storage in this version.
- Target: under 300 ms server time on the 13-source corpus; design for 500 sources by indexing placements by cluster and subject.

## Phases and checks

Five phases, A to E, each shippable alone. A comes first because every view reads its values; B comes next because the missing full list is the main complaint. Phases B to E need Phase 3 of the reading-first plan (reading mode beside the page) where they open a source.

### Phase A · Signals, placements and links (backend only)

Depth, trust, stance, roles, home, typed links, and the two API changes that expose them.

- [ ] Every source has exactly one home; sources with ideas in several clusters also have also-here placements
- [ ] Every non-empty cluster has exactly one anchor; no retelling is ever an anchor
- [ ] "Truth as a Compression Artifact in Language Model Training" (arXiv, Krestnikov) is the anchor of its home cluster
- [ ] If the Telegram note «Чтобы LLM не галлюцинировала…» names Krestnikov, its link to his paper is `based_on`
- [ ] Trust words come from Olga's person scoring; a search of the diff finds no other trust computation
- [ ] People shown as "named, no profile" give "unknown", not a low trust word

### Phase B · All results list

A view beside map mode that lists every source found, with:

- a header line: "9 of 13 sources match 'hallucination'"
- group-by, scope and "matches only / all, matches first" controls
- a slices bar showing how the result splits across groups; clicking a segment scrolls to that group
- group headers with counts per role
- rows with depth, trust, type and matched terms
- one column per cluster: filled mark for home, hollow for also-here, size by role

A row click opens the source in reading mode. A mark in a cluster column opens map mode at that cluster with that placement selected.

- [ ] On "hallucination", every matching source appears in the list, and the header count equals the rows shown
- [ ] Grouped by "cluster, every place", a multi-idea source appears once per cluster, marked home or also-here
- [ ] Changing group-by keeps the selected source selected and in view
- [ ] The slices bar segments add up to the rows shown
- [ ] View, grouping and scope survive a reload through the URL
- [ ] At 390 px wide only the table scrolls sideways, never the page

### Phase C · Depth rail

A strip under the header: All › Topic › Cluster › Source › Idea §n, each level with its counts and position ("3 of 8").

- [ ] The rail matches the current selection in list, map and reading mode
- [ ] Clicking a level goes there; clicking the cluster level from reading mode returns to map mode with that block open

### Phase D · Cluster blocks and graph

Cluster blocks gain also-here rows (hollow marker, "home: \<cluster>") plus depth bars and trust words on anchor and perspective rows; noise stays a count. In the graph, the selected cluster opens in place into its sources: anchor at the centre, perspectives on a middle ring, noise on the outer ring. Hovering a source draws dashed lines to its other places.

- [ ] Hovering a multi-idea source shows every cluster it sits in
- [ ] Labels sit outside the circles and do not overlap at 1440 × 900 on the test corpus
- [ ] The graph fits the selection on load; no node is cut off at the edge

### Phase E · Typed links and the thread

The context panel's Connected section groups links by type, each with its one-line label and a "deeper" flag; retellings fold into "Retold by N". Choosing a link opens that source in reading mode and adds a station to the thread strand. Clicking an earlier station goes back; choosing a link from an earlier station starts a branch from there.

- [ ] Every link shows its type word and a reason taken from the data
- [ ] Retellings never appear as separate link options
- [ ] Reloading restores the thread and the current station
- [ ] Opening a source from an also-here placement scrolls to the passage that put it there

## Testing and performance

Unit tests run on a small fixture corpus whose answers are known; acceptance checks run on the real corpus.

- **Fixture corpus.** About 14 sources shaped like the prototype: sources with ideas in two or three clusters, two retellings of one paper, one dispute, one author with two sources, and people with no profile.
- **Invariants.** Exactly one home per source. Exactly one anchor per non-empty cluster. A retelling is never an anchor. Every link points from a later source to an earlier one.
- **Person scoring.** Unit tests stub it through its public interface; one integration test calls the real implementation.
- **API.** Snapshot tests of `/api/subject/results` for each `group_by` value.
- **End to end.** List → reading mode → pull two links → step back → branch → reload. Use the repo's existing browser test tool if there is one.
- **Speed.** Under 300 ms server time on the 13-source corpus. On a synthetic 500-source corpus: under 1.5 s server time, and the list renders in under 200 ms, virtualising rows beyond 200.

## Open questions

The first four need Olga; the rest are product decisions.

- What range does the person score use, and is 0.5 the default for anyone without evidence?
- Is the score global or per topic, and does it cover outlets and Telegram channels?
- For a source with several authors, should trust be the highest author score or the mean?
- Where should the "trusted" and "known" thresholds sit?
- The reading-first plan keeps trust out of the context panel. This plan shows a trust word in the list and on cards. Confirm the word is acceptable there.
- Should roles follow the subject (in-scope ideas only) by default, or always show the cluster-level role?
- Is 80% shared ideas the right cut-off for a retelling?
- Should a thread be saveable and named later, or is the URL enough?
