FROM python:3.12-slim

WORKDIR /app

RUN mkdir -p /data

ENV DATABASE_URL=sqlite:////data/loads.db

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY data ./data
COPY scripts ./scripts

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request; k=os.environ.get('API_KEY','').strip(); r=urllib.request.Request('http://127.0.0.1:8000/health',headers={'X-API-Key':k}); urllib.request.urlopen(r,timeout=5).read()"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
