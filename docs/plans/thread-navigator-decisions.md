# Thread Navigator — decisions made while building it

28 Oct 2026 · branch `kirill/thread-navigator`, on top of `kirill/reading-map`. One branch with one commit per phase (A–E) rather than five stacked branches, so it can be compared with reading-map as a whole. Measured on `.local-vault/telegram` with the Qwen vectors.

Each decision says where this branch differs from reading-map, so the two approaches can be compared.

## Data and scoring (Phase A)

1. **Storage.** Nothing is written. `refresher/navigator.py` computes everything from the replayed store with the subject, and `app.State` caches it with the subject. The only addition to replay is `store.extras`, each item's extraction links and triage kind, which the store did not keep. It sits next to `store.summaries`. There is no schema change.
2. **Trust word** comes only from `store.experts()` (Olga's PR #37) for the source's writer, scored in the subject's in-scope Ideas:
   - unscored or no writer → **unknown**
   - scored, track record ≥ 0.6 and evidence "developing" or "established" → **trusted**
   - otherwise → **known**

   The thresholds are constants at the top of `navigator.py`; with no attributions yet they could not be calibrated. Quoted speakers do not count towards a source's trust, only its writer.
   - Real data: 13 of 13 sources read "unknown", because every relation record is still relate-v2.
3. **Depth.** The mean of three inputs, each scaled by its corpus maximum: log of the word count, Ideas, and specific claims (a figure, or the name of a person or organisation the source mentions). It is then cut at corpus quintiles, and a repost is 1. Depth is subject-independent.
4. **Stance.** Attribution decides when present (author → states, quoted → discusses). Otherwise a source "discusses" an Idea when an earlier source holds it or a near-duplicate (cosine ≥ 0.85), or when its claim relates to an earlier claim. A placement's stance is the majority of its Ideas there, with ties counting as "states".
5. **Roles per cluster.** Greedy cover, candidates ordered as the plan says: stance, coverage, depth, trust, earliest.
   - Coverage counts in-scope relevance. A cluster with no in-scope Idea falls back to all its Ideas counted equally, so every non-empty cluster has an anchor.
   - The cluster-level role is also kept (`cluster_role`).
   - A repost is never a candidate: it is always noise.
   - Differs from reading-map, which has one role per source, in its home only, with the anchor chosen by the source's in-scope weight overall.
6. **Home.** The plan's order (best role, "states", most Ideas there, relevance), with one rule ahead of it: a cluster the source shares with another source beats one only it is in.
   - **Deviation from the plan.** Without it, "Reporting bias" would be homed in "Slide Footer Info", a cluster made only of its slide's footer Ideas.
   - With it, Reporting bias lives in Mathematics Evolution, as the reading-first check wants.
   - A repost lives with its original.
   - Differs from reading-map, where home is "most Ideas, a single-source cluster counting half".
7. **Results on the checks.**
   - Krestnikov's paper anchors Truth as Compression Artifact on both subjects; it was a perspective in reading-map.
   - The Telegram note's home is LLM Internet Access Risks, where it is the anchor. In Truth it appears as a perspective, also here. That follows from "best role first".
8. **Link types**, one per source pair by precedence: disputes > retells > based on > supports > updates > builds on > cites > same author > shares an Idea.
   - `updates` (from SUPERSEDES) and `shares an Idea` (a shared Idea, or a near-duplicate at the cluster-neighbour cosine of 0.55) are additions. Without them the real corpus has almost no links.
   - A source keeps its 12 strongest "shares an Idea" links, the cap reading-map's connected list already used.
9. **based_on / cites.** "Links it" means one of the extraction links matches the other source's URL, compared without scheme, `www.`, query or trailing slash, or the other source was captured from this one's link. "Names its author" compares surnames across scripts (Cyrillic transliterated, first 6 letters), so «Кости Крестникова» matches Konstantin Krestnikov. Then:
   - "shares something" means a shared Idea, a near-duplicate, or the same cluster. That gives based on, else cites.
   - Real data: the note → paper link is **based on**, through the link.
10. **Link direction.** Later to earlier by event time, except that a source that links or names another is the later one by definition. The arXiv paper has no publication date, so its capture time made it look newer than the note that links it.
11. **"Deeper"** is set when the other source has a higher depth or a higher trust word, as the plan says.
12. **What matches** (Phase B's header): a source holding an Idea above the subject's floor. "hallucination": 5 of 13, not the prototype's 9.
13. **Matched terms**: query words of 3 or more letters, cut to the word minus its last 3 letters (at least 4), found in the source's text or claims. Topics have none, so the column shows the count of matching Ideas instead.

## Pages (Phases B–E)

14. **All results** is a view beside the map (`?view=list&group=&scope=&all=1`), not a separate page. The header with "Start reading" stays above it.
    - "Type" is the triage kind (post, thought, newsletter_digest…), else the channel.
    - "Topic" groups a source under the topic holding most of its Ideas.
15. **The depth rail** reads `GET /api/path`.
    - It takes the subject as `id=` or `q=`, as `/api/subject` does, rather than the plan's `topic=`.
    - It is not sticky, so it does not fight the reading columns' own sticky rails.
16. **Opened from a cluster**: reading mode keeps `&in=<cluster>` when a source is opened from a cluster that is not its home. The rail and eyebrow use it, and the reader scrolls to that placement's first passage.
17. **Cluster blocks.**
    - They list every placement, so a block counts its also-here sources too ("3 sources · 2 also here from other clusters").
    - The one expanded card is shown only in the next source's home.
18. **The graph.**
    - The open cluster is the one you are in, or main on arrival.
    - Its rings leave a gap at the top for its name.
    - Labels are placed from their measured size, and one that cannot be placed stays a tooltip.
    - The view box is fitted to what is drawn.
19. **The thread.**
    - Branching from an earlier station drops the stations after it; only the current path is kept, in the URL.
    - Opening a source other than by a link starts a new thread.
20. **Link words** are read from the current source's side: based on / basis for, cites / cited by, disputes / disputed by, supports / supported by, updates / updated by, builds on / built on by.

## Not met or not done

- **Speed.**
  - Synthetic 500-source corpus: `subject()` takes about 2.2 s, against the 1.5 s target. About 1.5 s of that is the navigator (links and near pairs on a very dense corpus). The rest is the existing query expansion and connected list.
  - Real corpus: 24–40 ms, within the 300 ms target.
- **List rendering:** the list does not virtualise rows beyond 200.
- **Browser tests:** there is no browser test in the repo. Layout and flows were checked with headless Chrome over the DevTools protocol, from a script outside the repo.
- **Fixture corpus:** there is no 14-source fixture shaped like the prototype. The tests use hand-made inputs for the pure functions and the existing bridge store for end to end.
