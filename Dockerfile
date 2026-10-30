# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.9.18 /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=0 UV_LINK_MODE=copy
WORKDIR /app

# Cache dependencies separately from application changes; never re-resolve the lock.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --extra web --extra mcp --no-dev --no-install-project
COPY refresher/ refresher/
COPY channels/ channels/
COPY engine/ engine/
COPY graph/ graph/
COPY store/ store/
# uv keys its cached wheel of the project on pyproject.toml only; rebuild it so code changes are not lost.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --extra web --extra mcp --no-dev --no-editable --reinstall-package refresher

FROM python:3.12-slim-bookworm AS runtime
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    REFRESHER_VAULT=/data/telegram
# jq and less support the terminal evidence viewer; HTTPS needs CA roots.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates jq less \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 refresher \
    && useradd --uid 10001 --gid refresher --create-home refresher \
    && mkdir /data \
    && chown refresher:refresher /data
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY eval/seed_corpus/ eval/seed_corpus/
USER refresher:refresher
CMD ["python", "-m", "channels.telegram", "--vault", "/data/telegram"]

# Install locked development tools only in the test environment.
FROM builder AS test-builder
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --extra web --extra mcp --no-editable

# Tests run without credentials, external networking, or access to the real vault.
FROM runtime AS test
COPY --from=test-builder /app/.venv /app/.venv
COPY tests/ tests/
CMD ["python", "-m", "pytest", "-p", "no:cacheprovider", "-q"]

FROM runtime AS production

# Nebius Serverless (scripts/nebius/): starts as root only to hand the vault folder on the shared filesystem to the
# app user, then runs the command as that user.
FROM production AS nebius
USER root
COPY scripts/nebius/entrypoint.sh /usr/local/bin/refresher-entrypoint
ENTRYPOINT ["refresher-entrypoint"]
CMD ["refresher", "serve", "--host", "0.0.0.0", "--port", "8080"]
