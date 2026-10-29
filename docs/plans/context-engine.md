# Refresher: the Context Engine — viewer design

Oct 29, 2026 · @Kirill

## Goal

For a search or a topic, show what has survived to now: the major ideas as they stand today, the one source to read in depth for each, the sources that only lent a fact, the rewrites collapsed, how an idea developed and what it replaced, and who to listen to on this subject. When the user comes back to the same question, show only what arrived since last time, and say what of it matters.

### Why three earlier tries fell short

| Try | What it got right | Where it stopped |
| --- | --- | --- |
| Dossier (main) | Freshness, supersession chains, contested Ideas, a written brief | Binary membership; one "why" line for 36 of 40 Ideas |
| Reading map (`kirill/reading-map`) | Every source placed; rings by marginal coverage; a reading plan | No time: freshness and supersession play no part |
| Thread Navigator (`kirill/thread-navigator`) | Roles per cluster, typed links, depth, trust words, the Read layout | Most link types are empty; every trust word reads "unknown" |

All three hit the same wall, which is in the data, not the page: on the Telegram vault 221 of 222 claim verdicts are NONE and every Idea has one source. Atomic claims (up to 25 per item) rarely match across sources, because one writer's paraphrase of another is not the same atomic statement. So there is nothing to say which source is the base, which is a rewrite and what replaced what.

## Granularity: thesis over claim

The choice was between going deep (a blog post becomes five ideas, which loses the post as a unit) and going shallow (one or two ideas per post, which loses the edges other sources add). We keep both levels:

- **Claim** stays atomic: the unit of evidence, decay and borrowed facts. Nothing about claims changes.
- **Thesis** is new: one to three per source, what the source argues or reports, each resting on its claim ids, with a stance: *original*, *commentary*, *summary* or *retelling*, and the references it cites.
- Relations are judged **between theses across sources**: same thesis, builds on, updates, conflicts. This is where base and rewrite come from.
- A **major idea** is a group of theses related that way. Inside it each source has a role:

| Role | Meaning | Shown as |
| --- | --- | --- |
| Base | Its own thesis, original stance; the earliest or the one others cite; deepest | "Read in depth" |
| Builder | Its thesis builds on the base and adds to it | "Built on by" chips |
| Adjacent | Its thesis is elsewhere, but some of its claims feed this idea | "Lent facts", with the facts listed |
| Rewrite | Same thesis, retelling or summary stance, nothing new | Collapsed: "S5, S6 (+10) retell S3" |
| Superseded | An older thesis an update replaced | "Previously…" sentence and History |

## Who uses it, and for what

Personas are assumptions from one primary user and a teammate; they are validated by the usability test below, not by research.

- **P1, the catch-up practitioner** (primary). Forwards Telegram posts, papers and articles all week. Before a meeting, a decision or a piece of writing, needs the state of a subject in five minutes and the one thing to read in depth.
- **P2, the agent.** Claude, through the handoff: the same brief as citable JSON or markdown.
- **P3, the curator.** The same person in another mode: fixes a wrong base, rejects a supersession.

| Job | When… | I want… |
| --- | --- | --- |
| J0 Catch me up since last time | I return to a question | what arrived since my last brief, what is critical in it and what was noise |
| J1 Catch me up from scratch | I start on a subject | what is current on it, in a few paragraphs |
| J2 What to read | an idea matters | the base, not its rewrites |
| J3 What changed | a view I held may be stale | what replaced it, when and why |
| J4 Whom to listen to | I weigh a claim | who is an authority on this subject, and why |
| J5 Hand it off | an agent works on it | the brief with its sources |
| J6 Capture and trust the filing | I forward something | to know where it went |

### Journey

1. **Capture.** The bot replies: filed under thesis T, retells S, updates U.
2. **Time passes.**
3. **Home** lists the user's query threads: "hallucination · 14 arrived since 12 Oct · 2 critical". Open one, or search something new, which starts a thread.
4. **Brief.** On a returning thread it opens on *Since last stamp* (J0): critical cards, then notes, then noise as counts; below it the updated picture (J1). *Stamp* freezes it.
5. *Read in depth* opens the base in the Reader at its thesis (J2).
6. A history badge opens the History dialog (J3).
7. The People panel (J4).
8. Export or hand off (J5).
9. Mark read, thumbs, "not the base" (P3): these feed trust and the next brief.

## Query threads and stamped snapshots

A query the user comes back to is a **thread**. Each brief on it can be **stamped**: stored as an immutable snapshot (query, time, the items it covered, its major ideas with roles and text, the people ranking). The next brief takes only the sources captured between the last stamp and now, weighted by the query, and sorts each:

| Class | When |
| --- | --- |
| Critical | It updates or contradicts a thesis in the last snapshot; it starts a new major idea with an original thesis; it is a new, deeper base for an idea already there; its author is a top authority on the subject |
| Note | It builds on an idea already there, or lends it facts |
| Noise | It retells a known thesis; it is below the query's relevance floor; it is ephemeral |

Arrival is capture time ("what reached me"); which thesis replaced which still goes by event time. The thread's timeline shows every snapshot and diffs any two: ideas added, updated, superseded, dropped.

## Trust and authority

One computation, two views. Authority is scored per (person or channel, cluster): the role of each source the person wrote (base 1.0, builder 0.6, adjacent 0.2, rewrite 0) times their track record (Olga's `experts()`), plus what flows from others who build on or cite them. A query's people view sums the cluster scores weighted by each cluster's relevance to the query, so the two screens agree. Reach (how often someone is named) is shown beside authority, not mixed into it: a dozen mentions in rewrites weigh less than one citation from the author of a base. A Telegram channel with no named writer is scored as the publisher.

## The viewers

### Home (`/`)
Query threads first: last stamp, arrivals since, critical / note / noise counts. Follow any search. Topics stay below as suggested queries.

### Brief (default tab of `/topic/`; tabs Brief | Sources | Read)
- Header: the query, "as of", and "N major ideas from M sources · K rewrites collapsed · J superseded".
- On a returning thread, *Since last stamp* first, with a Stamp button.
- One card per major idea: the current thesis as title; an agent-written paragraph with citations `[S3]` and borrowed facts `[+S7]`; a "Previously…" sentence when a foundational thesis was replaced; a history badge; *Read in depth* (base, author, date, depth, trust word); builders and lent facts as chips.
- Adjacent sources with the facts taken from each; rewrites behind one expander; open disagreements.
- Constellation (right, sticky): a large circle per major idea with its base at the centre, builders inside, adjacent sources as satellites, rewrites as dots; people around; linked to the cards both ways.
- People: authority on this query with a "why" line, reach as a second mark.

### History dialog
One major idea over time: date, source, step (first stated, restated, built on, updated, contradicted), the one-sentence rationale. Current on top; superseded struck through but kept.

### Thread timeline (`/thread/<slug>`)
Snapshots on a time axis; pick one for the frozen brief, two for a diff.

### Sources and Read
The Thread Navigator's map and list, and the Read layout, unchanged, as the second and third tabs.

## Usability test

Guerrilla, 2–3 participants (teammates), 20 minutes, think-aloud, on the seed and Telegram vaults with one thread stamped a week earlier.

| Task | Success |
| --- | --- |
| "Something changed on X since you last looked. What?" (J0) | Names the critical change in under 1 minute |
| "Which one source would you read on this?" (J2) | Picks the base in under 1 minute |
| "Was anything you knew replaced?" (J3) | Explains the change from the History dialog in under 2 minutes |
| "Whose word would you take on X, and why?" (J4) | Agrees with the top authority, or can say why not |

Issues are rated 1 (cosmetic) to 4 (blocks the task).

## Evaluation

`eval/context/manifest.yaml` lists golden subjects with the expected base, rewrites, superseded theses and top people; `refresher eval-context` reports base@1, rewrite precision and recall, supersession found, the share of brief sentences carrying a valid citation (target 95 %), and a delta check: stamp at T1, the correction that arrives later is critical, the repost noise, an off-query item absent.
