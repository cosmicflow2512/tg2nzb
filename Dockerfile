FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    tini \
    gosu \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /app/
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PUID=99 \
    PGID=100

VOLUME ["/config", "/data"]

# Heartbeat-based healthcheck: app writes /data/heartbeat every 15s,
# we fail if the file is older than 60s or missing.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD test -f /data/heartbeat && \
      test "$(cat /data/heartbeat 2>/dev/null)" -gt "$(($(date +%s) - 60))" \
      || exit 1

# OCI + Unraid Docker-Tab labels
LABEL org.opencontainers.image.title="tg2nzb" \
      org.opencontainers.image.description="Telegram channel to SABnzbd bridge: uploads posted .nzb attachments, Pushover notifications" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/cosmicflow2512/tg2nzb" \
      net.unraid.docker.icon="https://raw.githubusercontent.com/cosmicflow2512/tg2nzb/main/assets/icon.png" \
      net.unraid.docker.shell="/bin/sh"

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["python", "/app/main.py"]
