# Running Campaign Studio

How to configure the stack, start it, run a campaign from brief to finished ads,
inspect what happened, and reset. Architecture lives in `README.md`; this file is
only "what do I type, in what order".

---

## 0. What you will have running

| Service | Where | What it is |
|---|---|---|
| API (FastAPI) | http://localhost:8000 (docs: http://localhost:8000/docs) | Creates campaigns, serves results and generated files (`/assets/...`) |
| Worker | no port | Temporal worker: runs research, spec, image, compose and video activities |
| App Postgres | localhost:5432 | Campaigns, research trace, specs, assets |
| Temporal server | localhost:7233 | Workflow orchestration (its own Postgres + Elasticsearch) |
| Temporal Web UI | http://localhost:8080 | Watch workflows, retries, failures |
| Frontend (Vite) | http://localhost:5173 | Campaign Studio UI |

Prerequisites: Docker with Compose v2.20+ (`docker compose version`), Node 18+ for the
frontend, and Python 3.12 if you run tests or the app outside Docker.

---

## 1. Configure

All settings come from one env file. Docker Compose reads `docker/.env`; the app run
outside Docker reads `.env` in the repo root. Both are gitignored -- never commit keys.

```bash
cp .env.example docker/.env      # for the Docker stack
```

### Start in fixture mode first (no keys, no cost)

`.env.example` ships with `PROVIDER_MODE=fixture`: every provider (LLM, search, page
reader, image, video, storage) is a canned local adapter. Use it to check the stack
works end to end before spending anything.

### Then switch to real providers

Edit `docker/.env`:

| Variable | Value | Notes |
|---|---|---|
| `PROVIDER_MODE` | `real` | |
| `OPENAI_API_KEY` | your key | research, spec, vision check, image model |
| `OPENAI_MODEL` | e.g. `gpt-5.6-luna` | research loop, creative spec, vision product locator |
| `OPENAI_IMAGE_MODEL` | blank | blank = the adapter default (`gpt-image-2.5-flare`) |
| `OPENAI_IMAGE_INPUT_FIDELITY` | **blank** for `gpt-image-2.5-flare` | that model rejects the parameter; the adapter retries without it, but blank saves one failed call per hero |
| `TAVILY_API_KEY` | your key | web search for research |
| `STORAGE_BACKEND` | `local` | files land in `data/assets/` |
| `AD_TEXT_MODE` | `overlay` or `model` | see §6 |

Leave everything else as in `.env.example`.

---

## 2. Start the stack

```bash
cd docker
make local-d          # build the api/worker image and start everything, detached
make migrate          # create/upgrade the app database tables (run after every pull)
make ps               # all containers should be "running"/"healthy"
```

Check it is alive:

```bash
curl http://localhost:8000/health        # {"status":"ok"}
```

and open http://localhost:8080 (Temporal UI) — you should see the `default` namespace.

Changed `docker/.env`? `make restart` recreates api + worker with the new values.
Changed worker code? `make restart-worker` (the worker does not hot-reload; the API does).

### Alternative: app processes on your machine, infra in Docker

Useful when the Docker image cannot be built (the image installs ffmpeg with apt) or
when you want to debug in your IDE:

```bash
cd docker && docker compose -f docker-compose-local.yml up -d postgres temporal temporal-ui && cd ..
cp docker/.env .env                       # same settings, read from the repo root
# LOCAL_STORAGE_PATH in .env.example is a container path (/code/data/assets):
# set LOCAL_STORAGE_PATH=./data/assets in .env when running outside Docker, and start
# both processes below from the repo root (the worker reads stored files from that path).
pip install -r requirements.txt
python -m alembic upgrade head
python -m app.worker                      # terminal 1 (needs ffmpeg on PATH for videos)
uvicorn app.api.main:app --port 8000      # terminal 2
```

---

## 3. Start the frontend

```bash
cd frontend/campaign-studio
npm install
npm run dev            # http://localhost:5173
```

It talks to `http://localhost:8000` by default; point it elsewhere with
`VITE_API_BASE_URL=...` in `frontend/campaign-studio/.env`.

---

## 4. Run a campaign — in the UI

1. **Create campaign**: fill in product name, description, target audience, objective,
   tone and **call to action** (shown verbatim on the ads). Add **verified claims**
   (only facts you can stand behind — in `model` mode the first short one becomes the
   badge). Upload a **reference image**: a clean photo of the real pack. It is sent to
   the image model so the generated product matches yours; without it the model
   invents packaging.
2. **Research** runs (about 1–2 min in real mode). The page shows the trace: the plan,
   each search and page read, what was screened out and why, verified quotes.
3. **Pick an angle**. Each shows its sourced observations (quote + link) separately
   from the creative interpretation.
4. **Spec + assets** generate (about 1 min): hero, 1:1 ad, 9:16 ad, 8 s video.
   Any single asset can be **retried** without re-running research.

## 4b. Run a campaign — with curl

```bash
# brief.json: product_name, product_description, target_audience, objective, tone, cta,
#             verified_claims (list), optionally reference_image_url
curl -X POST localhost:8000/campaigns \
  -F "brief=<brief.json;type=application/json" \
  -F "reference_image=@packshot.png;type=image/png"
```

Use `brief=<brief.json` (read from file). Inline `-F "brief={...}"` breaks: curl
treats `;` inside the value as a field separator.

The response has `campaign.id`. Then:

```bash
ID=<campaign id>
curl localhost:8000/campaigns/$ID                 # status: researching -> awaiting_angle_selection
curl localhost:8000/campaigns/$ID/research        # plan, steps, sources, 3 angles
curl -X POST localhost:8000/campaigns/$ID/select-angle \
     -H 'content-type: application/json' -d '{"angle_id":"<angle id>"}'
curl localhost:8000/campaigns/$ID                 # generating_spec -> generating_assets -> completed
curl localhost:8000/campaigns/$ID/spec            # hook, CTA, scene, palette, typography_style
curl localhost:8000/campaigns/$ID/assets          # hero, ad_1x1, ad_9x16, video (+ status, errors)
curl localhost:8000/campaigns/$ID/usage           # provider usage rows
curl -X POST localhost:8000/campaigns/$ID/assets/<asset id>/retry   # regenerate one asset
```

Statuses: `draft` → `researching` → `awaiting_angle_selection` → `generating_spec` →
`generating_assets` → `completed` (or `completed_with_errors` if an asset failed, or
`failed` if research/spec failed).

### Where the files are

`data/assets/campaigns/<campaign id>/`: `hero_image.jpg`, `ad_1x1.jpg` (1080×1080),
`ad_9x16.jpg` (1080×1920), `video.mp4` (1080×1920, 8 s), and in `model` mode
`master_ad_9x16.jpg`. The API also serves them at
`http://localhost:8000/assets/campaigns/<campaign id>/...`. Uploaded packshots are in
`data/assets/reference-images/`.

---

## 5. What happens under the hood (short)

brief → **research** (text only; the image is not used here) → you pick an angle →
**creative spec** (LLM writes hook, scene, palette, type style; the CTA is copied from
your brief) → **hero image** (prompt + your packshot) → in parallel **1:1 ad**,
**9:16 ad**, **video**. Full detail: `README.md` §4–§5.

---

## 6. Choose who renders the ad copy: `AD_TEXT_MODE`

| | `overlay` (default) | `model` |
|---|---|---|
| Image model draws | a text-free product photo | a finished 1:1 ad: headline, brand line, CTA button, optional claim badge |
| Copy placed by | our compositor (fonts in `app/assets/fonts/`) | the image model, from the exact strings in the prompt |
| 9:16 | re-laid-out crop of the same hero | the 1:1 master, outpainted taller; the master's pixels are pasted back so the copy cannot change |
| Image calls / campaign | 1 (+3 small vision calls) | 2 |
| Strength | text is character-exact, every time | looks like a designed ad (closest to D2C brand creatives) |
| Watch for | copy can still land on the product on some images | the model may add small unrequested marks (an icon, a logo on a prop) — review before publishing |

Switch by changing only the env var, then `make restart` (or restart the worker).
The mode applies to campaigns whose hero is generated after the switch.

---

## 7. Inspect and debug

```bash
cd docker
make logs-worker      # activity logs: research steps, image calls, failures
make logs-api
make psql             # SQL shell on the app DB
```

- Temporal UI (http://localhost:8080): each campaign is workflow `campaign-<id>`;
  you can see activity inputs/outputs, retries and errors there.
- Why the copy went where it did (overlay mode): `assets.generation_prompt` holds a
  JSON layout report (placement, product overlap, measured contrast, collision flag):
  ```sql
  select asset_type, generation_prompt from assets where campaign_id = '<id>';
  ```
  For the hero it holds the exact image prompt (model mode: the master prompt +
  outpaint prompt).

---

## 8. Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests
```

Offline, no keys needed. The ffmpeg video test skips without `ffmpeg` on `PATH`; the
workflow test downloads Temporal's test server on first use (or set
`TEMPORAL_TEST_SERVER_PATH`).

---

## 9. Stop and reset

```bash
cd docker
make down     # stop and remove containers; keeps DB data and Temporal history
make clean    # DESTRUCTIVE: also deletes the app DB and Temporal volumes
```

Generated files in `data/assets/` are not removed by either; delete them by hand.

---

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `make local-d` fails at `apt-get install ffmpeg` | the build cannot reach the Debian mirrors (proxy/firewall); use the "app on your machine" option in §2 |
| Hero fails with "does not support the 'input_fidelity' parameter" | set `OPENAI_IMAGE_INPUT_FIDELITY=` (blank); current code also retries automatically |
| Worker changes have no effect | `make restart-worker` (no hot reload for the worker) |
| `.env` changes have no effect | `make restart` |
| API errors about missing columns after a pull | `make migrate` |
| `'brief' is not valid JSON` from curl | use `-F "brief=<brief.json;type=application/json"` (see §4b) |
| Research shows Reddit reads as "not readable" | Reddit blocks non-browser readers; the harness drops the site and moves on |
| Campaign stuck in `researching` | `make logs-worker` and the Temporal UI; check `OPENAI_API_KEY` / `TAVILY_API_KEY` and outbound network to api.openai.com / api.tavily.com |
