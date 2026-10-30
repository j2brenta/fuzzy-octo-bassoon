# Refresher

A personal knowledge engine. Forward posts, links, PDFs and screenshots to a Telegram bot (or add them in the browser),
and Refresher reads them for you: NVIDIA Nemotron extracts the claims, people and organizations, checks them against
the source, and places them in a graph of ideas. An hourly review links what belongs together, spots duplicates and
corrections, and asks you only about what it cannot decide. When you come back to a subject, you get a short brief of
its major ideas, each citing the sources it stands on, and what changed since you last looked.

Everything stays in a vault of plain files that you own. Other agents can read it, never write it, over MCP.

## NVIDIA models on Nebius

All model calls go to **Nebius Token Factory** with one API key:

| Model | Used for |
|---|---|
| `nvidia/nemotron-3-super-120b-a12b` | Triage and claim extraction; in the review, relating new items to the graph, confirming merges, naming topics |
| `nvidia/Nemotron-3-Ultra-550b-a55b` | Independent review of every claim against its source, with bounded corrections; people and organizations |
| `nvidia/Nemotron-3.5-Super-VL` | Text from pictures and screenshots sent to the bot |
| `Qwen/Qwen3-Embedding-8B` | Finding related ideas and grouping them into topics |
| `Qwen/Qwen3-30B-A3B-Instruct-2507` | Translations for reading (never used as evidence) |

The demo runs on **Nebius AI Cloud**: the app and the MCP server on a Serverless Endpoint, the hourly review as a
Serverless Job, the vault on a shared filesystem, secrets in MysteryBox ([below](#deploy-on-nebius)).

## Quick start with Docker Compose

Needs Docker with Compose 2.24 or newer and a [Nebius Token Factory](https://tokenfactory.nebius.com) API key.

```bash
cp .env.example .env               # set NEBIUS_API_KEY; the rest has working defaults
mkdir -p .local-vault/telegram     # your vault: plain files on your disk
docker compose build
docker compose up -d app review    # the app, and the hourly review
```

Open <http://localhost:8080>. On Linux, first `export LOCAL_UID=$(id -u) LOCAL_GID=$(id -g)` so the containers can
write the vault.

| Service | What it does |
|---|---|
| `app` | The web app on port 8080: add sources, read subjects, the map, the review |
| `review` | Every 10 minutes, runs the review when an hour has passed and something new arrived |
| `telegram` | The Telegram bot (optional, below) |
| `mcp` | Read-only MCP server on port 8081 (optional, profile `mcp`, below) |

`docker compose logs -f app` follows a service; `docker compose down` stops everything. The vault is kept.

### Telegram bot (optional)

1. Create a bot with [@BotFather](https://t.me/BotFather) (`/newbot`) and put its token in `.env` as
   `TELEGRAM_BOT_TOKEN`.
2. `docker compose up -d telegram`, then send `/start` to your bot: it answers with your numeric user ID.
3. Put that ID in `.env` as `TELEGRAM_OWNER_ID` and run `docker compose up -d telegram` again.

Now forward posts, links, PDFs or screenshots to the bot; it replies once each is saved and read. It answers only you.
`/consolidate` runs the review now.

### MCP for other agents (optional)

Set `MCP_TOKEN` in `.env` (`openssl rand -hex 32`), then:

```bash
docker compose --profile mcp up -d mcp
claude mcp add --transport http refresher http://127.0.0.1:8081/mcp --header "Authorization: Bearer $MCP_TOKEN"
```

Tools: `list_topics`, `brief`, `find_sources`, `read_source`, `list_threads`. None of them writes, and the vault is
mounted read-only.

## Using it

- **Home** lists your topics. Type any subject to get its brief: the major ideas, each citing numbered sources.
- **+ Add source** takes pasted text, a public link or a file (text or PDF) and shows each step as it is read.
- **Explore** is the map of ideas, topics, sources and people.
- **Review** holds what the review could not decide alone: merges, new topics, the same person under two names.
  Accept, reject or leave each one. The count in the bar shows how many wait.
- **More → Update connections** runs the review now instead of waiting for the hour.

## Settings

All settings are in `.env`; [`.env.example`](.env.example) explains each one. The ones you are most likely to change:

| Setting | Default | Meaning |
|---|---|---|
| `NEBIUS_API_KEY` | (none) | Your Token Factory key |
| `REFRESHER_LANGUAGE` | `en` | Language of everything Refresher writes; sources are always quoted in their own language |
| `TRIAGE_PROVIDER`, `JUDGE_PROVIDER`, `ENTITY_EXTRACTION` | `nebius`, `nebius`, `on` | Set `off` to capture without model calls |
| `WEB_FETCH` | `on` | Read the articles behind links |
| `REFRESHER_RUN_TOKENS`, `REFRESHER_RUN_SECONDS` | `2000000`, `1800` | Spending cap for one review run |
| `REFRESHER_LOGIN` | (none) | `user:password` before you publish the app anywhere but localhost |

## Publishing it

Without a login the app is for localhost only. To put it on the internet, serve it over **HTTPS** and set
`REFRESHER_LOGIN=user:password` (every page asks for it; only `/healthz` stays open) and `REFRESHER_APP_URL` to its
public address. Use a demo vault and a Nebius key you can revoke: everyone with the login can add sources.

### Deploy on Nebius

`scripts/nebius/` deploys the same image to Nebius AI Cloud. Resource IDs are in `scripts/nebius/config.sh`, model
settings in `scripts/nebius/models.env`, and the key, login and MCP token in a MysteryBox secret.

| Script | Does |
|---|---|
| `endpoint.sh` | Creates the endpoint once: the app on one HTTPS URL, MCP on another, the vault on a shared filesystem |
| `deploy.sh` | Builds and pushes the image and restarts the endpoint; the URLs stay the same |
| `review-job.sh` | Runs the review as a Serverless Job; `.github/workflows/nebius-review.yml` starts it hourly when something new is waiting |

## Development

```bash
uv sync --extra web --extra mcp
uv run --extra mcp pytest                                   # or, in Docker: docker compose --profile test run --rm test
uv run refresher serve --vault .local-vault/telegram        # the app without Docker
uv run refresher --help                                     # every command: brief, consolidate, proposals, ...
```

The [full guide](docs/guide.md) covers every stage and setting in detail, and the [container guide](docs/docker.md)
covers backups, permissions and troubleshooting.

## License

Apache-2.0; see [LICENSE](LICENSE).
