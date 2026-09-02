FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app

COPY src ./src
COPY README.md pyproject.toml ./

RUN python -m pip install --no-cache-dir "telethon>=1.41,<2" "PyJWT[crypto]>=2.8"

RUN mkdir -p /app/data /app/sessions \
    && chown -R 10001:10001 /app

USER 10001:10001

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -m telegram_secretary healthcheck --url http://127.0.0.1:8080/healthz

CMD ["python", "-m", "telegram_secretary", "serve", "--host", "0.0.0.0", "--port", "8080"]
