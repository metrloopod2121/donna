# Isolated Deployment Plan

This is a reversible server deployment plan for `telegram-secretary`. It intentionally does not enable TLS, Telegram webhooks, nginx routes, DNS-dependent callbacks, external accounts, or real secrets.

Confirmed facts:

- Temporary host: `secretary.vacanator.xyz`.
- REG.RU A record has been created for the existing always-on server.
- Public DNS resolution is confirmed.
- The server already runs other applications, Docker, nginx, and systemd services.
- Do not modify or restart existing nginx, Docker workloads, or systemd services.

## Deployment Shape

Primary option: a dedicated Docker Compose project managed by one dedicated systemd unit.

Fallback when the server has Docker but no Compose plugin: a dedicated systemd unit that runs exactly one container via plain `docker run`. This avoids installing packages or changing the existing Docker setup. The same paths, env files, ports and rollback boundaries apply.

Isolation boundaries:

- Compose project: `telegram-secretary`.
- Container name: `telegram-secretary-app`.
- Host bind: `127.0.0.1:18097` only.
- No nginx site is installed.
- No TLS certificate is requested.
- Telegram bot delivery mode stays `polling`.
- Webhooks stay disabled.
- Service data is under `/var/lib/telegram-secretary`.
- Config is under `/etc/telegram-secretary`.
- App checkout is under `/opt/telegram-secretary/app`.

## Server Layout

```text
/opt/telegram-secretary/app/                 # copied project checkout
/etc/telegram-secretary/compose.env          # non-secret compose settings
/etc/telegram-secretary/runtime.env          # non-secret runtime settings
/etc/telegram-secretary/secrets.env          # real secrets, mode 0600, never committed
/var/lib/telegram-secretary/data/            # SQLite and runtime data
/var/lib/telegram-secretary/sessions/        # Telegram MTProto session files
/var/lib/telegram-secretary/sessions/telethon/ # Telethon session files, 0700 dir, 0600 files
/var/lib/telegram-secretary/craft-export/    # Craft exported notes/tasks, read-only mount
/etc/systemd/system/telegram-secretary-compose.service
/etc/systemd/system/telegram-secretary-docker.service   # Docker CLI fallback if Compose is unavailable
```

Optional host user:

```bash
sudo useradd --system --home /var/lib/telegram-secretary --shell /usr/sbin/nologin telegram-secretary
```

This is optional because the container uses UID/GID `10001`. If a host user is created, align the volume ownership with the runtime UID/GID.

## Safe Preparation Commands

Run only after reviewing the files in `deploy/`. These commands create isolated paths and do not touch existing nginx or other services.

```bash
sudo install -d -m 0755 /opt/telegram-secretary
sudo install -d -m 0750 /etc/telegram-secretary
sudo install -d -m 0750 /var/lib/telegram-secretary/data
sudo install -d -m 0750 /var/lib/telegram-secretary/sessions
sudo install -d -m 0700 /var/lib/telegram-secretary/sessions/telethon
sudo install -d -m 0750 /var/lib/telegram-secretary/craft-export
sudo chown -R 10001:10001 /var/lib/telegram-secretary
```

Copy the project checkout to `/opt/telegram-secretary/app` using rsync or git from a reviewed commit. Then copy examples into real config files:

```bash
sudo cp /opt/telegram-secretary/app/deploy/env/compose.env.example /etc/telegram-secretary/compose.env
sudo cp /opt/telegram-secretary/app/deploy/env/runtime.env.example /etc/telegram-secretary/runtime.env
sudo cp /opt/telegram-secretary/app/deploy/env/secrets.env.example /etc/telegram-secretary/secrets.env
sudo chmod 0640 /etc/telegram-secretary/compose.env /etc/telegram-secretary/runtime.env
sudo chmod 0600 /etc/telegram-secretary/secrets.env
```

Do not fill real secrets through chat. Put real values into `/etc/telegram-secretary/secrets.env` only on the server or via an approved secret manager.

## Minimal Dry-Run Start

The first start should prove only that the isolated service can boot and expose health locally.

Expected mode:

- `SECRETARY_RUNTIME_MODE=dry_run`
- `TELEGRAM_BOT_DELIVERY_MODE=polling`
- `TELEGRAM_WEBHOOK_ENABLED=false`
- `SECRETARY_TLS_WEBHOOK_ENABLED=false`
- `VOICE_PROVIDER=dry_run`
- `AUTO_REPLY_ENABLED=false`

Install the systemd unit only after paths and env files are reviewed:

```bash
sudo cp /opt/telegram-secretary/app/deploy/systemd/telegram-secretary-compose.service /etc/systemd/system/telegram-secretary-compose.service
sudo systemctl daemon-reload
sudo systemctl start telegram-secretary-compose.service
```

If `docker compose` is unavailable, use the Docker CLI unit instead:

```bash
sudo cp /opt/telegram-secretary/app/deploy/systemd/telegram-secretary-docker.service /etc/systemd/system/telegram-secretary-docker.service
sudo systemctl daemon-reload
sudo docker build -t telegram-secretary:local -f /opt/telegram-secretary/app/deploy/compose/Dockerfile /opt/telegram-secretary/app
sudo systemctl start telegram-secretary-docker.service
```

Health check:

```bash
/opt/telegram-secretary/app/deploy/scripts/healthcheck.sh
curl -fsS http://127.0.0.1:18097/readyz
```

Do not enable the unit until dry-run health is confirmed:

```bash
sudo systemctl enable telegram-secretary-compose.service
```

Or, for the Docker CLI fallback:

```bash
sudo systemctl enable telegram-secretary-docker.service
```

## Minimal Live Polling Check

After the owner manually fills `/etc/telegram-secretary/secrets.env` on the server with `TELEGRAM_BOT_TOKEN` and `SECRETARY_OWNER_TELEGRAM_ID`, the service can run a bot-only polling check.

Safe runtime flags for this phase:

- `SECRETARY_RUNTIME_MODE=telegram_polling`
- `TELEGRAM_BOT_DELIVERY_MODE=polling`
- `TELEGRAM_WEBHOOK_ENABLED=false`
- `SECRETARY_TLS_WEBHOOK_ENABLED=false`
- `AUTO_REPLY_ENABLED=false`
- `CALENDAR_PROVIDER=icloud_caldav`
- `CRAFT_SOURCE_MODE=disabled`
- `VOICE_PROVIDER=dry_run`
- `VOICE_OUTBOUND_ENABLED=false`

Checks:

```bash
curl -fsS http://127.0.0.1:18097/healthz
curl -fsS http://127.0.0.1:18097/readyz
curl -fsS http://127.0.0.1:18097/pollingz
```

If the one-time owner test message returns `Bad Request: chat not found`, ask the owner to open the Telegram bot chat and send `/start`. Telegram bots cannot initiate a chat with a user who has not opened the bot.

## iCloud CalDAV Read-Only Check

The Apple Calendar phase stays read-only and owner-only. It does not create, move or delete events, and it does not enable automatic replies to contacts.

Runtime flags:

- `CALENDAR_PROVIDER=icloud_caldav`
- `AUTO_REPLY_ENABLED=false`
- `TELEGRAM_WEBHOOK_ENABLED=false`
- `SECRETARY_TLS_WEBHOOK_ENABLED=false`
- `CRAFT_SOURCE_MODE=disabled`
- `VOICE_PROVIDER=dry_run`

Secrets go only into `/etc/telegram-secretary/secrets.env` on the server:

```env
APPLE_CALENDAR_USERNAME=
APPLE_CALENDAR_APP_PASSWORD=
```

After secrets are entered, restart only this service:

```bash
sudo systemctl restart telegram-secretary-docker.service
```

Owner-only Telegram checks:

- `/free`
- `/today`

Before the iCloud values are present, both commands should say that Apple Calendar is not connected.

## Telegram User Account Telethon Preparation

This phase prepares a read-only user-account ingest path but does not authorize or poll the personal Telegram account.

Safe runtime flags:

- `TELEGRAM_USER_INGEST_ENABLED=false`
- `TELEGRAM_USER_CLIENT=telethon`
- `TELEGRAM_USER_SESSION_DIR=/app/sessions/telethon`
- `TELEGRAM_USER_FILES_DIR=/app/sessions/files`
- `AUTO_REPLY_ENABLED=false`
- `TELEGRAM_WEBHOOK_ENABLED=false`
- `SECRETARY_TLS_WEBHOOK_ENABLED=false`

Server-only secrets, filled only in `/etc/telegram-secretary/secrets.env`:

```env
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_USER_PHONE_NUMBER=
```

The owner obtains API ID/hash from `my.telegram.org`. The Telegram one-time code and optional 2FA password must be entered only in a server console during a manual Telethon login step, never in the bot chat and never in Codex.

The current code exposes only owner-only bot commands:

- `/tgstatus` reports safe connection state without values.
- `/dialog <person>` and `/analyze <person>` are safe analysis stubs. With ingest disabled they report that personal Telegram is not enabled; after a future ready session they can return "nothing found" when no event-like messages are found.

Telethon login is a one-shot server-console step. It creates a local session under `/var/lib/telegram-secretary/sessions/telethon`; user-account ingest still stays disabled until the owner explicitly approves starting read-only ingest.

## Reversal Plan

Stop and remove only this service:

```bash
sudo systemctl disable --now telegram-secretary-compose.service
sudo rm -f /etc/systemd/system/telegram-secretary-compose.service
sudo systemctl disable --now telegram-secretary-docker.service
sudo rm -f /etc/systemd/system/telegram-secretary-docker.service
sudo systemctl daemon-reload
cd /opt/telegram-secretary/app/deploy/compose
sudo docker compose --env-file /etc/telegram-secretary/compose.env -f docker-compose.yml down
sudo docker rm -f telegram-secretary-app
```

Preserve `/var/lib/telegram-secretary` unless the owner explicitly approves deleting runtime data and Telegram session files.

Remove config and app files only after backup/approval:

```bash
sudo rm -rf /opt/telegram-secretary/app
sudo rm -rf /etc/telegram-secretary
```

## What Waits For DNS And Accesses

Wait for explicit user approval:

- Prepare an nginx site review.
- Request/attach TLS.
- Consider Telegram webhook mode.

Wait for secrets/accesses:

- Telegram API ID/hash and bot token.
- Owner Telegram ID and trusted sender allowlist.
- iCloud/Apple Calendar app-specific password or selected CalDAV credentials.
- Craft access path: direct read connector or scheduled Markdown/JSON export.
- Voice provider credentials if real calls are enabled.

## Explicit Non-Actions

- No nginx config is created in this repo.
- No TLS/Certbot commands are included.
- No external account creation is included.
- No existing service restart is required.
- No real secret value is stored in committed files.
