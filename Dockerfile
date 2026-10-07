FROM python:3.12-slim AS dashboard
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
WORKDIR /app
COPY requirements-dashboard.txt ./
RUN pip install --no-cache-dir -r requirements-dashboard.txt \
    && useradd --create-home --uid 10001 app
COPY --chown=app:app config.py ./
COPY --chown=app:app agents/ ./agents/
COPY --chown=app:app evaluation/ ./evaluation/
COPY --chown=app:app dashboard/ ./dashboard/
COPY --chown=app:app data/ ./data/
COPY --chown=app:app reports/ ./reports/
COPY --chown=app:app refactoring/ ./refactoring/
USER app
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4)"
CMD ["python", "-m", "streamlit", "run", "dashboard/app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]

FROM python:3.12-slim AS pipeline
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app
COPY requirements.txt requirements-dashboard.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=app:app . .
USER app
CMD ["python", "run_pipeline.py", "--reports-only"]
