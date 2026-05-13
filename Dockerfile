# Multi-target image (see DEPLOYMENT.md for Fly.io / Railway / local runs):
#   docker build --target api .        # FastAPI + Gunicorn on :8000 (production API)
#   docker build --target streamlit . # Streamlit dashboard on :8501 (baseUrlPath /dashboard)
#
# Railway / Fly: set the Docker build target to **api** in the service settings (or
# `fly deploy --build-target api`).

# -----------------------------------------------------------------------------
FROM python:3.12-slim AS streamlit

WORKDIR /app

COPY requirements-dashboard.txt .
RUN pip install --no-cache-dir -r requirements-dashboard.txt

COPY dashboard.py .
COPY .streamlit/config.toml ./.streamlit/config.toml

EXPOSE 8501

ENV API_BASE_URL=http://api:8000
ENV API_METRICS_PATH=/v1/metrics

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/dashboard/_stcore/health', timeout=5).read()"

CMD ["streamlit", "run", "dashboard.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.baseUrlPath=/dashboard", \
     "--server.enableCORS=false", \
     "--server.enableXsrfProtection=false", \
     "--browser.gatherUsageStats=false"]

# -----------------------------------------------------------------------------
FROM python:3.12-slim AS api

WORKDIR /app

RUN mkdir -p /data

ENV DATABASE_URL=sqlite:////data/loads.db
ENV WEB_CONCURRENCY=4

COPY requirements.txt requirements-prod.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-prod.txt

COPY app ./app
COPY data ./data
COPY scripts ./scripts

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; k=os.environ.get('API_KEY','').strip(); r=urllib.request.Request('http://127.0.0.1:8000/v1/health',headers={'X-API-Key':k}); urllib.request.urlopen(r,timeout=5).read()"

CMD ["sh", "-c", "exec gunicorn app.main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000 -w ${WEB_CONCURRENCY:-4}"]
