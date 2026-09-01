# Handoff для Claude Code — Telegram Secretary

> Язык работы: русский. Пиши коротко и конкретно. Этот файл описывает фактическое состояние, обновлено 2026-09-01; не считай README единственным источником правды.

## Цель продукта

Личный Telegram-секретарь Матвея. Он общается с владельцем через отдельного Telegram-бота, по запросу читает личные Telegram-диалоги владельца, находит договорённости/события, готовит сводки и черновики напоминаний. Далее продукт должен уметь напоминать о планах в Telegram и голосом, помогать с календарной доступностью и, только после отдельной настройки, безопасно отвечать доверенным контактам.

Ключевые пользовательские сценарии:

- «Что Матюха написал насчёт встречи?»
- «Проанализируй последние переписки с этим человеком, вытащи события».
- Найденная договорённость -> черновик напоминания/события -> **только после подтверждения владельца** запись в календарь.
- Утренний/дневной брифинг: планы, задачи, важные сообщения.
- Owner-only звонки-напоминания и регулярные созвоны по проектам (2–3 раза в неделю) — поздний этап.

## Неподвижные границы безопасности

- Никогда не выводить и не коммитить токены, пароли, Apple app password, Telegram-код/2FA, Telethon session или персональные сообщения.
- Не читать/не логировать сырые сообщения для отладки. Диагностика только агрегированная.
- Личный Telegram используется только read-only; не отправлять сообщения от user-account.
- `AUTO_REPLY_ENABLED=false`: никаких автоответов людям, пока владелец отдельно не согласует policy/allowlist и e2e-проверку.
- Календарь сейчас read-only. Не создавать/переносить/отменять события до отдельного подтверждения владельца на каждое действие или до явно согласованной policy.
- Не трогать nginx, TLS, вебхуки Telegram и другие сервисы без отдельного разрешения. Текущий Bot API работает через polling.
- Не включать голосовые звонки или интеграции Craft без отдельного решения и доступа.

## Инфраструктура

- Рабочий сервер: `31.76.0.133` (Latvia). Временный публичный hostname: `secretary.vacanator.xyz`.
- Приложение: `/opt/telegram-secretary/app`.
- Runtime env: `/etc/telegram-secretary/runtime.env`; server-only secrets: `/etc/telegram-secretary/secrets.env` (0600).
- Данные: `/var/lib/telegram-secretary/`; Telethon session: `/var/lib/telegram-secretary/sessions/telethon`.
- Сервис: `telegram-secretary-docker.service`; контейнер: `telegram-secretary-app`; image: `telegram-secretary:local`.
- Health доступен **только локально**: `127.0.0.1:18097`. Сервис не enabled при boot.
- На сервере нет Docker Compose plugin: применяется изолированный Docker CLI systemd unit. Не менять/не перезапускать чужие контейнеры.

Проверенные безопасные runtime-флаги:

```env
TELEGRAM_BOT_DELIVERY_MODE=polling
TELEGRAM_WEBHOOK_ENABLED=false
SECRETARY_TLS_WEBHOOK_ENABLED=false
AUTO_REPLY_ENABLED=false
CALENDAR_PROVIDER=icloud_caldav
CRAFT_SOURCE_MODE=disabled
VOICE_PROVIDER=dry_run
VOICE_OUTBOUND_ENABLED=false
```

## Что реально работает

### Git

- Локальный git-репозиторий есть, но initial commit еще не сделан.
- Git remote не настроен. Не выдумывать remote; сначала согласовать с владельцем.

### Бот

- Owner-only polling bot: `@secretaryJarwisBot`.
- `/start`, `/status`, `/tgstatus`, `/today`, `/free`, `/dialog <имя>`, `/analyze <имя>`.
- Доступ владельца ограничен `SECRETARY_OWNER_TELEGRAM_ID`; не ослаблять это ограничение.

### Apple Calendar

- iCloud CalDAV подключён read-only; Apple credentials уже есть только в server secret file.
- `/today` показывает занятые интервалы за весь день без названий/деталей событий.
- `/free` показывает оставшиеся свободные рабочие окна. Событие на весь день корректно отображается как «весь день», не как `00:00-00:00`.

### Личный Telegram через Telethon

- TDLib **не использовать**: его исходная C++ сборка на этом VPS была OOM-killed. Это не ломало действующий бот.
- Telethon установлен в image; личная сессия создана владельцем в SSH-консоли. Коды/2FA не должны попадать в чат.
- `TELEGRAM_USER_INGEST_ENABLED=true` на сервере.
- Реализован request-scoped режим: по `/analyze <имя>` или `/dialog <имя>` находится диалог по отображаемому имени (точное совпадение или часть имени, case-insensitive), читаются последние текстовые сообщения (лимит из `TELEGRAM_USER_ANALYSIS_MAX_MESSAGES`, сейчас 200). Фоновый мониторинг **ещё не реализован**.
- Текущая интерпретация событий rule-based: ключевые слова вроде «встреч», «созвон», «звон», `meeting`, `call`, `event`, «календар». Это не LLM-анализ. Если найдено совпадение, бот сообщает лишь, что возможны обсуждения событий; полноценной сводки/извлечения дат ещё нет.
- Последний ручной тест `/analyze Матюха` вернул, что найдены возможные обсуждения событий. Это подтверждает доступ к диалогу и keyword-path, но не качество анализа.

### Business research calls

- Добавлен локальный workflow для owner-only звонков организациям:
  - `/call +79991234567 | Теннисный клуб | узнать стоимость абонемента; какие дни свободны`;
  - CLI `call-dry-run`;
  - CLI `call-analyze`;
  - SQLite tables для call request, placement, recording notice, transcript, extraction;
  - webhook endpoints `/voice/business/recording`, `/voice/business/transcription`, `/voice/business/status`, `/voice/business/recorded`;
  - dry-run provider по умолчанию;
  - Twilio outbound provider как транспорт реального звонка;
  - transcript post-processing через `CALL_ANALYSIS_PROVIDER=rule_based` или `cloudflare_worker`;
  - optional STT через `VOICE_RECORDING_TRANSCRIBER=cloudflare_whisper`.
- Реальные звонки не включены и не деплоились. Для live нужны server-only secrets: `VOICE_BUSINESS_CALLS_ENABLED=true`, `VOICE_BUSINESS_CALL_PROVIDER=twilio`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_PHONE_E164`, `VOICE_WEBHOOK_BASE_URL`, `VOICE_WEBHOOK_SECRET`.
- По умолчанию разрешен только префикс `+7` через `VOICE_BUSINESS_CALL_ALLOWED_PREFIXES`.
- Twilio используется как транспорт записи. Для русского транскрипта не полагаться на встроенный STT провайдера; использовать transcript callback другого провайдера или Cloudflare Whisper.

## Cloudflare Workers AI: новый LLM-путь

Пользователь не хочет платный OpenAI. Используем его Cloudflare Workers AI (free tier: 10k neurons/day; на момент проверки usage был 0).

- Существующий `family-bot-llm` найден и доступен, но **не использовать и не менять**: это отдельный Family Bot.
- Пользователь создал отдельный Worker `secretary-llm` через Dashboard -> Workers & Pages -> Hello World.
- Ему был выдан код HTTP API Worker: принимает только `POST`, проверяет `Authorization: Bearer <SECRETARY_TOKEN>`, ожидает `{messages: [...]}`, вызывает binding `env.AI.run("@cf/meta/llama-3.1-8b-instruct", ...)`, возвращает `{text: ...}`.
- Текущий факт деплоя/вставки кода **не подтверждён**: сначала проверить Dashboard или endpoint. Не предполагать, что Worker готов.
- Для завершения требуется в Dashboard Worker `secretary-llm`:
  1. добавить Workers AI binding с именем `AI`;
  2. создать secret `SECRETARY_TOKEN` (значение генерируется и вводится server-side, не в чат);
  3. deploy;
  4. записать endpoint и тот же bearer token только в `/etc/telegram-secretary/secrets.env`;
  5. сделать одну безопасную тестовую LLM-сводку без вывода сырых сообщений в логи.
- Лучше ограничить prompt: отправлять не более необходимого числа последних сообщений, запрашивать краткий JSON/структуру с `summary`, `events[]`, `uncertainties[]`; запрещать модели фантазировать и создавать календарные события.

## Следующая рекомендуемая работа

1. Сделать initial commit и настроить remote после указания владельца.
2. Закончить `secretary-llm` Worker и проверить его одним безопасным запросом.
3. Привязать `CALL_ANALYSIS_PROVIDER=cloudflare_worker` к Worker endpoint и сделать smoke-test без вывода сырых сообщений в логи.
4. Для реальных звонков выбрать транспорт: Twilio сейчас подготовлен; Zvonobot можно добавить отдельным adapter после получения API docs/key.
5. Настроить STT: либо provider transcript callback, либо Cloudflare Whisper для recording URL.
6. Изменить `/analyze <имя>`: возвращать краткую сводку, список найденных договорённостей с уверенностью и черновик напоминания. Не выводить длинные/raw messages.
7. Добавить owner-only действие подтверждения: «создать напоминание» как черновик. До отдельного дизайна write-flow не вызывать CalDAV PUT.
8. Только затем проектировать фоновый monitoring/push notifications и Craft.

## Код и проверка

- Python 3.12, stdlib + Telethon. Тесты: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests`.
- Проект пока не имеет Git remote и initial commit. Не выдумывать remote; сначала согласовать с владельцем. Общая память живёт в отдельном `agent-memory` GitHub repository.
- При деплое пересобирать только image `telegram-secretary:local` и перезапускать только `telegram-secretary-docker.service`.

## Коммуникация с владельцем

- Он предпочитает русский, короткие точные инструкции и один следующий шаг, особенно во время ручной настройки.
- Если требуется его действие/разрешение, сказать прямо: что именно и зачем. Не повторять уже выполненные инструкции.
- Он хочет, чтобы весь существенный прогресс и решения фиксировались в shared `agent-memory` repository.
