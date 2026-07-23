FROM python:3.12-slim

# Versión inyectada por el CI (--build-arg VERSION). Llega al runtime como APP_VERSION
# y se loguea al arrancar. El .git NO entra en la imagen (ver .dockerignore).
ARG VERSION=dev

# Usuario no-root; su uid/gid debe coincidir con el del montaje CIFS (uid=1000).
RUN groupadd -g 1000 app && useradd -u 1000 -g 1000 -m app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config.yaml .
COPY src/ ./src/

# /state debe pertenecer a 'app': un named volume vacío hereda el ownership del
# dir en la imagen, así el usuario no-root puede escribir la SQLite y el heartbeat.
RUN mkdir -p /state && chown -R app:app /state

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    CONFIG_PATH=/app/config.yaml \
    ROOT_PATH=/data/documentos \
    STATE_DB_PATH=/state/sync.sqlite \
    HEARTBEAT_FILE=/state/heartbeat \
    HEARTBEAT_MAX_AGE=300 \
    APP_VERSION=${VERSION}

LABEL org.opencontainers.image.version=${VERSION} \
      org.opencontainers.image.title="allm-sync" \
      org.opencontainers.image.description="Sincroniza un share de Windows con workspaces de AnythingLLM"

USER app

# Liveness: el loop refresca HEARTBEAT_FILE cada ciclo; si queda viejo, el
# container se marca unhealthy. start-period cubre el primer escaneo.
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
    CMD ["python", "/app/src/healthcheck.py"]

CMD ["python", "/app/src/main.py"]
