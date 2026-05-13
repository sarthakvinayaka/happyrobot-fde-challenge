# Deployment guide

This project is a **FastAPI** API (`app.main:app`) and an optional **Streamlit** dashboard. Production images use **`python:3.12-slim`**, install from **`requirements.txt`** + **`requirements-prod.txt`**, and expose the API on **port 8000** (Gunicorn + Uvicorn workers). HTTPS termination is provided by the host (**Fly.io**, **Railway**, nginx, etc.).

**Repository (example):** `https://github.com/<my-username>/happyrobot-fde-challenge`

**Security**

- All **`/v1/*`** routes (and most other routes) require header **`X-API-Key`** matching the server’s **`API_KEY`** environment variable. There is no separate `FMCSA_API_KEY` name in this codebase—use **`FMCSA_WEB_KEY`** or **`FMCSA_KEY`** (same FMCSA QCMobile web key; see `.env.example`).
- Do not commit real secrets; set them only in the cloud provider UI or secrets store.

---

## How to access the deployment (fill in after deploy)

After you deploy, replace the placeholders with your real hostnames.

| What | Example URL |
|------|----------------|
| **API base URL** | `https://<your-api-host>` |
| **OpenAPI (Swagger)** | `https://<your-api-host>/docs` |
| **OpenAPI JSON** | `https://<your-api-host>/openapi.json` |
| **Health** | `https://<your-api-host>/v1/health` (requires `X-API-Key`) |
| **Dashboard (Streamlit)** | `https://<your-dashboard-host>/dashboard/` **or** your Streamlit Cloud / second Fly app URL |

**Example authenticated request**

```bash
curl -sS -H "X-API-Key: <YOUR_API_KEY>" "https://<your-api-host>/v1/health"
```

HappyRobot HTTP tools must use the same **`X-API-Key`** header and **`https://`** base URL.

---

## Environment variables (set in cloud UI / secrets; do not commit values)

| Variable | Required | Purpose |
|----------|----------|---------|
| `API_KEY` | **Yes** | Shared secret; clients send `X-API-Key: <API_KEY>`. |
| `DATABASE_URL` | **Yes** for durable prod | e.g. `postgresql+psycopg://user:pass@host:5432/dbname`. SQLite works for quick demos (`sqlite:////data/loads.db` in Docker). |
| `FMCSA_WEB_KEY` or `FMCSA_KEY` | Recommended | FMCSA QCMobile web key for carrier verification. |
| `REDIS_URL` | Optional | Redis for FMCSA response cache; omit if you accept slower repeat lookups. |
| `DASHBOARD_ENTRY_URL` | Optional | Used by API’s `GET /dashboard` redirect (default dev Streamlit URL). |

Streamlit **dashboard** service (if deployed separately) also needs:

| Variable | Purpose |
|----------|---------|
| `API_BASE_URL` | Public URL of the API (e.g. `https://<your-api-host>`). |
| `API_METRICS_PATH` | Usually `/v1/metrics`. |
| `API_KEY` | Same value as the API’s `API_KEY` (dashboard calls metrics with this header). |

---

## Database migrations

There is **no Alembic** migration step in this repo. The API runs **`Base.metadata.create_all`** on startup (`app/main.py` lifespan), so tables are created on first boot. For **seed data**, run the seed script against the same `DATABASE_URL` you configured (locally or inside a one-off container/job):

```bash
# From repo root, with DATABASE_URL pointing at your DB:
PYTHONPATH=. python scripts/seed_loads.py
```

Docker one-off example:

```bash
docker run --rm -e DATABASE_URL="postgresql+psycopg://..." -e API_KEY="..." \
  <your-api-image> python scripts/seed_loads.py
```

(Use the image you built from `--target api`.)

---

## Reproducing the deployment — Fly.io (recommended; `fly.toml` in repo)

**Prerequisites:** [flyctl](https://fly.io/docs/hands-on/install-flyctl/), GitHub repo pushed (e.g. `https://github.com/<my-username>/happyrobot-fde-challenge`).

1. **Clone / fork** the repo and `cd` into it.
2. **Install CLI and log in:** `fly auth login`
3. **Create the app** (once): `fly apps create <your-app-name>`
4. **Edit `fly.toml`:** set `app = "<your-app-name>"` (replace `replace-with-your-fly-app-name`).
5. **Set secrets** (values are examples—use your own):

   ```bash
   fly secrets set API_KEY="your-long-random-secret"
   fly secrets set DATABASE_URL="postgresql+psycopg://..."
   fly secrets set FMCSA_KEY="your-fmcsa-web-key"
   # Optional:
   fly secrets set REDIS_URL="redis://..."
   ```

6. **Deploy the API** (Docker **api** stage):

   ```bash
   fly deploy --config fly.toml --build-target api
   ```

7. **Note the hostname:** `https://<your-app-name>.fly.dev` (or custom domain you attach).

8. **Optional — Streamlit as a second Fly app:** create another app, deploy with `docker build --target streamlit` (see Dockerfile), set `API_BASE_URL` to your API URL, `API_KEY`, and `API_METRICS_PATH=/v1/metrics`, expose **8501**, and use Streamlit’s `/dashboard` base path as in the Dockerfile `CMD`.

**HTTPS:** Fly terminates TLS automatically (`force_https = true` in `fly.toml`).

---

## Reproducing the deployment — Railway

**Prerequisites:** [Railway CLI](https://docs.railway.com/develop/cli), GitHub repo connected.

1. Fork/clone `https://github.com/<my-username>/happyrobot-fde-challenge`.
2. In Railway: **New Project** → **Deploy from GitHub** → select the repo.
3. Add a **service** from this repo; set **builder** to **Dockerfile** (this repo includes **`railway.json`** pointing at `Dockerfile`).
4. In the service **Settings → Build → Docker**: set **Dockerfile path** to `Dockerfile` and **Docker Build Target** to **`api`** (multi-stage image).
5. Under **Variables**, add the same names as in the table above (`API_KEY`, `DATABASE_URL`, …). Do not paste values into git.
6. Deploy; copy the generated **public URL** (Railway provides HTTPS).
7. **Health / probes:** `/v1/health` requires `X-API-Key`; prefer relying on the image **HEALTHCHECK** (already in the Dockerfile) or a TCP check on port **8000**, unless your platform can send custom headers on HTTP checks.

Optional: `./deploy.sh railway` from the repo if you use Railway CLI and have `railway link` set up (see `deploy.sh`).

---

## Reproducing locally — Docker (API)

From the repository root:

```bash
docker build --target api -t happyrobot-fde-api .
docker run --rm -p 8000:8000 --env-file .env happyrobot-fde-api
```

Then:

- API: `http://127.0.0.1:8000/docs`
- Use header `X-API-Key: <same as API_KEY in .env>`.

**Streamlit (local, separate container)**

```bash
docker build --target streamlit -t happyrobot-fde-streamlit .
docker run --rm -p 8501:8501 \
  -e API_BASE_URL=http://host.docker.internal:8000 \
  -e API_METRICS_PATH=/v1/metrics \
  -e API_KEY=<same as API_KEY> \
  happyrobot-fde-streamlit
```

Open `http://127.0.0.1:8501/dashboard/` (path matches `--server.baseUrlPath` in the Dockerfile).

---

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| `401` + `INVALID_API_KEY` | Missing or wrong `X-API-Key` header, or `API_KEY` on server does not match. |
| `503` + `API_KEY_NOT_CONFIGURED` | `API_KEY` is empty on the server. |
| FMCSA errors | `FMCSA_WEB_KEY` / `FMCSA_KEY` not set or invalid. |
| Empty loads | Run `scripts/seed_loads.py` against your `DATABASE_URL`. |

---

## Files reference

| File | Role |
|------|------|
| `Dockerfile` | Multi-stage: **`api`** (Gunicorn :8000), **`streamlit`** (:8501). |
| `fly.toml` | Fly.io: build target `api`, internal port 8000, HTTPS. |
| `railway.json` | Railway: Dockerfile build entrypoint. |
| `deploy.sh` | Optional wrapper: `./deploy.sh fly` or `./deploy.sh railway`. |
