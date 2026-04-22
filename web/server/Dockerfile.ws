# realtalk-ws: PTY-hosting WebSocket service. Concurrency=1 per instance.
# Installs the `realtalk` CLI itself so it can be spawned inside the PTY.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# The `realtalk` CLI pulls in textual (ncurses deps) — keep them thin.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        locales \
        ca-certificates \
    && sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8 LANGUAGE=en_US:en LC_ALL=en_US.UTF-8

WORKDIR /app

RUN pip install --no-cache-dir uv==0.5.11

# Install the realtalk CLI from the parent repo (injected by Cloud Build
# as /app/realtalk-src), then the web server.
COPY realtalk-src /tmp/realtalk
RUN uv pip install --system --no-cache /tmp/realtalk && rm -rf /tmp/realtalk

COPY pyproject.toml ./
COPY realtalk_web ./realtalk_web
RUN uv pip install --system --no-cache .

# Non-root — PTY only needs to own its child, not the host.
RUN useradd --create-home --shell /bin/bash realtalk
USER realtalk

EXPOSE 8080

CMD ["uvicorn", "realtalk_web.ws_main:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8080", "--workers", "1", \
     "--ws", "websockets"]
