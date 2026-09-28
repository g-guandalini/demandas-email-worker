FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/g-guandalini/demandas-email-worker"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN groupadd --system --gid 10001 demandas \
    && useradd --system --uid 10001 --gid demandas --home-dir /nonexistent --shell /usr/sbin/nologin demandas

COPY --chown=demandas:demandas worker.py web_server.py ./
COPY --chown=demandas:demandas web ./web

USER demandas:demandas
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=2)" || exit 1

CMD ["python3", "worker.py", "web"]
