# Freight Loads API (HappyRobot FDE challenge)

FastAPI service for loads, FMCSA carrier checks (Redis-cached), call processing webhook, and call metrics. HTTP API is versioned under **`/v1`**. Optional Streamlit dashboard and Docker Compose stacks (SQLite dev, Postgres prod).

**HappyRobot integration:** see **[HAPPYROBOT_INTEGRATION.md](HAPPYROBOT_INTEGRATION.md)** for workflow steps, example JSON, and how to interpret `next_action` / transfer fields.

**Deploy to cloud (Fly.io / Railway):** see **[DEPLOYMENT.md](DEPLOYMENT.md)** for URLs, env vars, and reproduce steps.

## Requirements

- Python 3.12+
- Docker (optional, for Compose)

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-prod.txt
cp .env.example .env
# Edit .env: API_KEY, FMCSA_WEB_KEY or FMCSA_KEY, REDIS_URL if not local Redis
```

Run API:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Run dashboard (second terminal):

```bash
export API_BASE_URL=http://127.0.0.1:8000
export API_METRICS_PATH=/v1/metrics
export API_KEY=sk-testkey   # same as API_KEY in .env
streamlit run dashboard.py
```

Every request must send header **`X-API-Key`** (except browser `OPTIONS` preflight).

## API docs (Swagger)

With the server running:

- **Swagger UI:** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **OpenAPI JSON:** [http://127.0.0.1:8000/openapi.json](http://127.0.0.1:8000/openapi.json)

Versioned routes live under **`/v1`**, for example:

| Method | Path |
|--------|------|
| GET | `/v1/health` |
| GET | `/v1/metrics` |
| GET/POST | `/v1/loads` |
| GET | `/v1/search-loads` |
| GET | `/v1/calls` |
| POST | `/v1/process-call` |
| GET | `/v1/verify-carrier/mc/{mc_number}` |

### What this assignment expects (summary)

- **This repository** is the **backend decision engine**: FMCSA eligibility, load search + `pitch_text`, negotiation bounds, outcomes, sentiment, persistence, and metrics.
- **HappyRobot** hosts the **inbound web call** workflow: prompts, speech, and **HTTP tools** that call this API over **HTTPS** with **`X-API-Key`**; live conversation is not implemented here.
- **Three tools map to the brief:** `GET /v1/verify-carrier/mc/{mc}` (MC check), `GET /v1/search-loads` (lanes + pitch), `POST /v1/process-call` (each negotiation turn, interest, booking).
- **Deliverables:** a **link to your HappyRobot workflow** (published inbound agent), this API on a **public URL**, and the **metrics/calls dashboard** wired to `/v1/metrics` and `/v1/calls`.

### HappyRobot inbound workflow (concise)

1. **Web Call** trigger starts the inbound session.
2. Agent collects **MC** → tool `GET /v1/verify-carrier/mc/{mc}`. If `valid` is false, polite rejection and **end call**.
3. If valid, agent collects **lane / equipment** (and optional filters) → tool `GET /v1/search-loads?...`. Agent reads **`pitch_text`** for one or more loads and asks if the carrier is **interested**.
4. If **not interested**, `POST /v1/process-call` with `carrier_interested: false` (and transcript); use `next_action: "end_call"`.
5. If **interested**, each price round: append to **`counter_offers`**, set **`current_round`** = `len(counter_offers)` when sending it, `POST /v1/process-call` with full **`transcript`**, **`mc_number`**, **`interested_load_id`**. Branch on **`outcome`**, **`next_action`**, **`suggested_counter`**, **`transfer_message`** / **`transfer_status_message`**.
6. On **`outcome`** = `booked` and **`next_action`** = `transfer_to_sales`, run your **transfer** node (mock is fine); backend does not perform telephony.
7. Every `process-call` **persists** a call row; dashboard reads **`/v1/metrics`** and **`/v1/calls`**.

Full step-by-step, payloads, and response interpretation: **[HAPPYROBOT_INTEGRATION.md](HAPPYROBOT_INTEGRATION.md)**.

Root **`GET /`** returns a small JSON index. **`GET /dashboard`** returns a **307** redirect to `DASHBOARD_ENTRY_URL` (Streamlit URL).

## Environment variables

| Variable | Purpose |
|----------|---------|
| `API_KEY` | Required shared secret; send as `X-API-Key`. |
| `DATABASE_URL` | SQLAlchemy URL. Default SQLite file `./data/loads.db`. Prod: `postgresql+psycopg://user:pass@host:5432/dbname` |
| `REDIS_URL` | Redis for FMCSA cache (e.g. `redis://localhost:6379/0`). |
| `FMCSA_WEB_KEY` | FMCSA QCMobile web key. |
| `FMCSA_KEY` | Alias for the same key (compose examples use this name). |
| `DASHBOARD_ENTRY_URL` | Target for `GET /dashboard` redirect. |
| `WEB_CONCURRENCY` | Gunicorn workers (Docker `api` stage only). |

## Reproduce (quick checks)

```bash
# 401 without key on /v1/* (API routes)
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/v1/health

# 200 for /docs and / without key (browser-friendly); /v1/* still needs X-API-Key
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/docs

# 200 with key on /v1/health
curl -s -H "X-API-Key: sk-testkey" http://127.0.0.1:8000/v1/health

# Metrics JSON
curl -s -H "X-API-Key: sk-testkey" http://127.0.0.1:8000/v1/metrics | python -m json.tool

# Dashboard redirect (GET, not HEAD)
curl -s -D - -o /dev/null -H "X-API-Key: sk-testkey" http://127.0.0.1:8000/dashboard
```

## HappyRobot webhook (`POST /v1/process-call`)

Configure HappyRobot **HTTP tools** to call your **public HTTPS** URL (see **[HAPPYROBOT_INTEGRATION.md](HAPPYROBOT_INTEGRATION.md)** for the full graph: verify MC → search loads → process-call each turn).

- **URL:** `https://<your-host>/v1/process-call`
- **Method:** `POST`
- **Header:** `X-API-Key: <same value as server API_KEY>`
- **Body:** JSON matching `ProcessCallRequest` (flat fields: `transcript`, `mc_number`, `interested_load_id`, `counter_offers`, optional `carrier_interested`, `current_round`, `interested_reason`, `final_agreed_price`). Responses are usually **HTTP 200** with business outcomes in JSON—branch on `outcome` / `next_action`, not only status code.

Optional query: `?debug=true` for an extended debug payload in the response.

**Auth errors:** `401` / `503` from the API may include `error_code` (e.g. `INVALID_API_KEY`) next to `detail` for workflow branching.

## Docker — development (SQLite + Redis + nginx + Streamlit)

```bash
docker compose up --build
```

- **HTTPS:** [https://localhost](https://localhost) (self-signed; trust in browser).
- **API:** paths under **`/v1/...`** (e.g. `https://localhost/v1/health`).
- **Dashboard:** [https://localhost/dashboard/](https://localhost/dashboard/)

Redis and the API are not published on the host; only nginx **80/443** are.

## Docker — production compose (Postgres + Gunicorn)

Create a small env file (do not commit secrets):

```bash
cat > .env.prod <<'EOF'
API_KEY=change-me
DB_PASS=change-me-strong
FMCSA_KEY=
DASHBOARD_ENTRY_URL=https://localhost/dashboard/
EOF

docker compose -f docker-compose.prod.yml --env-file .env.prod up --build
```

- **Postgres** data volume: `pgdata`.
- **API** runs **Gunicorn** with **Uvicorn** workers (`WEB_CONCURRENCY`, default 4).
- **nginx** enforces **`X-API-Key`** for **`/v1/`** at the edge (FastAPI enforces it on all routes as well).

### Migrate data from SQLite to Postgres

With Postgres reachable and `DATABASE_URL` pointing at it:

```bash
export DATABASE_URL=postgresql+psycopg://freight:YOURPASS@localhost:5432/freight
PYTHONPATH=. python scripts/sqlite_to_postgres.py sqlite:///./data/loads.db
```

## Images: build & push (Docker Hub)

```bash
export DOCKERHUB_USER=yourdockerhubid
docker login
chmod +x scripts/push-images.sh
./scripts/push-images.sh
```

Images: `${DOCKERHUB_USER}/happyrobot-fde-challenge-api:latest`, `-streamlit:latest`, `-nginx:latest` (override with `IMAGE_PREFIX`).

Set `DOCKER_PLATFORM=linux/arm64` on Apple Silicon if your registry needs arm images.

## Deploy script (Railway or Fly.io)

```bash
chmod +x deploy.sh
./deploy.sh railway   # Railway CLI: variables + railway up
./deploy.sh fly       # fly deploy --config fly.toml --build-target api
```

- **Railway:** install [Railway CLI](https://docs.railway.com/develop/cli), `railway login`, `railway init` in the repo, then export `API_KEY`, `FMCSA_KEY`, `DATABASE_URL`, `REDIS_URL` before running (see script).
- **Fly.io:** edit **`fly.toml`** `app = "..."`, then `fly secrets set API_KEY=... DATABASE_URL=... REDIS_URL=...`. Optional: `FMCSA_KEY`. The sample `fly.toml` deploys the **`api`** Dockerfile stage only; add managed Postgres/Redis separately.

## Project layout

- `app/` — FastAPI app (`main.py`, `call_processing.py`, `carrier_verify.py`, models, schemas).
- `HAPPYROBOT_INTEGRATION.md` — Workflow + example payloads for HappyRobot tool builders.
- `dashboard.py` — Streamlit metrics UI (Plotly).
- `docker-compose.yml` — Dev stack (SQLite volume).
- `docker-compose.prod.yml` — Prod stack (Postgres, Gunicorn, prod nginx).
- `nginx/` — Dev `Dockerfile` + prod `Dockerfile.prod` + TLS / routing templates.
- `scripts/push-images.sh` — Buildx push to registry.
- `deploy.sh` — Thin wrapper for Railway or Fly.

## License

Challenge / demo project; adjust as needed for your org.
