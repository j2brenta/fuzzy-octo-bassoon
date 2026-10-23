# Reviewer token usage — 23 October 2026

Measured two fresh `judge-v4` calls on the committed Super extraction candidates, using `nvidia/Nemotron-3-Ultra-550b-a55b`. Historical vaults were not modified. This evaluates reviewer usage, not model rankings or a new nine-seed pipeline score.

## Actual API usage

| Sample | Claims | Input tokens | Output tokens | Total tokens | Seconds | Review |
|---|---:|---:|---:|---:|---:|---|
| people-list | 25 | 5,155 | 5,856 | 11,011 | 12.652 | accepted |
| linkedin-math | 16 | 5,168 | 4,005 | 9,173 | 9.999 | needs_revision |

**Totals:** 10,323 input + 9,861 output = **20,184 tokens**. Each review validated on its first model response. Output usage includes the model’s generated response/reasoning budget; no currency cost is estimated.

The essay remains `needs_revision`: one claim names the two problem categories while its cited passage only introduces their existence; the names occur in adjacent, uncited passages. This measurement did not run correction/enrichment or entity review. The people-list claim review passed.

## Repeated evidence overhead

Each claim includes its exact cited text, and the request also includes the full source. This was added to reduce mistakes about which passages actually support a claim. It has a measurable payload-size cost:

| Sample | Full user JSON, characters | Same JSON without attached evidence | Increase | Source plus repeated evidence / source |
|---|---:|---:|---:|---:|
| people-list | 18,095 | 9,998 | 81.0% | 2.62× |
| linkedin-math | 20,022 | 13,091 | 52.9% | 1.73× |

These are **character counts**, not an exact token-cost multiplier. The API usage above measures the actual current request. No second model call with evidence removed was made, so this run does not isolate the marginal token count or compare review accuracy with/without that context. Repeated source text can appear under several claims, which explains the people-list overhead exceeding one extra copy.

## Saved evidence

Full request payloads, source/extraction copies, replies and usage are retained locally under `.local-vault/seed-reviewer-usage-2026-10-23/`. `measurement.json` records request IDs, model, latency, checks and counts; `<seed>-payload.json` stores the actual prompt. These test artifacts are ignored by Git.

## Decision

Keep attached evidence for now: previous failures showed inaccurate passage interpretation. The measured expense is visible; any optimization should be evaluated on both usage and source-faithfulness before changing the review prompt. No claim acceptance policy or extraction wording was weakened.
