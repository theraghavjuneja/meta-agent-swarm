# Campaign Studio — API/worker image.
#
# One image serves both processes started by docker-compose-local.yml (the
# `api` container running uvicorn, and the `worker` container running
# `python -m app.worker`) — they need the identical dependency set, so a
# second Dockerfile would only be a second thing to keep in sync.
#
# Build context is the project root (the parent of this `docker/` folder),
# not `docker/` itself — see the `build.context` path in
# docker-compose-local.yml — because it needs `requirements.txt` and, once
# it exists, `alembic/`, both of which live next to `app/`, not inside
# `docker/`.

FROM python:3.12-slim

# ffmpeg: app.assets.adapters.video_ffmpeg shells out to the `ffmpeg` binary
#   in real (non-fixture) provider mode.
# libpq5: the runtime library asyncpg's wheel links against; installing it
#   explicitly means the image works even on a base where the wheel's
#   bundled copy is absent.
# curl: used by the container healthcheck in docker-compose-local.yml.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /code

# Dependencies are installed from requirements.txt alone, before the source
# is copied in. Docker caches this layer by the hash of requirements.txt, so
# editing application code — the normal loop in local dev — never triggers a
# reinstall. Only a dependency change invalidates this layer.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Baked in for completeness (e.g. `docker run` without compose, or a future
# CI build) — but docker-compose-local.yml bind-mounts the real source
# directory over this at /code, which is what makes --reload see edits on
# disk without a rebuild. Compose's volume mount always wins over what COPY
# put here at container start.
#
# `COPY . .` (not named COPY app/ / alembic/ / alembic.ini) is deliberate:
# alembic.ini and alembic/env.py do not exist yet (see the Alembic setup
# steps) — a named COPY of a file that is not there yet fails the build
# outright, whereas `COPY . .` simply copies whatever currently exists.
# `.dockerignore` (project root) keeps this from also pulling in `docker/`
# itself, `.git`, and local junk like `__pycache__`.
COPY . .

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/code

EXPOSE 8000

# Overridden per-service by docker-compose-local.yml's `command:` (uvicorn
# with --reload for `api`, `python -m app.worker` for `worker`). This is the
# fallback for running the image standalone.
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]