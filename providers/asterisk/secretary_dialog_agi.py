#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


CONFIG_FILES = (
    Path("/etc/telegram-secretary/asterisk.env"),
    Path("/etc/telegram-secretary/runtime.env"),
    Path("/etc/telegram-secretary/secrets.env"),
)
WORK_DIR = Path("/var/lib/telegram-secretary/asterisk/calls")
DEFAULT_TOKEN_NAME = "LLM_WORKER_BEARER_TOKEN"


class AGI:
    def __init__(self) -> None:
        self.env = self._read_env()

    def command(self, command: str) -> str:
        print(command, flush=True)
        return sys.stdin.readline().strip()

    def verbose(self, message: str) -> None:
        self.command(f'VERBOSE "{escape_agi(message)}" 1')

    def get_variable(self, name: str) -> str:
        response = self.command(f"GET VARIABLE {name}")
        match = re.search(r"result=1 \\((.*)\\)", response)
        return match.group(1) if match else ""

    def stream_file(self, path_without_extension: Path) -> None:
        self.command(f'STREAM FILE {path_without_extension} ""')

    def record_file(self, path_without_extension: Path, timeout_ms: int) -> None:
        self.command(f'RECORD FILE {path_without_extension} wav "#" {timeout_ms} BEEP s=2')

    @staticmethod
    def _read_env() -> dict[str, str]:
        values: dict[str, str] = {}
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.rstrip("\n")
            if not line:
                break
            if ":" in line:
                key, value = line.split(":", 1)
                values[key.strip()] = value.strip()
        return values


def main() -> None:
    agi = AGI()
    config = load_config()
    task_path = agi.get_variable("SECRETARY_TASK_PATH")
    if not task_path:
        fail(agi, config, {}, "missing_task_path", "No SECRETARY_TASK_PATH channel variable.")
        return

    try:
        task = json.loads(Path(task_path).read_text(encoding="utf-8"))
    except Exception as exc:
        fail(agi, config, {}, "bad_task", str(exc))
        return

    request_id = clean_text(task.get("requestId") or agi.get_variable("SECRETARY_REQUEST_ID"))
    call_dir = WORK_DIR / safe_filename(request_id or str(int(time.time())))
    call_dir.mkdir(parents=True, exist_ok=True)
    agi.verbose(f"secretary call started {request_id}")

    turns: list[dict[str, str]] = []
    latest_text = ""
    max_turns = clamp_int(task.get("maxTurns"), 8, 1, 20)
    max_record_ms = max(10000, min(clamp_int(task.get("maxDurationSeconds"), 480, 60, 1800) * 1000, 60000))

    for turn_index in range(max_turns):
        decision = dialogue_turn(task, config, turns, latest_text, turn_index, max_turns)
        phrase = clean_text(decision.get("say")) or "Извините, не получилось получить следующий вопрос."
        done = bool(decision.get("done"))
        turns.append({"speaker": "bot", "text": phrase})
        speak(agi, call_dir / f"prompt_{turn_index}", phrase)
        if done:
            post_final(task, config, turns, "dialogue_done")
            return

        record_base = call_dir / f"answer_{turn_index}"
        agi.record_file(record_base, max_record_ms)
        try:
            latest_text = transcribe(config, record_base.with_suffix(".wav"))
        except Exception as exc:
            latest_text = ""
            agi.verbose(f"ASR failed: {str(exc)[:160]}")
        if latest_text:
            turns.append({"speaker": "human", "text": latest_text})
            agi.verbose(f"ASR result: {latest_text[:160]}")
        else:
            agi.verbose("ASR result is empty")

    post_final(task, config, turns, "max_turns")


def dialogue_turn(
    task: dict[str, Any],
    config: dict[str, str],
    turns: list[dict[str, str]],
    latest_text: str,
    turn_index: int,
    max_turns: int,
) -> dict[str, Any]:
    try:
        return post_json(
            config,
            "/dialogue-turn",
            {
                "requestId": task.get("requestId") or "",
                "targetName": task.get("targetName") or "Организация",
                "goal": task.get("goal") or "",
                "questions": task.get("questions") if isinstance(task.get("questions"), list) else [],
                "latestText": latest_text,
                "turns": turns,
                "turnIndex": turn_index,
                "maxTurns": max_turns,
            },
        )
    except Exception:
        return fallback_dialogue_turn(task, turns, latest_text, turn_index, max_turns)


def fallback_dialogue_turn(
    task: dict[str, Any],
    turns: list[dict[str, str]],
    latest_text: str,
    turn_index: int,
    max_turns: int,
) -> dict[str, Any]:
    questions = task.get("questions") if isinstance(task.get("questions"), list) else []
    if turn_index == 0:
        first_question = clean_text(questions[0] if questions else task.get("goal") or "")
        return {
            "say": (
                "Здравствуйте. Это автоматический помощник Матвея. "
                "Разговор записывается, чтобы точно передать ответ. "
                f"Подскажите, пожалуйста: {ensure_question(first_question)}"
            ),
            "done": False,
        }
    if not latest_text and turn_index < max_turns - 1:
        return {"say": "Извините, я плохо расслышал. Можете повторить чуть короче?", "done": False}
    answered = len([turn for turn in turns if turn.get("speaker") == "human" and turn.get("text")])
    if answered < len(questions) and turn_index < max_turns - 1:
        return {"say": f"Понял. Ещё уточните: {ensure_question(questions[answered])}", "done": False}
    return {"say": "Спасибо, я всё записал и передам Матвею.", "done": True}


def speak(agi: AGI, base_path: Path, text: str) -> None:
    raw_wav = base_path.with_name(base_path.name + "_raw.wav")
    sln_path = base_path.with_suffix(".sln")
    subprocess.run(
        ["espeak-ng", "-v", os.getenv("ASTERISK_TTS_VOICE", "ru"), "-s", "155", "-w", str(raw_wav), text],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            "sox",
            str(raw_wav),
            "-t",
            "raw",
            "-r",
            "8000",
            "-e",
            "signed-integer",
            "-b",
            "16",
            "-c",
            "1",
            str(sln_path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    agi.stream_file(base_path)


def transcribe(config: dict[str, str], audio_path: Path) -> str:
    if not audio_path.is_file() or audio_path.stat().st_size < 1024:
        return ""
    request = Request(
        worker_url(config, "/asr"),
        data=audio_path.read_bytes(),
        headers={
            "Authorization": f"Bearer {worker_token(config)}",
            "Content-Type": "audio/wav",
            "User-Agent": "telegram-secretary-asterisk/0.1",
        },
        method="POST",
    )
    with urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return clean_text(payload.get("text") or "")


def post_final(
    task: dict[str, Any],
    config: dict[str, str],
    turns: list[dict[str, str]],
    reason: str,
) -> None:
    try:
        post_json(
            config,
            "/dialogue-final",
            {
                "requestId": task.get("requestId") or "",
                "targetName": task.get("targetName") or "Организация",
                "goal": task.get("goal") or "",
                "questions": task.get("questions") if isinstance(task.get("questions"), list) else [],
                "turns": turns,
                "reason": reason,
                "recordingUrl": "",
                "destination": task.get("destination") or "",
                "transport": "asterisk",
                "ownerTelegramChatId": task.get("ownerTelegramChatId") or "",
            },
        )
    except (HTTPError, URLError, TimeoutError, OSError):
        return


def fail(
    agi: AGI,
    config: dict[str, str],
    task: dict[str, Any],
    reason: str,
    detail: str,
) -> None:
    agi.verbose(f"secretary call failed: {reason}: {detail[:160]}")
    if task:
        task["failureDetail"] = detail
        post_final(task, config, [], reason)


def post_json(config: dict[str, str], path: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = Request(
        worker_url(config, path),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {worker_token(config)}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "telegram-secretary-asterisk/0.1",
        },
        method="POST",
    )
    with urlopen(request, timeout=45) as response:
        data = response.read().decode("utf-8")
    parsed = json.loads(data or "{}")
    return parsed if isinstance(parsed, dict) else {}


def worker_url(config: dict[str, str], path: str) -> str:
    base = config.get("LLM_WORKER_URL") or config.get("VOXIMPLANT_WORKER_URL") or ""
    if not base:
        raise RuntimeError("LLM_WORKER_URL is missing.")
    return base.rstrip("/") + path


def worker_token(config: dict[str, str]) -> str:
    token = config.get(DEFAULT_TOKEN_NAME) or config.get("SECRETARY_AI_TOKEN") or ""
    if not token:
        raise RuntimeError("LLM_WORKER_BEARER_TOKEN is missing.")
    return token


def load_config() -> dict[str, str]:
    values: dict[str, str] = dict(os.environ)
    for path in CONFIG_FILES:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def ensure_question(text: object) -> str:
    cleaned = clean_text(text) or "подскажите актуальную информацию"
    return cleaned if cleaned.endswith(("?", "!", ".")) else cleaned + "?"


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return cleaned.strip("._") or "call"


def clamp_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def escape_agi(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


if __name__ == "__main__":
    main()
