# Telegram Secretary MVP

> Для продолжения работы агентом/Claude Code сначала читай [CLAUDE.md](CLAUDE.md): там зафиксировано фактическое состояние развёртывания, подключений и ближайший план. Этот README описывает исходный MVP-каркас и частично устарел.

MVP личного Telegram-секретаря: отдельный чат с ботом для управления, мониторинг входящих сообщений личного Telegram-аккаунта, выделение важного, ответы на вопросы пользователя о переписке, сводки, проверка доступности по Apple Calendar, учет заметок/задач из Craft и голосовые напоминания.

## Архитектура

Поток обработки входящих:

1. **Telegram user ingestor** читает входящие сообщения личного аккаунта через MTProto-клиент Telethon. Это не Bot API: бот не может читать личные чаты пользователя. Сессия Telegram создается только на сервере после явного ручного логина пользователя.
2. **Normalizer** приводит входящие сообщения к единой модели `IncomingMessage`.
3. **Classifier** определяет важность и намерение: календарный вопрос, общий вопрос, просьба о действии, FYI или неизвестно.
4. **Apple Calendar provider** получает свободные/занятые окна из локального Apple Calendar на macOS. Для автоответов наружу используется только факт доступности, без названий событий и участников.
5. **Notes/tasks provider** подмешивает контекст Craft: заметки, список дел на сегодня, дедлайны и пометки для напоминаний. Если прямой Craft-доступ нельзя использовать в headless-сервисе, MVP переключается на локальный Markdown/JSON-export из Craft или Apple Reminders как источник задач.
6. **AutoReplyPolicy** решает, можно ли отправить ответ автоматически. Автоответ разрешен только для заранее разрешенных типовых вопросов о доступности, доверенных отправителей и высокой уверенности классификации.
7. **Secretary bot** пишет пользователю в отдельный приватный чат: важные входящие, сводки, черновики ответов, сегодняшние дела и кнопки подтверждения/отклонения.
8. **Voice provider** принимает звонки пользователя на номер секретаря и озвучивает ближайшие дела. Отдельный business-call workflow умеет по owner-only команде готовить или ставить исходящий звонок в организацию, записывать ответ, транскрибировать запись и разбирать ее в структурные факты.
9. **Storage/audit log** хранит входящие, решения политики, черновики, прочитанные источники задач и действия пользователя в SQLite.

Граница безопасности:

- Автоматически отправляются только короткие ответы о свободных окнах.
- Автоматический ответ не создает, не переносит и не отменяет встречи.
- Автоматический ответ не раскрывает названия событий, участников, заметки Craft и детали календаря.
- Все нестандартные вопросы, неоднозначные сообщения и просьбы о действиях превращаются в черновик для подтверждения.
- Исходящие звонки-напоминания идут только пользователю-владельцу.
- Исходящие research-звонки организациям доступны только владельцу через отдельную команду, по allowlist телефонных префиксов и в dry-run режиме по умолчанию.
- Секреты не запрашиваются и не сохраняются в чате. Настройка идет через локальный `.env`, системный keychain или секрет-хранилище окружения.

## Пользовательский сценарий

1. Пользователь создает Telegram-бота и открывает с ним приватный чат.
2. Пользователь локально настраивает переменные окружения из `.env.example`.
3. После отдельного подтверждения пользователь вручную запускает Telethon-login на сервере, вводит одноразовый код/2FA только в серверной консоли и получает локальный session-файл.
4. На macOS пользователь дает сервису read-only доступ к Apple Calendar.
5. Пользователь подключает Craft как предпочтительный источник заметок и задач либо включает экспорт Craft Today/Tasks в локальную папку.
6. Сервис слушает входящие личного аккаунта.
7. Если сообщение важно, бот-секретарь присылает карточку: кто написал, текст, почему важно, рекомендуемый ответ.
8. Пользователь может спросить у бота: "кто писал сегодня?", "сводка за утро", "что срочного?", "что у меня дальше?", "какие задачи на сегодня?".
9. Если доверенный контакт спрашивает типовой вопрос о доступности, сервис проверяет Apple Calendar и может отправить безопасный автоответ.
10. Если сообщение требует решения, обещания, переноса встречи или нестандартного ответа, бот показывает черновик и ждет подтверждения.
11. Пользователь может позвонить секретарю и спросить о ближайших делах; секретарь отвечает голосом на основе Apple Calendar и сегодняшних задач.
12. Секретарь может позвонить пользователю с напоминанием, если событие/задача подходит под правила срочности, времени и quiet hours.

## Требования к подключениям

Telegram:

- `TELEGRAM_API_ID` и `TELEGRAM_API_HASH` для Telethon/MTProto-клиента берутся на `my.telegram.org`.
- `TELEGRAM_USER_PHONE_NUMBER` нужен для ручного входа личного аккаунта на сервере.
- `TELEGRAM_USER_SESSION_DIR` указывает на Telethon-хранилище. На сервере это `/var/lib/telegram-secretary/sessions/telethon`, смонтированное в контейнер как `/app/sessions/telethon`.
- Telethon-каталог должен быть `0700`, session-файлы `0600`, их нельзя коммитить или копировать в чат.
- `TELEGRAM_BOT_TOKEN` нужен только для чата с ботом-секретарем.
- `SECRETARY_OWNER_TELEGRAM_ID` ограничивает доступ к боту одним пользователем.
- `AUTO_REPLY_TRUSTED_SENDER_IDS` задает отправителей, которым разрешены безопасные автоответы.
- Текущий MVP включает только owner-only команды `/tgstatus`, `/dialog <человек>` и `/analyze <человек>` как безопасные заглушки. Они не читают личные чаты, пока Telethon-сессия не создана и ingest не включен отдельным подтверждением.

Apple Calendar:

- Основной календарь MVP на сервере: iCloud CalDAV read-only.
- Доступ задается только через server secrets: `APPLE_CALENDAR_USERNAME` и `APPLE_CALENDAR_APP_PASSWORD`.
- App-specific password пользователь вводит сам на сервере; значение нельзя писать в чат, код, логи или memory.
- `APPLE_CALENDAR_IDS` ограничивает набор календарей.
- Сервис читает только занятость и наружу сообщает только свободные окна, без названий событий, участников и заметок.
- Owner-only команды `/today` и `/free` показывают свободные окна на сегодня. Пока credentials не введены, бот безопасно отвечает, что Apple Calendar не подключен.

Craft, заметки и задачи:

- Предпочтительный источник: Craft daily note / документ задач / коллекция задач.
- В текущей среде доступен Craft-коннектор для чтения и поиска документов, но отдельному серверному сервису нужен свой способ доступа.
- Первый работающий релиз должен поддержать `CRAFT_SOURCE_MODE=markdown_export`: Craft экспортирует Today/Tasks в локальную папку, сервис парсит чекбоксы и заметки.
- Если прямой Craft-доступ недоступен, замена для задач: Apple Reminders или Todoist. Замена для заметок: локальная Markdown-папка с ежедневными заметками.
- Секретарь учитывает сегодняшние задачи в сводках, ответах пользователю и правилах напоминаний.

Голос:

- Telegram Bot API не поддерживает телефонные звонки боту. Для настоящих звонков нужен внешний voice/SIP-провайдер. Основной путь для живого диалога: Voximplant как движок диалога/ASR/TTS, а исходящий PSTN-выход либо через Voximplant Caller ID, либо через внешний SIP-транк. MTS Exolve оставлен как старый one-shot fallback, Twilio оставлен только как старый опциональный адаптер.
- `VOICE_PROVIDER` выбирает провайдера, `VOICE_OWNER_PHONE_E164` задает номер владельца, `VOICE_OUTBOUND_ENABLED` включает исходящие звонки.
- Входящий звонок: STT -> интент -> ответ о ближайших делах/задачах -> TTS.
- Исходящий звонок: только владельцу, только по правилам `CALL_REMINDER_*`, с quiet hours и rate limit.
- Business research-звонок: owner-only команда `/call +79991234567 | Теннисный клуб | узнать абонементы; какие дни свободны` создает запрос и запускает провайдера звонка.
- `VOICE_BUSINESS_CALL_PROVIDER=voximplant_dialog` запускает живой диалог: Voximplant звонит, слушает через ASR, спрашивает следующие вопросы через `secretary-ai` Worker и после разговора отправляет финальный разбор в Telegram, если в Worker заданы `TELEGRAM_BOT_TOKEN` и `SECRETARY_OWNER_TELEGRAM_ID`.
- Сценарий для Voximplant лежит в `providers/voximplant/secretary_dialogue_scenario.js`; его нужно вставить в Application -> Scenarios и привязать к routing rule. В Voximplant Secrets нужно добавить `SECRETARY_AI_TOKEN` со значением `LLM_WORKER_BEARER_TOKEN`.
- Для запуска через Voximplant PSTN нужны `VOICE_BUSINESS_CALLS_ENABLED=true`, `VOICE_BUSINESS_CALL_PROVIDER=voximplant_dialog`, `VOXIMPLANT_OUTBOUND_TRANSPORT=pstn`, `VOXIMPLANT_RULE_ID`, `VOXIMPLANT_CALLER_ID`, `VOXIMPLANT_CREDENTIALS_FILE=/etc/telegram-secretary/voximplant-credentials.json`, `LLM_WORKER_URL`.
- Для запуска через внешний SIP-транк нужны `VOXIMPLANT_OUTBOUND_TRANSPORT=sip`, `VOXIMPLANT_SIP_URI_TEMPLATE` и, если оператор требует авторизацию, `VOXIMPLANT_SIP_AUTH_USER` плюс `VOXIMPLANT_SIP_PASSWORD_SECRET_NAME`. SIP URI можно собирать из `{destination}`, `{destination_digits}`, `{destination_local}`, `{destination_trunk8}`.
- Старый Exolve fallback: `VOICE_BUSINESS_CALL_PROVIDER=exolve`, `EXOLVE_API_KEY` и `EXOLVE_SOURCE_PHONE`. Это не живой диалог, а voice-message + запись/разбор.
- Для первой настоящей проверки без публичного webhook есть CLI `call-live-test`: он звонит через выбранного провайдера, ждёт завершения, скачивает запись или берет транскрипт провайдера, отправляет аудио в Cloudflare Whisper и затем разбирает транскрипт.
- Для русского языка встроенная транскрибация провайдера не считается обязательной: сервис принимает готовый transcript callback или может скачать recording URL и отправить запись в `VOICE_RECORDING_TRANSCRIBER=cloudflare_whisper`.

LLM:

- `OPENAI_API_KEY` опционален для будущей LLM-классификации, суммаризации, голосового NLU и генерации черновиков.
- `CALL_ANALYSIS_PROVIDER=rule_based` работает локально без внешних API. `CALL_ANALYSIS_PROVIDER=cloudflare_worker` отправляет транскрипт в `secretary-ai` Worker через `LLM_WORKER_URL` и `LLM_WORKER_BEARER_TOKEN`, ожидая строгий JSON с `summary`, `facts`, `missing_items`, `next_actions`.
- `VOICE_RECORDING_TRANSCRIBER=cloudflare_worker` отправляет аудиозапись в тот же `secretary-ai` Worker на `/asr`, а Worker вызывает Cloudflare Whisper через AI binding. В сервисе не нужен `CLOUDFLARE_ACCOUNT_ID`/`CLOUDFLARE_API_TOKEN`.
- В стартовом каркасе есть rule-based ядро, чтобы политику безопасности можно было тестировать без внешних сервисов.

## Первый работающий релиз

Входит:

- Telegram-чат с ботом-секретарем для `/inbox`, `/summary`, `/today`, подтверждения/отклонения черновиков.
- Подготовленный путь для чтения входящих Telegram личного аккаунта через Telethon после ручной серверной авторизации.
- SQLite audit log.
- Rule-based классификация важности и календарных вопросов.
- Apple Calendar read-only: свободные окна и ближайшие события.
- Craft-задачи/заметки через локальный экспорт или подключенный read/search adapter.
- Daily briefing: ближайшие события, задачи на сегодня, важные входящие.
- Безопасные автоответы о доступности только для allowlist-контактов.
- Голосовой dry-run и опциональная Twilio/Vonage-интеграция: входящий звонок владельца с ответом о ближайших делах; исходящие звонки-напоминания владельцу; owner-only research-звонки организациям с записью и post-processing.

Не входит в первый релиз:

- Автоматическое создание/перенос/отмена встреч.
- Автоответы на нестандартные сообщения.
- Автоматические исходящие звонки контактам из Telegram без отдельной owner-only команды.
- Раскрытие деталей календаря или заметок собеседникам.
- Полная двусторонняя синхронизация Craft без подтвержденного headless API/экспорта.

## Deployment

Подготовлен безопасный серверный dry-run layout для временного адреса `secretary.vacanator.xyz`: Docker Compose, отдельный systemd wrapper-unit, env/secrets layout, локальный health endpoint и обратимый план установки.

Важные ограничения текущего этапа:

- DNS A-запись создана в REG.RU, публичное разрешение подтверждено.
- TLS, nginx-конфигурация и Telegram webhooks не включаются до отдельного подтверждения.
- Сервис стартует локально на `127.0.0.1:18097` в `dry_run` режиме без реальных секретов.
- Live polling режим остается локально привязанным к `127.0.0.1:18097`; Telegram webhook/TLS/nginx выключены.
- Существующие Docker/nginx/systemd сервисы на сервере не изменяются и не перезапускаются.

План и файлы: [docs/deployment-plan.md](docs/deployment-plan.md), [deploy/](deploy/).

## Стартовая структура

```text
.
├── .env.example
├── README.md
├── pyproject.toml
├── src/telegram_secretary/
│   ├── __init__.py
│   ├── __main__.py
│   ├── calendar.py
│   ├── availability.py
│   ├── classifier.py
│   ├── config.py
│   ├── models.py
│   ├── notes.py
│   ├── policy.py
│   ├── secretary.py
│   ├── storage.py
│   ├── voice.py
│   └── adapters/
│       ├── __init__.py
│       ├── apple_calendar.py
│       ├── icloud_caldav.py
│       ├── craft_notes.py
│       ├── secretary_bot.py
│       ├── telegram_user.py
│       └── telephony.py
└── tests/
    ├── test_calendar.py
    ├── test_icloud_caldav.py
    ├── test_availability.py
    ├── test_notes.py
    ├── test_policy.py
    └── test_storage.py
```

## Пошаговый план реализации

1. Зафиксировать policy gate: какие намерения, контакты и шаблоны допускают автоответ.
2. Подключить SQLite и audit log для входящих сообщений, решений и подтверждений.
3. Подключить Telegram user ingestor через Telethon: только чтение входящих и передача в `SecretaryCore`.
4. Подключить secretary bot через aiogram: команды `/summary`, `/inbox`, `/pending`, `/approve`, `/reject`.
5. Подключить Apple Calendar read-only adapter через iCloud CalDAV и owner-only команды `/today`/`/free`.
6. Подключить Craft notes/tasks provider. При недоступности прямого режима включить Markdown/JSON-export из Craft или Apple Reminders как источник задач.
7. Добавить `/today`: ближайшие календарные события, задачи на сегодня, важные непрочитанные входящие.
8. Добавить e2e dry-run режим: входящие читаются и решения показываются в боте, но ответы не отправляются.
9. Добавить voice dry-run: текстовый сценарий звонка без реального провайдера.
10. Подключить Twilio/Vonage/SIP adapter для входящих звонков владельца.
11. Добавить исходящие звонки-напоминания владельцу с quiet hours, allowlist типов напоминаний и rate limit.
12. Добавить business research-звонки: `/call`, Twilio recording callback, транскрибацию записи, LLM JSON post-processing.
13. Добавить LLM-провайдера для более качественной классификации, суммаризации и черновиков, но оставить policy gate детерминированным.
14. Включить автоответы только после локального smoke-test и заполнения allowlist контактов.

## Локальная проверка ядра

```bash
python -m unittest discover -s tests
PYTHONPATH=src python -m telegram_secretary simulate --sender-id 42 --text "Ты свободен завтра днем?"
PYTHONPATH=src python -m telegram_secretary call-dry-run "+79991234567 | Теннисный клуб | узнать стоимость абонемента; какие дни свободны"
PYTHONPATH=src python -m telegram_secretary call-analyze "+79991234567 | Ресторан | есть ли стол на 20:00" --transcript "Стол на 20:00 можно забронировать, депозит не нужен."
VOICE_BUSINESS_CALLS_ENABLED=true VOICE_BUSINESS_CALL_PROVIDER=voximplant_dialog PYTHONPATH=src python -m telegram_secretary call-live-test "+79991234567 | Тест | спроси, что я сказал"
VOICE_BUSINESS_CALLS_ENABLED=true VOICE_BUSINESS_CALL_PROVIDER=voximplant_dialog VOXIMPLANT_OUTBOUND_TRANSPORT=sip VOXIMPLANT_SIP_URI_TEMPLATE='sip:{destination_digits}@sip.provider.example' PYTHONPATH=src python -m telegram_secretary call-live-test "+79991234567 | Тест SIP | спроси, что я сказал"
```
