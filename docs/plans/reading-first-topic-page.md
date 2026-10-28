# Refresher: reading-first topic page — implementation plan

Oct 28, 2026 · @Kirill

## Goal

The topic page becomes a reading of a subject organised by clusters: where to start, which source is the anchor, what sits next to it, and what is noise to read later. The Atlas stays as the place to verify a claim, not the place to start.

A reader arriving on a subject should be able to answer, without scrolling:

- Which clusters form around this subject, and how they connect to each other
- Which single source to read first, and which is the deepest in each cluster
- Which sources are perspectives on the anchor, and which are noise
- How much of the subject they will have covered after each step

### What is wrong today

| Where | Problem | Seen on 28 Oct |
| --- | --- | --- |
| Topic page, first version | Binary membership: a source is in the topic or invisible | "hallucination" showed 4 of 13 sources |
| Atlas | No query; corpus-wide inventory; claim level | Claims view: 160 nodes, 1 relation |
| Topic page, rings version | Five competing reading entry points | Next, Start here, Skim the path, per-thread card-by-card, per-card Read |
| Topic page, rings version | Orientation map at the bottom, after Periphery | Map seen only after everything it orients |
| Topic page, rings version | Conflicting numbers on one card | Deepest · 15 Ideas next to Adds 12% |
| Topic page, rings version | Adjacent looks more valuable than core | Adjacent rows: Adds 22 new Ideas |
| Topic page, rings version | Rings encode only distance; relations between clusters invisible | Sectors with no meaning for angle |
| Ideas view | 36 of 40 entries share one identical Why line | Ranking explanation carries no information |
| Reader | Full text replaces the whole page | Context and position in the reading are lost |
| Atlas sidebar | Too narrow, all details at once | People, organisations, connected articles were useful but unreadable |
| Ranking | Core ring inherits accepted-topic membership | Reporting bias (maths) filed under AI Nervous System |

## Design principles

Every decision below follows from these seven rules; when a detail is unclear, check it against them.

1. **The cluster is the unit of the page.** Not the source, not the ring. Each cluster is a block with one fixed internal shape: anchor, perspectives, noise.
2. **One reading order.** A single primary action, "Start reading", in the header. Each card has one secondary action. Every other entry point is removed.
3. **One number per card, relative to the subject.** Marginal coverage: "+38% of the subject". Roles are words, not counts: *anchor*, *perspective*, *noise*.
4. **Noise is a count, never a list.** It sits inside the cluster it belongs to, as "1 noise source · show".
5. **Graph of clusters, not rings.** Nodes are clusters, edges are shared ideas, the main cluster is pinned at the centre. Distance from the subject is carried by fill, so position is free to show relationships.
6. **Reading never leaves the map.** Full text opens beside the reading plan, not in place of it. Context (people, organisations, connected articles) is one click away, never all shown at once.
7. **Atlas is for verification.** It is reached from a source or an idea, already filtered to its neighbourhood.

## Page layout: map mode

The page has three zones top to bottom: header, orientation (cluster graph beside the reading plan), and the cluster blocks. The people row moves out of the header into the reader's context panel.

### Header

- Title, then one counts line: "12 sources · 3 clusters on the subject · 4 adjacent · 3 noise"
- Coverage bar with "about 8 min to 80%"
- One primary button: **Start reading** (later: **Continue · #3**)
- "Another subject…" search stays, right-aligned, smaller

### Orientation zone (above the fold)

**Cluster graph**, left, about 280 × 220 px.

- Node = cluster. Size = ideas it holds. Fill = relevance: solid (main), tinted (adjacent), outline (far)
- Main cluster pinned at centre; force layout places the rest
- Edge = shared ideas between two clusters; stroke width by count; hover shows the top shared idea
- State: current cluster ringed, read clusters filled grey, the next edge drawn stronger
- Click a node scrolls to its block and opens it
- Hard cap: 8 nodes. Beyond that, far clusters merge into one "other" node

**Reading plan**, right.

- Numbered list of the sources that reach 80%, normally 3–6 rows
- Each row: number, title, cluster tag, cumulative coverage bar, cumulative %
- Click a row opens that source in the reader

### Cluster blocks

Blocks are ordered by cluster relevance to the subject. Only the main cluster is open on arrival; the others show their header and anchor row only.

Each block, in this order:

1. **Header:** cluster name, role tag (*main* / *adjacent*), "2 sources · 22 ideas", and for adjacent clusters "shares 3 ideas with main"
2. **Anchor:** the source with the highest marginal coverage in the cluster. Expanded card when it is the next unread in the plan; otherwise a row
3. **Perspectives:** one row each: number in plan (if any), title, *perspective*, +N%, chevron to expand gist
4. **Noise:** one line, "1 noise source · show". Hidden list until clicked
5. **Transition edge:** "connects to *Maths evolution* through 'counting solved tasks is a poor measure'". Only drawn when the next block shares at least one idea with this one

### Far sources

One line at the end: "4 sources from other clusters near this subject · 3 with no ideas extracted · show". Expanded, it lists rows sorted by overlap with the subject, each with "shares N ideas with #k".

### Card rules

- Only one expanded card on screen. Marking a source read or skimmed collapses it to a row and expands the next one in the plan
- Expanded card: meta line, title, gist (max 4 lines, then "more"), "New here" (2 ideas + "and N more"), Read · Skim, "+38% of the subject"
- Row: state dot, title, role, +N%, chevron
- Removed from cards: "Deepest · N Ideas", "Adds N new Ideas" on adjacent rows, "Mark skimmed" as a separate button (Skim marks it)

## Reader: reading mode

Opening a source no longer replaces the page. The page switches to three columns: the reading plan shrinks to a left rail, the text takes the centre, and a context panel holds people, organisations and connected articles on the right. Position in the reading is always visible, and details are there without crowding the text.

[embedded content: map mode and reading mode · one panel for context]

Opening a source moves from map mode to reading mode; the plan stays on the left, context on the right, and Esc returns to the same place in the map.

### Columns (desktop, 1280 px and wider)

| Column | Width | Holds |
| --- | --- | --- |
| Plan rail | 240 px | Mini cluster graph (160 px), reading plan with the current source highlighted, read/skimmed state per row |
| Text | 640–720 px, centred | Title, meta, gist, full text. Ideas extracted from this source get a quiet underline; click opens a popover with "also in: …" |
| Context panel | 360 px | People, organisations, connected articles, ideas, Atlas link |

The Atlas sidebar failed because it was narrow and showed every section open at once. The context panel is wider, its sections are collapsed to one summary line each, and every item says how it relates to *this* source.

### Context panel

Sections, in this order. Each collapsed section shows its count and the top 3 items as chips; one section is expanded at a time. **Connected** is open by default.

1. **Connected articles.** Grouped into "same cluster" and "other clusters". Each row: title, cluster tag, the shared idea that links them (one line), read state. Click opens it in the reader and adds it to the plan rail as a detour
2. **People.** Avatar, name, role. One line: *wrote this*, *quoted here*, or *discussed here*, plus "in 3 other sources". Click highlights their mentions in the text; second click opens a person card inside the panel (not a modal)
3. **Organisations.** Same pattern as people: name, one line on its role in this source, "in N other sources"
4. **Ideas in this source.** Split into "new to you" and "already covered". Each idea links to the passage in the text
5. **Check in Atlas ↗.** Opens the Atlas filtered to this source's neighbourhood

Items never show trust scores, freshness, or provisional flags here; those belong in the Atlas.

### Behaviour

- URL carries the state: `/topic/?id=…&read=<source id>`. Back and Esc return to map mode at the same scroll position
- Bottom bar in the text column: **Mark read** · **Skim** · "Next: #3 Reporting bias ▸"
- When the next source is in another cluster, the bottom bar shows the transition edge: "Next cluster: Maths evolution, through 'counting solved tasks is a poor measure'"
- Keys: J / K next and previous in the plan, R read, S skim, Esc back to map
- Reading progress updates the plan rail and the mini graph live

### Narrower screens

| Width | Change |
| --- | --- |
| 1024–1279 px | Context panel becomes a drawer, opened from a button showing its counts ("3 people · 5 connected") |
| 768–1023 px | Plan rail collapses to a progress strip at the top: plan numbers as dots, current one labelled |
| Below 768 px | Single column; context panel opens as a bottom sheet |

## Backend and data

Most of the data already exists (ideas, clusters, shared-idea relations, people). What changes is that everything is scored against the subject instead of inherited from accepted-topic membership, and three new derived values are computed: cluster relevance, marginal coverage order, and edge labels.

### Computations

1. **Subject vector.** For an accepted topic: centroid of its core ideas. For a free query: embedding of the query, expanded with the top 10 matching ideas
2. **Idea relevance.** Cosine similarity of each idea to the subject vector, 0–1. Ideas below a floor (start at 0.35) are out of scope for coverage
3. **Cluster relevance.** Relevance-weighted share of a cluster's ideas above the floor. Bands: main (highest), adjacent (shares ≥ 1 idea with main or relevance ≥ 0.5), far (the rest)
4. **Source role inside its cluster.** Anchor = highest coverage of in-scope ideas. Perspective = adds ≥ 1 in-scope idea not in the anchor. Noise = adds none, or has no ideas extracted
5. **Reading plan.** Greedy set cover over in-scope ideas, weighted by relevance: pick the source adding the most uncovered weight, repeat until 80% or 6 sources. Ties broken by trust, then freshness. Already-read sources count as covered
6. **Edges between clusters.** For each pair: count of shared ideas (same idea or near-duplicate above 0.85) and the label = the shared idea with the highest relevance to the subject
7. **Entities per source.** People and organisations with a role: author, quoted, discussed. Count of other sources each appears in
8. **Connected articles per source.** Sources sharing ≥ 1 idea, with the top shared idea as label, split same cluster / other clusters

The fix for "Reporting bias" sitting under AI Nervous System comes from step 3–4: a source belongs to the cluster its ideas belong to, and its place on the page comes from relevance to the subject, not from the accepted topic.

### Endpoints

| Endpoint | Returns |
| --- | --- |
| `GET /api/subject?id=…` or `?q=…` | Header counts, coverage, clusters (id, name, band, relevance, idea count, source ids by role), edges (from, to, count, label), reading plan (ordered source ids + cumulative %) |
| `GET /api/source/{id}/context?subject=…` | Gist, full text, ideas with "new to you" flag and passage offsets, people, organisations, connected articles |
| `POST /api/source/{id}/state` | read / skimmed; response returns the recomputed plan and coverage |

The subject payload for 13 sources and 105 ideas is small; compute on request and cache per subject until the next consolidation run.

## Delivery in four phases

Each phase ships on its own and is checked on the two test subjects: the accepted topic "Compressibility of Consistent Patterns" and the free query "hallucination". Phase 1 comes first because no layout fixes a wrong ranking.

### Phase 1 · Scoring against the subject

Backend computations 1–6 and `GET /api/subject`. No UI change yet beyond reading from the new payload.

- [ ] All 13 sources appear somewhere on both test subjects
- [ ] "Reporting bias" is placed in the maths cluster, not AI Nervous System
- [ ] On "Compressibility…", the main cluster is Truth as compression and the plan starts with one of its two sources
- [ ] The three Microsoft-buyout and gastarbeiter sources are classified noise or far

### Phase 2 · Map mode

Header, cluster graph, reading plan, cluster blocks, far sources line. Old Threads, Adjacent, Periphery sections and the ring map are removed.

- [ ] One primary action on the page; no other "Next", "Start here", "Skim the path" or per-thread walkthrough
- [ ] Graph and plan are above the fold at 1440 × 900
- [ ] Exactly one expanded card on arrival
- [ ] Every card shows exactly one number, as "+N% of the subject"
- [ ] Each transition edge names a real shared idea; no edge where there is none

### Phase 3 · Reading mode

Three-column reader, context panel, URL state, bottom bar, keys.

- [ ] Opening a source keeps the plan rail visible; Esc returns to the same scroll position
- [ ] Context panel shows one expanded section at a time, Connected first
- [ ] Every person, organisation and connected article carries a one-line relation to this source
- [ ] Marking read updates the rail, the mini graph and coverage without a reload
- [ ] Drawer and bottom-sheet variants work at 1024 and 390 px wide

### Phase 4 · Atlas and Ideas clean-up

- [ ] "Check in Atlas" lands on the neighbourhood of the source, not all topics
- [ ] Atlas Claims view opens from a cluster, not as a top-level tab
- [ ] Ideas view drops the "Why #N" line when it is identical across entries, and groups ideas by cluster

## Removals and open questions

### Removed from the topic page

| Element | Replaced by |
| --- | --- |
| Radial ring map | Cluster graph |
| "Start here" block and its duplicate in Threads | Anchor of the main cluster, expanded |
| Next link in header, Skim the path, per-thread card-by-card | One Start reading / Continue button |
| Adjacent and Periphery sections | Adjacent cluster blocks and the far sources line |
| "Deepest · N Ideas" chip | Role word: anchor |
| People row in the header | People section in the context panel |
| Full-page reader | Reading mode with plan rail and context panel |
| Sources map and People map dialogs | Cluster graph and the context panel |

### Open questions

- Where should a detour (a connected article opened from the context panel) sit in the plan: appended at the end, or inserted after the current source?
- Should the relevance floor (0.35) and the 80% target be user settings or fixed?
- For a free query with no accepted topic, is the cluster name enough as a block header, or does it need a one-line summary?
- Should skimmed sources count fully, half, or not at all towards coverage?
- At what corpus size does the 8-node graph cap start hiding clusters people need, and is "other" the right merge?
