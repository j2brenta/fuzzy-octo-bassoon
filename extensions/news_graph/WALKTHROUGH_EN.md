# Offline news evidence map

The producer remains authoritative in Vault JSON. This extension retains immutable
revision snapshots and decisions so a map can be inspected offline. It does not
rerun extraction or model inference when rendering. Open a story, follow a link,
inspect its exact quote, then switch to claims and select a particular directed
relation. The comparison shows that decision's shared context once and both sets
of original evidence. Selecting two claims manually is a neutral comparison.

From the checkout, with Python 3.11+:

```bash
demo_dir="$HOME/newsmap-demo-$(date +%Y%m%d-%H%M%S)"
python3 extensions/news_graph/run.py demo "$demo_dir"
python3 -m http.server 8765 --bind 127.0.0.1 --directory "$demo_dir/viewer"
```

Open http://127.0.0.1:8765/. The synthetic Aurora story includes an announcement,
reposts, a correction, changed conditions, a pending source and a replacement
revision. Operator actions are fixtures, not independent human evaluation. The
map shows at most 20 primary nodes and 60 edges; further entries are paginated.
Dates use UTC. Unknown dates and conditions remain explicit.

Optional automatic assessment uses the same retained evidence and review protocol.
Create a manifest outside Git, replacing the ledger path with an absolute local path:

```json
{
  "mode": "OFFLINE_SYNTHETIC",
  "project": "newsmap-demo",
  "stage": "assessment-1",
  "model": "fixture-model",
  "paid_cap_micro": 0,
  "simulation_cap_micro": 1000000,
  "ledger": "/absolute/path/outside-git/budget/ledger.sqlite"
}
```

Use an existing producer Python environment, without installing another one:

```bash
PYTHONPATH="$PWD:$PWD/extensions/news_graph/src" python3 extensions/news_graph/run.py \
  --db "$demo_dir/authority/news.sqlite" assess-offline --mode NEBIUS --manifest /path/to/manifest.json
python3 extensions/news_graph/run.py --db "$demo_dir/authority/news.sqlite" \
  export-local "$HOME/newsmap-new-export"
```

These are explicitly simulated replies and simulated cost estimates, not live
Nebius calls. MODEL_ASSESSED differs from HUMAN_REVIEWED. A human rejection remains
in effect on rerun. Use the existing `review show/reject/revoke/accept` commands to
inspect and control an exact proposal. A fresh export reads saved results only.

The opt-in full producer runner also accepts `texts` (one or two synthetic strings),
`scenario` (`accepted`, `invalid_then_repair`, `judge_reject`) and `workflow`
(`MANUAL` or `NEBIUS`). It requires a fresh output folder and keeps the same project
ledger across stages. Invoke it with `python3 -m newsmap_sidecar.runner MANIFEST OUTPUT`
and the same PYTHONPATH. A 10-attempt gate can stop an item as incomplete; accepted
prior artifacts remain. Simulation budget and stage ID are explicit, not reset on retry.

On MacBook, use a local APFS directory in your home folder, outside Git/cloud sync.
The native macOS path has been implemented but has not yet been run on a real Mac.
The ordinary offline viewer needs no host Python dependencies. Live inference,
GPU execution and service deployment remain separate, disabled stages.
