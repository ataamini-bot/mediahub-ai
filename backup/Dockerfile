FROM postgres:16-bookworm
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates tzdata \
    && python3 -m venv /opt/backup-venv \
    && /opt/backup-venv/bin/pip install --no-cache-dir cryptography 'psycopg[binary]' requests \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/app/backup /app/app/backup
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["/opt/backup-venv/bin/python", "-m", "app.backup.engine"]
CMD ["daemon"]
