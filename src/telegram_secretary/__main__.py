from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telegram_secretary.adapters.telephony import (
    ExolveLiveTestRunner,
    TwilioLiveTestRunner,
    build_call_research_service,
)
from telegram_secretary.calendar import BusyWindow, StaticCalendarProvider, default_business_window
from telegram_secretary.call_research import (
    CallResearchService,
    CallRecordingNotice,
    CallTranscript,
    DryRunBusinessCallProvider,
    parse_business_call_argument,
    render_call_extraction,
    render_call_placement,
)
from telegram_secretary.classifier import RuleBasedClassifier
from telegram_secretary.config import AppConfig
from telegram_secretary.health import check_url, serve_health
from telegram_secretary.models import IncomingMessage
from telegram_secretary.policy import AutoReplyPolicy
from telegram_secretary.secretary import SecretaryCore
from telegram_secretary.storage import SQLiteStore
from telegram_secretary.telegram_bot import (
    TelegramBotPoller,
    TelegramPollingState,
    should_start_polling,
)
from telegram_secretary.telethon_login import (
    add_telethon_login_arguments,
    run_telethon_login_command,
)


def main() -> None:
    parser = argparse.ArgumentParser(prog="telegram-secretary")
    subparsers = parser.add_subparsers(dest="command", required=True)

    simulate = subparsers.add_parser("simulate", help="Run one message through the MVP policy")
    simulate.add_argument("--sender-id", required=True)
    simulate.add_argument("--sender-name", default="Test Sender")
    simulate.add_argument("--text", required=True)
    simulate.add_argument("--auto-reply-enabled", action="store_true")
    simulate.add_argument("--trusted-sender-id", action="append", default=[])

    serve = subparsers.add_parser("serve", help="Run the minimal health/readiness HTTP server")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)

    healthcheck = subparsers.add_parser("healthcheck", help="Check an HTTP health endpoint")
    healthcheck.add_argument("--url", default="http://127.0.0.1:18097/healthz")
    healthcheck.add_argument("--timeout-seconds", type=float, default=3.0)

    call_dry_run = subparsers.add_parser(
        "call-dry-run",
        help="Parse a business-call request and render the voice script without placing a call",
    )
    call_dry_run.add_argument("request", help="Same format as /call")

    call_analyze = subparsers.add_parser(
        "call-analyze",
        help="Analyze an already transcribed business call",
    )
    call_analyze.add_argument("request", help="Same format as /call")
    transcript_source = call_analyze.add_mutually_exclusive_group(required=True)
    transcript_source.add_argument("--transcript")
    transcript_source.add_argument("--transcript-file")

    call_live_test = subparsers.add_parser(
        "call-live-test",
        help="Place one real phone call via the configured provider",
    )
    call_live_test.add_argument("request", help="Same format as /call")
    call_live_test.add_argument("--wait-seconds", type=int, default=240)
    call_live_test.add_argument("--poll-interval-seconds", type=float, default=5.0)

    telegram_login = subparsers.add_parser(
        "telegram-login",
        help="Run one-time server-console Telethon login for the owner's personal account",
    )
    add_telethon_login_arguments(telegram_login)

    args = parser.parse_args()
    if args.command == "simulate":
        run_simulation(args)
    elif args.command == "serve":
        config = AppConfig.from_env()
        store = SQLiteStore(config.database_path)
        store.initialize()
        call_service = build_call_research_service(config, store=store)
        polling_state = TelegramPollingState()
        if should_start_polling(config):
            TelegramBotPoller(config, polling_state, call_service=call_service).start()
        serve_health(
            args.host or config.health_host,
            args.port or config.health_port,
            config,
            polling_state.snapshot,
            call_service=call_service,
        )
    elif args.command == "healthcheck":
        raise SystemExit(check_url(args.url, timeout_seconds=args.timeout_seconds))
    elif args.command == "call-dry-run":
        run_call_dry_run(args)
    elif args.command == "call-analyze":
        run_call_analyze(args)
    elif args.command == "call-live-test":
        run_call_live_test(args)
    elif args.command == "telegram-login":
        raise SystemExit(run_telethon_login_command(args))


def run_simulation(args: argparse.Namespace) -> None:
    now = datetime.now(timezone.utc)
    business_start, _ = default_business_window(now)
    calendar = StaticCalendarProvider(
        busy_windows=[
            BusyWindow(
                start=business_start + timedelta(hours=4),
                end=business_start + timedelta(hours=5),
            )
        ]
    )
    core = SecretaryCore(
        classifier=RuleBasedClassifier(),
        policy=AutoReplyPolicy(
            trusted_sender_ids=frozenset(args.trusted_sender_id),
            auto_reply_enabled=args.auto_reply_enabled,
        ),
        calendar=calendar,
    )
    message = IncomingMessage(
        message_id="simulation-1",
        chat_id="simulation-chat",
        sender_id=args.sender_id,
        sender_name=args.sender_name,
        text=args.text,
        received_at=now,
    )
    decision = core.handle_incoming(message)
    print(
        json.dumps(
            {
                "action": decision.action.value,
                "draft_text": decision.draft_text,
                "requires_confirmation": decision.requires_confirmation,
                "reasons": decision.reasons,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def run_call_dry_run(args: argparse.Namespace) -> None:
    parsed = parse_business_call_argument(args.request)
    if not parsed.is_valid:
        raise SystemExit(parsed.error)
    service = CallResearchService(provider=DryRunBusinessCallProvider())
    request = service.create_request_from_command(parsed)
    placement = service.place_call(request)
    print(render_call_placement(request, placement))


def run_call_analyze(args: argparse.Namespace) -> None:
    parsed = parse_business_call_argument(args.request)
    if not parsed.is_valid:
        raise SystemExit(parsed.error)
    config = AppConfig.from_env()
    service = build_call_research_service(config)
    request = service.create_request_from_command(
        parsed,
        max_duration_seconds=config.voice_business_call_max_duration_seconds,
    )
    transcript_text = args.transcript
    if args.transcript_file:
        transcript_text = Path(args.transcript_file).read_text(encoding="utf-8")
    transcript = CallTranscript(
        request_id=request.request_id,
        provider_call_id=None,
        transcript_text=transcript_text,
        source="cli",
    )
    extraction = service.analyze_transcript(request, transcript)
    print(render_call_extraction(extraction))


def run_call_live_test(args: argparse.Namespace) -> None:
    parsed = parse_business_call_argument(args.request)
    if not parsed.is_valid:
        raise SystemExit(parsed.error)

    config = AppConfig.from_env()
    service = build_call_research_service(config)

    request = service.create_request_from_command(
        parsed,
        max_duration_seconds=config.voice_business_call_max_duration_seconds,
    )
    try:
        provider_name = config.voice_business_call_provider.casefold()
        if provider_name in {"exolve", "mts_exolve"}:
            recording = ExolveLiveTestRunner(config).place_call_and_wait_for_recording(
                request,
                wait_seconds=args.wait_seconds,
                poll_interval_seconds=args.poll_interval_seconds,
            )
            transcript_text = recording.transcription_text or ""
            if not transcript_text:
                if service.transcriber is None:
                    raise RuntimeError(
                        "Для Exolve включи STT: "
                        "VOICE_RECORDING_TRANSCRIBER=cloudflare_worker, "
                        "LLM_WORKER_URL и LLM_WORKER_BEARER_TOKEN."
                    )
                transcript_text = service.transcriber.transcribe(
                    CallRecordingNotice(
                        request_id=request.request_id,
                        provider="exolve",
                        provider_call_id=recording.call_id,
                        recording_url=recording.recording_url,
                        recording_status=recording.status,
                        duration_seconds=recording.duration_seconds,
                    )
                )
            provider_call_id = recording.call_id
            recording_url = recording.recording_url
            duration_seconds = recording.duration_seconds
        elif provider_name == "twilio":
            if service.transcriber is None:
                raise RuntimeError(
                    "Для Twilio включи STT: "
                    "VOICE_RECORDING_TRANSCRIBER=cloudflare_worker, "
                    "LLM_WORKER_URL и LLM_WORKER_BEARER_TOKEN."
                )
            recording = TwilioLiveTestRunner(config).place_call_and_wait_for_recording(
                request,
                wait_seconds=args.wait_seconds,
                poll_interval_seconds=args.poll_interval_seconds,
            )
            transcript_text = service.transcriber.transcribe(
                CallRecordingNotice(
                    request_id=request.request_id,
                    provider="twilio",
                    provider_call_id=recording.call_sid,
                    recording_url=recording.recording_url,
                    recording_status="completed",
                    duration_seconds=recording.duration_seconds,
                )
            )
            provider_call_id = recording.call_sid
            recording_url = recording.recording_url
            duration_seconds = recording.duration_seconds
        elif provider_name in {
            "vox",
            "voximplant",
            "voximplant_dialog",
            "voximplant_dialogue",
        }:
            placement = service.place_call(request)
            print(render_call_placement(request, placement))
            print(
                "Диалог и финальный разбор выполняет Voximplant "
                "scenario через Cloudflare Worker. Если в Worker заданы "
                "TELEGRAM_BOT_TOKEN и SECRETARY_OWNER_TELEGRAM_ID, "
                "результат придет в Telegram."
            )
            return
        else:
            raise RuntimeError(
                "Для call-live-test поставь "
                "VOICE_BUSINESS_CALL_PROVIDER=voximplant_dialog "
                "и VOICE_BUSINESS_CALLS_ENABLED=true."
            )
    except Exception as exc:
        raise SystemExit(f"call-live-test failed: {exc}") from exc
    transcript = CallTranscript(
        request_id=request.request_id,
        provider_call_id=provider_call_id,
        transcript_text=transcript_text,
        source="call-live-test",
        recording_url=recording_url,
        duration_seconds=duration_seconds,
    )
    extraction = service.analyze_transcript(request, transcript)
    print(render_call_extraction(extraction))


if __name__ == "__main__":
    main()
