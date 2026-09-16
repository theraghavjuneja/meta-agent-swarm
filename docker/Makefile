# Campaign Studio — local Docker dev.
#
# Run everything from this directory:  cd docker && make local
#
# All targets assume they're invoked from here (make's default working
# directory is the directory containing the Makefile), which is also where
# docker-compose-local.yml and .env live - so no -f/--env-file flags are
# needed anywhere below.

COMPOSE := docker compose -f docker-compose-local.yml

.PHONY: local local-d down stop logs logs-api logs-worker logs-temporal ps build \
        migrate makemigration downgrade current history psql clean

## Bring up the full stack in the foreground (Ctrl-C stops it). Primary entrypoint.
local:
	$(COMPOSE) up --build

## Same as `local`, but detached - useful before running `make migrate` for the first time.
local-d:
	$(COMPOSE) up --build -d

## Stop and remove containers (keeps volumes, so Postgres data and Temporal history survive).
down:
	$(COMPOSE) down

## Stop containers without removing them.
stop:
	$(COMPOSE) stop

## Tail logs for everything.
logs:
	$(COMPOSE) logs -f

## Tail logs for just the API.
logs-api:
	$(COMPOSE) logs -f api

## Tail logs for just the worker.
logs-worker:
	$(COMPOSE) logs -f worker

## Tail logs for the Temporal server itself (useful when `api`/`worker` are stuck restarting on boot).
logs-temporal:
	$(COMPOSE) logs -f temporal

## Show container status.
ps:
	$(COMPOSE) ps

## Rebuild the api/worker image without starting anything.
build:
	$(COMPOSE) build

# --- Alembic -----------------------------------------------------------------
#
# These run inside the already-running `api` container (`make local-d`
# first), so they use the exact same interpreter, installed packages, and
# DATABASE_URL as the app itself - never a locally-installed Alembic
# talking to a different Postgres by accident.

## Apply every unapplied migration.
migrate:
	$(COMPOSE) exec api alembic upgrade head

## Autogenerate a new migration from model changes. Usage: make makemigration m="add assets table"
makemigration:
	$(COMPOSE) exec api alembic revision --autogenerate -m "$(m)"

## Roll back one migration.
downgrade:
	$(COMPOSE) exec api alembic downgrade -1

## Show the migration Alembic believes is currently applied.
current:
	$(COMPOSE) exec api alembic current

## Show the full migration history.
history:
	$(COMPOSE) exec api alembic history --verbose

# --- Misc ---------------------------------------------------------------------

## Open a psql shell against the app database.
psql:
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-postgres} -d $${POSTGRES_DB:-campaign_studio}

## Full teardown: stop everything AND delete volumes (app DB data + Temporal history/ES data). Destructive.
clean:
	$(COMPOSE) down -v