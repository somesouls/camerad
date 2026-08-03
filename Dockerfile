# ================================================================
#  All-in-one lokal (Python saja): Frontend FastAPI (web_app.py)
#  + Backend FastAPI (llm_fix_final_combined.py) dalam 1 container.
#  Tidak butuh PHP lagi.
# ================================================================
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependency Python (torch CPU + requirements)
COPY requirements.txt .
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

# Salin seluruh kode
COPY . .

# Backend hanya diakses internal oleh frontend di dalam container
ENV PIPELINE_API_HOST=127.0.0.1
ENV PIPELINE_PORT=8000
ENV PIPELINE_API_BASE=http://127.0.0.1:8000
ENV PIPELINE_FORCE_LOCAL=1
ENV WEB_HOST=0.0.0.0
ENV WEB_PORT=8080

EXPOSE 8080
CMD ["bash", "start_docker.sh"]
