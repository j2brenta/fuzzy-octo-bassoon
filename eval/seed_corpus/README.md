# Seed corpus v1

A seed corpus is a small collection of inputs with human-written expected labels.
It gives later parsing, extraction and graph work a shared set of examples.

## Contents

- Four byte-identical copies of the populated `sample_raw` files. The two
  researcher lists are intentionally both present for semantic deduplication.
- Three explicitly synthetic announcement/repost/correction fixtures. They
  describe a fictional workshop; they are not claims about the hackathon.
- `manifest.yaml`: source identity, optional author/forwarder/timestamps and
  expected processing labels for each input.

Simulated channel/source/message IDs are seed identifiers, not real Telegram
IDs. Real samples' unknown authors and publication timezones are not guessed.
The real-content labels are drafts for team review. Synthetic labels describe
intended evaluation outcomes and may need a finer claim schema later.

## Run

From the repository root:

```bash
python -m pip install -r requirements.txt
python -m channels.importer eval/seed_corpus --validate-only
python -m channels.importer eval/seed_corpus --vault .local-vault/seed
```

The import command can also take the manifest file directly. A second run reuses
all seven records. It preserves fixture capture/event times and stable source
IDs, even if the corpus directory moves. Use a fresh vault when changing capture
metadata: a retry preserves the first record rather than silently updating it.

## Three different kinds of duplicate

1. The same delivery imported twice: capture reuses one record.
2. A repost in another message/source: both originals survive capture. A later
   graph relation should connect their claims.
3. Independent sources confirming a claim: both originals survive too; the graph
   must judge independence. Identical text alone does not establish confirmation.

## Labels are an answer key

`expected` fields stay only in this manifest. They are not copied into raw
records or used to generate model/graph output. We have not evaluated semantic
relations yet: this block checks the import plumbing and source preservation.

The three synthetic inputs arrive correction → repost → original, while their
publication times establish original → repost → correction. This will test that
later relation processing uses event time, not arrival order.

## Limitations

Only UTF-8 text originals are supported so far. Add genuine HTML, PDF, email,
image, audio and video cases with parsers later. The two empty sample placeholders
are deliberately excluded. Five fully labeled refresher topics and baseline
comparisons remain future work; this starter corpus does not satisfy those gates.

All sources are prevalidated before any writes. Storage errors during a batch
can still leave a partial import; a retry safely resumes completed captures.
The manifest format and capture envelope are provisional until team contracts
are agreed. Editing a message under its existing ID is rejected, not versioned.
