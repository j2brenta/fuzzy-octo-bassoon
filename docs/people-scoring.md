# People importance in the collected knowledge

`people-v3` extends the existing file-backed graph; no Neo4j adapter or extra service is required.
The new **People** mode at `/atlas/` (also reachable through `/graph/`) ranks people for all subjects, an accepted topic, or a provisional cluster.
Each ranking includes the score parts, exact attributed statements, source counts, professional evidence URLs,
and a rationale. Mentions, facts *about* a person, the document writer, and its forwarder are distinct.

## Attribution and compatibility

The existing relation model call now returns `attribution` per claim: `author`, `quoted`, or `unknown`.
Quoted speakers must reference a supplied person; their canonical name or source-grounded alias must occur
in the cited source passages. At least one attribution passage must also support the claim. Author attribution
requires an existing named `author_hint`; no author is inferred from a site title. Every span is rechecked on replay.
Organizations are not silently treated as people. Ambiguous, multiple or unnamed speakers remain unknown.
This is model-inferred, source-grounded attribution, not external fact verification or independent attribution review.
Name-only identity uses existing graph name/alias resolution until the user reviews statements with profile URLs; same-name identities stay unresolved until then.

Attribution lives in versioned `relations/<item>.json` alongside the model's existing judgments. No raw text,
claim IDs, extraction or review artifacts are rewritten. Old records load without attribution and stay unscored
for people importance. Source-author trust on legacy records remains available for compatibility.

`refresher relate --vault VAULT` upgrades old model verdicts to `relate-v3` once, using the normal bounded model
pipeline and incurring model usage. New submissions receive attribution during placement. `GRAPH_RELATOR=lexical`
keeps attribution unknown. Rebuilds, viewing the graph, and scoring never call a model or web service.

## Deterministic formula

```
subject_relevance = distinct eligible Ideas attributed to the person / eligible Ideas in the subject
track_record = (1 + checked_distinct_peer_origins + user_up) / (2 + checked_distinct_peer_origins + user_up + user_down)
importance = subject_relevance * (0.6 * track_record + 0.4 * professional_relevance)
```

A user-pinned trust value overrides track_record. Unknown professional relevance uses neutral **0.5**, while
remaining labeled unknown. No eligible attributed statements yields **unscored**, not a low-quality-person label.
The weights are a transparent initial policy, not empirically calibrated. Importance measures contribution coverage
in this collection, not a universal measure of someone's expertise or reputation.

Only accepted, current, nonduplicate claims at the scoring timestamp count. Contributions collapse by Idea;
reposts and repetitions do not earn extra corroboration credit. Assertion groups use SUPPORTS and DUPLICATES connectivity before duplicate filtering; star and chain layouts yield identical peer credit. Known different speakers are required, so two
journalists quoting the same person cannot confirm each other. Unreviewed updates and confirmations cannot change
scoring clocks, remove accepted statements, or add trust. The existing graph can still display unreviewed material.

An unresolved contradiction is reported as a disagreement and does not decide who is wrong. A supersession is
reported as an update, not an automatic credibility penalty. Disagreements/updates are counted within the original
claim's half-life. User downvotes remain an explicit negative signal. The existing overall trust export also reports
unresolved disagreements separately rather than penalizing both people.

Professional relevance uses only cached, sourced `people/` enrichment domains: the best fraction of domain words
matching the selected topic/cluster title, with explicit AI, ML and NLP expansions. A missed match remains unknown; a positive match never lowers the neutral baseline. This conservative lexical match does not infer credentials from job prestige,
affiliation alone, follower counts, or a mention. Additional synonyms and richer first-hand/publication evidence need a separately
reviewed domain mapping. For the all-subject view there is no global prestige score: profile relevance remains unknown.
No new enrichment searches are triggered by this feature; existing bounded professional enrichment can populate profiles.

Evidence labels describe sample size: **limited** below three accepted source items; **developing** from three;
**established** from five with at least three checked distinct peer origins. They are not probabilities of truth.

## Shared reads and exports

`GraphStore.experts(at, subject_id=None)` is the common read for expert retrieval. The new topic dossier reuses it for accepted topics, clusters and ad-hoc query scopes; selecting a dossier person shows the same scoped explanation in its embedded Atlas. Graph JSON contains
`people_scores` and `people_subjects`; claim rows contain `speaker` and source attribution. Person notes preserve
user trust overrides and include versioned importance explanations and statement links. All results rebuild offline.
Subject scores always use all eligible evidence for that subject; timeline filters only select which people are visible.

Focused regression coverage is in `tests/test_people_scoring.py`; it covers provenance validation, quoted-speaker
credit, independent sources, unreviewed inputs, disagreements, profile domains, overrides, migration, and replay order.

Docker keeps main's shared `/data/telegram` default. `REFRESHER_VAULT` in the local `.env` can select an existing `/data/jury` collection without moving or deleting its records.

## Speaker review and identity separation

In Atlas, select a claim and open **Review speaker and evidence origin**. Read the original numbered passages, select the source author, an explicitly extracted speaker, or **Unknown**, and select the supporting passages. Save to confirm or correct the model's suggestion. **Use model attribution again** reverses a correction. These actions make no model or search requests.

Corrections are append-only events in `people-reviews/<claim-hash>/`. They bind to the exact source, claim, source people and original model attribution. Changed evidence invalidates a correction; it is ignored on replay and marked stale in the reviewer. Model verdicts, raw text and extraction records are preserved. A saved speaker review does not accept an unreviewed source.

Optionally supply a public profile URL to distinguish identity. The normalized URL determines a stable person ID, so two same-name people with different profiles remain separate, while source-grounded aliases with the same reviewed profile can share an identity. Name-based merges cannot absorb a reviewed profile into another identity. No profile is guessed from a name, and existing enrichment is not transferred to a newly separated identity. Profile-specific note filenames prevent name collisions; trust overrides remain attached to their original person ID.

## Checked primary origins

Different speakers alone provide corroboration, not independent evidence. For each reviewed statement, optionally enter the **primary evidence origin** and explicitly check that it was verified rather than copied from a repost. Reports repeating a shared release should use the same primary URL. Fragments and common tracking parameters are removed when comparing URLs.

Track record uses only distinct checked peer origins in the same assertion group. Both the contributor and the peer need reviewed attribution and a checked origin; a shared origin adds no credit. Multiple speakers using one peer origin add at most one credit per assertion group. `corroborations` reports distinct peer speakers without asserting independence. `confirmations` and `checked_origin_count` report checked distinct-origin credit. `independence_verified` means that such reviewer-checked credit exists; it is not external fact verification. An unresolved contradiction or update still does not decide who is wrong.

The People graph and dossier display contribution rank, with ties sharing a rank, and show coverage, track record and professional relevance separately. Numeric importance remains in exports as an uncalibrated collection heuristic. Attribution status is `model_inferred`, `mixed` or `reviewed`. A name-only identity stays visibly unresolved. The general attributed-person trust read uses the same origin-credit helper; legacy records without attribution retain their existing author-trust behavior.

## Bounded paid update for older documents

In People mode, open **Update attribution in older documents**. The preview lists accepted, kept documents that have claims and source text but lack current model attribution. Previewing and refreshing never call a model. Select one to three documents and click **Run paid update for selected documents** to authorize that batch. The selected documents' relation judgments and attribution are updated; originals and source acceptance are retained. Changed attribution can invalidate an older speaker review.

Each batch makes at most three document-level model calls through the existing relation judge and BudgetedModel, with a 120,000-token and 600-second run budget. Provider network retries may occur; this is not a currency cap or a guarantee that an already-running request finishes within the run budget. The preview binds approval to document fingerprints and model/provider/request settings. Only one batch can be active. Work shares the capture worker and vault write lock, so concurrent ingestion/review writes are serialized.

Progress and completed document IDs persist in `people-jobs/`. On failure, completed model results are retained, remaining documents stop, and no automatic retry occurs. After a server restart, queued/running jobs become interrupted and require another explicit paid action. Offline exports include the updated scores and identity notes but omit interactive write controls.

Regression coverage includes source-bound review/reset, stale tokens, same-name separation, shared origins, unsafe URLs, paid batch bounds, interrupted recovery, selected-source changes and HTTP same-origin protections. Paid behavior is tested using fake model responses; these tests do not establish live extraction accuracy.
