# Container deployment

## What this checkout runs

| Code | Runtime role | Compose service |
| --- | --- | --- |
| `channels/telegram.py` | One long-polling Telegram bot; invokes parsing, article fetching, triage, extraction and review in the same process | `telegram` (default) |
| `refresher/app.py`, `refresher/web.py`, `refresher/static/`, `refresher/assets/map/` | The one browser app: capture with background ingestion and native PDF text reading, the Atlas, topics and dossiers, proposals | `app` (default) |
| `refresher/consolidate.py` | The hourly review: relate, review the whole graph, enrich people, rebuild the views | `review` (default) |
| `channels/importer.py`, `channels/web.py`, `engine/*`, `refresher/cli.py` | Finite capture, processing, knowledge-base rebuild and evaluation commands | `cli` (`tools` profile) |
| `refresher/viewer.py` | Interactive terminal evidence viewer using curses, jq and less | `viewer` (`tools` profile) |
| `tests/` | Existing offline pytest and unittest suite and fixtures | `test` (`test` profile) |
| `store/raw.py`, `refresher/vault.py`, `graph/memory/` | Vault files are persistent truth; graph state is replayed into memory | Host vault bind mount |
| `engine/llm.py` | HTTPS client for external Nebius Token Factory models | Configured external API |
| Neo4j Community | Persistent graph database for the planned graph adapter | `neo4j` |

There is no queue broker or database client in this checkout.
`graph/store.py` describes a future Neo4j implementation;
the current implementation is `MemoryStore`. The Neo4j service prepares that
database, but the application does not yet use it. Telegram uses outgoing HTTPS
long polling and needs no published port or reverse proxy. Voice transcription
is absent from this checkout and therefore has no container dependency.

## The app

Create the host vault directory, then build and start just the app:

```bash
mkdir -p .local-vault/telegram
docker compose up -d --build --wait app
```

Open <http://localhost:8080>. The app is `refresher serve`: topics at `/`, a topic's
dossier at `/topic/`, capture at `/capture/`, the Atlas and proposals at `/atlas/`. It uses `.env` for model settings
and works on the same vault as the Telegram bot (`REFRESHER_VAULT`, `/data/telegram`
in the container), so a web capture and a Telegram one land in one graph.
Submissions and uploads are kept in that vault's `submissions/` and `uploads/`.
To publish it on another port, set `APP_PORT` (`APP_PORT=8090 docker compose up -d app`):
the app takes changes only from its own address, and compose allows that port for it.
`VAULT_DIR` overrides the shared host parent directory. The web PDF extra and
static assets are installed in the application image; no Node build is needed.
The server listens on all interfaces inside its container, while the published
host port is restricted to localhost. This is a local demo without authentication.
The healthcheck verifies the submissions API. Restarting or removing the
container preserves the host vault.

## The hourly review

`review` runs `refresher consolidate --if-due --every 600`: every 10 minutes it
checks whether the last review is an hour old and something was placed since, and
if so reviews the whole graph (relations, duplicates, firm Ideas, proposals, people,
`wiki/` and the map). It starts with a plain `docker compose up`, so the review is on
by default whichever way captures arrive, bot or app.

The bot still reviews after a capture and on its own timer. A run repeats its check
under the vault lock, so when two runners are due at once, the one that waited sees
the other's stamp and skips: one `consolidate:` line per run in the vault's `log.md`.
`REFRESHER_REVIEW=off` turns the bot's own runs off; nothing sets it by default.

For a scheduler (a Nebius serverless job, cron), run it once per tick instead:

```bash
refresher consolidate --vault /data/telegram --if-due   # exits at once unless a review is due
```

All runners must see the same vault on a filesystem with working POSIX locks (a
shared filesystem, not object storage): the lock is what keeps a review and a
placement from interleaving.

## Neo4j

Set `NEO4J_AUTH=neo4j/<your-password>` in `.env` before starting Neo4j; use a
password of at least eight characters. `make up` builds the application and
pulls the official Neo4j Community image; it does not start containers.

```bash
make up
docker compose up -d --wait neo4j
```

Open <http://localhost:7474> and log in as `neo4j` with your configured password.
Bolt is available at `bolt://localhost:7687` on the host, or `bolt://neo4j:7687`
from other services in this Compose project. Both host ports bind only to
127.0.0.1. The healthcheck runs `RETURN 1` through `cypher-shell`.

Database files live in the named volume `refresher_neo4j-data`, separately from
the file vault. `docker compose down` preserves them; `down -v` deletes them.
`NEO4J_AUTH` sets the initial password only: changing it does not reset an
existing database password. The service has a 2 GB container memory limit, with
a 512 MB maximum heap and 512 MB page cache for local development.

Validated: the pinned image pulled successfully, and a temporary Compose
deployment passed the authenticated Cypher healthcheck. Test resources were
removed afterward.

See the official [Neo4j Docker guide](https://neo4j.com/docs/operations-manual/current/docker/introduction/).

## Build and start

Use Docker Engine/Docker Desktop with Compose 2.24+ (the optional `env_file`
syntax requires it). Run commands from the repository root.

1. Copy `.env.example` to `.env` if you have not configured it already.
2. Fill `TELEGRAM_BOT_TOKEN`, `TELEGRAM_OWNER_ID` and the model settings locally.
   Blank owner ID enables setup mode only: send `/start`, save your numeric ID
   to `.env`, then recreate the service. A missing token fails without a prompt.
3. Create the writable host path: `mkdir -p .local-vault/telegram`.
4. On Linux, export your host IDs before Compose commands:

   ```bash
   export LOCAL_UID=$(id -u)
   export LOCAL_GID=$(id -g)
   ```

   Alternatively add their numeric values to `.env`. Use a non-root account.
   Docker Desktop normally handles host bind-mount permissions with the default
   container UID/GID 10001. Existing files must be writable by the chosen user;
   the image does not recursively change ownership of your vault.
5. Stop any other bot polling the same token, then run:

   ```bash
   docker compose config --quiet
   docker compose build
   docker compose up -d telegram
   docker compose logs -f telegram
   ```

The bot logs its startup mode after checking Telegram's webhook configuration.
Each message then logs one line per pipeline step (capture, prepare, triage,
claim extraction, quality review, entities, handoff, graph placement, review)
with its outcome and duration, and one line per Nebius call with its tokens.
For example (model name, counts and times are illustrative):

```text
INFO channels.telegram: item 261c73e38e · quality review: started
INFO engine.llm: <NEBIUS_JUDGE_MODEL>: 6,912 in / 1,204 out tokens, 14.2 s
WARNING channels.telegram: item 261c73e38e · quality review: failed after 61.3 s (QualityError: …)
```

A step that has `started` but no outcome line was interrupted (for example by
a restart). Filter one item with `docker compose logs telegram | grep 261c73e38e`.
`LOG_LEVEL` in `.env` sets the detail; captured text and claims are never logged.

Every warning and error, from the bot, the jury frontend and `refresher` commands,
is also kept in the vault as JSON lines: `logs/problems.jsonl`, rotated at 5 MB
into `.1` … `.3`, whatever the console level. Review it with:

```bash
uv run refresher problems --vault .local-vault/telegram                 # grouped by kind, then the latest 20
uv run refresher problems --vault .local-vault/telegram --since 2026-10-27 --limit 50
```

Each line has the time, level, process (bot, frontend, cli), source module, the
item id when the message names one, and the message. Like the console, it never
holds captured text; a traceback is reduced to the exception type.
Send a short text to verify an acknowledgement and a new raw record under
`.local-vault/telegram/raw/`. This is the live integration check and requires
your credentials. Tests and image builds do not contact Telegram or Nebius.
With AI enabled, a live message may trigger paid model calls and send its text
to Nebius. For capture-only operation set `TRIAGE_PROVIDER=off`,
`JUDGE_PROVIDER=off` and `ENTITY_EXTRACTION=off`. Set `WEB_FETCH=off` to disable
automatic article fetching too.

After changing `.env`, run `docker compose up -d --force-recreate telegram`;
`restart` alone does not reload container environment variables. After changing
code, run `docker compose up -d --build telegram`.

## On-demand tools

Explicitly targeting a profiled service with `run` enables it automatically.
The CLI accepts a complete command, so it can run any installed module:

```bash
docker compose run --rm cli python -m channels.importer eval/seed_corpus --validate-only
docker compose run --rm cli python -m channels.importer eval/seed_corpus --vault /data/seed
docker compose run --rm cli python -m engine.parse /data/seed
docker compose run --rm cli python -m engine.results /data/telegram
docker compose run --rm cli refresher rebuild --vault /data/telegram
docker compose run --rm cli refresher km --vault /data/telegram
docker compose run --rm viewer
docker compose run --rm viewer refresher-view --vault /data/seed
```

`rebuild` requires existing `processed/` records. It replays the knowledge base
and writes wiki notes; it does not invoke an LLM. Other `engine` commands such as
`triage`, `extract`, `quality` and `entities` can invoke configured models.
Pause the bot with `docker compose stop telegram` before manually running
commands that rewrite the same item's pipeline artifacts, then restart with
`docker compose up -d telegram`. Do not scale the bot: Telegram permits one
polling receiver per token. The viewer mounts the vault read-only and has no
network or credential injection. Start it after at least one capture exists.

Only the application and public seed corpus are in the runtime image. To import
an arbitrary host file, mount a source directory explicitly as read-only:

```bash
docker compose run --rm -v "$PWD/imports:/imports:ro" cli \
  python -m channels.importer /imports/note.md --vault /data/telegram
```

## Persistence and recovery

`VAULT_DIR` defaults to `./.local-vault` and is mounted at `/data`. Thus the
bot's `/data/telegram` is the host's `.local-vault/telegram`. To use another
location, set `VAULT_DIR` in `.env` to an existing directory and create its
`telegram/` subdirectory. Paths supplied to CLI commands must use `/data/...`.
The bind mount deliberately refuses to create a missing host directory; this
avoids accidentally creating a root-owned or mistyped data location.

`docker compose down` removes containers and the project network; vault files
remain on the host. Readable pipeline notes are in
`.local-vault/telegram/processed/index.md`, and a knowledge-base rebuild writes
`wiki/`. Back up the entire vault, including raw captures, snapshots, annotations
and overrides. Stop the bot and all writing CLI jobs before copying the vault
to obtain a consistent backup. Restore into a directory writable by your
configured UID/GID, set `VAULT_DIR` to that directory and start the bot.

## Engineering choices

- One multi-stage image builds from `pyproject.toml` and `uv.lock` using
  `uv sync --locked --no-dev --no-editable`. Dependency layers are cached;
  the production image has no uv build tool or tests. Python and uv versions
  are selected explicitly; Python's 3.12 Debian Bookworm tag can still receive
  updates. For an immutable release, pin image digests in your release process,
  refresh them deliberately and rebuild for security updates. Apt packages and
  isolated Python build requirements are not fully locked by `uv.lock`.
- The build context uses an allowlist. `.env`, `.local-vault`, Git history,
  local virtual environments and unrelated documents never enter the context.
- Runtime uses a non-root user, a read-only root filesystem, a bounded `/tmp`
  tmpfs, dropped Linux capabilities and `no-new-privileges`. The vault is the
  only persistent writable mount. Each container has PID, CPU, memory and log
  limits; adjust these in `compose.yaml` for measured workloads.
- `init: true` reaps child processes; the application receives termination
  signals directly and gets 30 seconds to stop. Bot retries are bounded to
  three restarts on failure. Inspect logs for invalid credentials, webhook
  conflicts or a second polling receiver.
- `.env` injects credentials into the app, the review, the bot and the CLI at runtime. Keep it local
  and restrict its permissions (`chmod 600 .env`). Environment variables remain
  visible to Docker administrators; this is a local deployment configuration.
  The current app does not implement Docker secret-file configuration.
- There is no application readiness/heartbeat endpoint. A process-existence
  probe would not demonstrate that polling works, so no synthetic healthcheck
  is configured. Container status and logs plus a real capture establish basic
  operation; a future production rollout should add application-level polling
  and processing observability.

These choices follow the official [uv Docker integration guide](https://docs.astral.sh/uv/guides/integration/docker/)
and [Compose service reference](https://docs.docker.com/reference/compose-file/services/).

## Offline validation

```bash
docker compose --profile tools --profile test config --quiet
docker compose --profile test build telegram test
docker compose run --rm --no-deps test
```

The test service runs all existing pytest and unittest tests with its own fixtures, without
`.env`, network access or a vault mount. Its filesystem restrictions match the
production runtime; temporary test data goes under `/tmp`, and pytest's persistent
cache is disabled. Locked development dependencies are installed only in the test
image. No test dependencies
are added to the production image. Live Telegram delivery, article downloads
and model responses still require separately configured integration checks.

Verified for this change: both images built; Compose configuration validated;
the isolated suite passed **291 tests and 104 subtests**. A temporary host vault
also passed public seed import (nine items), parsing, readable-note generation
and access from a separate read-only viewer container. Live Telegram and Nebius
checks were not run.
