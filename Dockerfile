FROM python:3.12-slim

WORKDIR /app

RUN mkdir -p /data

ENV DATABASE_URL=sqlite:////data/loads.db

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY data ./data
COPY scripts ./scripts

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
