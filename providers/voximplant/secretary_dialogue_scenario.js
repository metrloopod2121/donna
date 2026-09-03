require(Modules.ASR);
require(Modules.Net);

const DEFAULT_WORKER_SECRET = "SECRETARY_AI_TOKEN";
const DEFAULT_ASR_LANGUAGE = "ASRLanguage.RUSSIAN_RU";
const DEFAULT_VOICE = "VoiceList.Yandex.ru_RU_oksana";
const NO_INPUT_TIMEOUT_MS = 9000;
const TURN_PAUSE_MS = 2500;
const MAX_UTTERANCE_MS = 35000;

let task = {};
let call = null;
let asr = null;
let listeningText = "";
let turns = [];
let turnIndex = 0;
let finalized = false;
let recordingUrl = "";
let pendingAfterPlayback = null;
let noInputTimer = null;
let pauseTimer = null;
let utteranceTimer = null;
let startWaitTimer = null;

VoxEngine.addEventListener(AppEvents.HttpRequest, (event) => {
  try {
    const incomingTask = JSON.parse(event.content || "{}");
    task = normalizeTask(Object.assign({}, task, incomingTask));
  } catch (error) {
    Logger.write(`Bad session task JSON: ${error}`);
    return;
  }
  startOutboundCall();
});

VoxEngine.addEventListener(AppEvents.Started, () => {
  try {
    task = normalizeTask(JSON.parse(VoxEngine.customData() || "{}"));
  } catch (error) {
    failFast("bad_custom_data");
    return;
  }

  if (task.workerUrl && hasDialTarget()) {
    startOutboundCall();
  } else {
    startWaitTimer = setTimeout(() => failFast("session_task_timeout"), 30000);
  }
});

function startOutboundCall() {
  if (call) {
    return;
  }
  if (!task.workerUrl || !hasDialTarget()) {
    failFast("missing_worker_or_dial_target");
    return;
  }
  clearTimer("startWait");
  call = createOutboundCall();
  call.addEventListener(CallEvents.Connected, onConnected);
  call.addEventListener(CallEvents.Failed, () => finalizeAndTerminate("call_failed"));
  call.addEventListener(CallEvents.Disconnected, () => finalizeAndTerminate("call_disconnected"));
  call.addEventListener(CallEvents.PlaybackFinished, onPlaybackFinished);
  call.addEventListener(CallEvents.RecordStarted, (event) => {
    recordingUrl = event.url || recordingUrl;
  });
  call.addEventListener(CallEvents.RecordStopped, (event) => {
    recordingUrl = event.url || recordingUrl;
  });
}

function createOutboundCall() {
  const transport = callTransport();
  if (transport === "sip") {
    Logger.write(`Starting secretary SIP dialogue call ${task.requestId || ""} to ${task.sipUri}`);
    return VoxEngine.callSIP(task.sipUri, sipCallParameters());
  }
  Logger.write(`Starting secretary PSTN dialogue call ${task.requestId || ""} to ${task.destination}`);
  return VoxEngine.callPSTN(task.destination, task.callerId);
}

function onConnected() {
  try {
    call.record({
      hd_audio: true,
      stereo: true,
      transcribe: false,
    });
  } catch (error) {
    Logger.write(`Recording start failed: ${error}`);
  }
  requestNextTurn("");
}

function requestNextTurn(latestText) {
  if (finalized) {
    return;
  }
  stopListening();
  postWorker("/dialogue-turn", dialoguePayload(latestText), (error, decision) => {
    if (error) {
      Logger.write(`dialogue-turn failed: ${error}`);
      decision = fallbackDecision(latestText);
    }
    const phrase = trimForTts(decision.say || "Спасибо, я всё записал и передам Матвею.");
    turns.push({ speaker: "bot", text: phrase });
    if (decision.done) {
      speak(phrase, () => finalizeAndHangup("dialogue_done"));
      return;
    }
    speak(phrase, startListening);
  });
}

function startListening() {
  if (finalized || !call) {
    return;
  }
  stopListening();
  listeningText = "";
  asr = VoxEngine.createASR({
    language: resolveAsrLanguage(task.asrLanguage || DEFAULT_ASR_LANGUAGE),
    interimResults: true,
  });
  asr.addEventListener(ASREvents.Result, onAsrResult);
  asr.addEventListener(ASREvents.InterimResult, onAsrInterimResult);
  asr.addEventListener(ASREvents.CaptureStarted, () => clearNoInputTimer());
  asr.addEventListener(ASREvents.SpeechCaptured, () => schedulePauseTimer());
  asr.addEventListener(ASREvents.ASRError, (event) => {
    Logger.write(`ASR error: ${event.error || "unknown"}`);
    finishHumanTurn();
  });

  call.sendMediaTo(asr);
  noInputTimer = setTimeout(() => finishHumanTurn(), NO_INPUT_TIMEOUT_MS);
  utteranceTimer = setTimeout(() => finishHumanTurn(), MAX_UTTERANCE_MS);
}

function onAsrResult(event) {
  const text = cleanText(event.text || event.result || event.response || "");
  if (text) {
    listeningText = mergeText(listeningText, text);
  }
  schedulePauseTimer();
}

function onAsrInterimResult(event) {
  const text = cleanText(event.text || event.result || event.response || "");
  if (text) {
    Logger.write(`ASR interim: ${text}`);
  }
}

function schedulePauseTimer() {
  clearTimer("pause");
  pauseTimer = setTimeout(() => finishHumanTurn(), TURN_PAUSE_MS);
}

function finishHumanTurn() {
  if (finalized) {
    return;
  }
  const latestText = cleanText(listeningText);
  stopListening();
  if (latestText) {
    turns.push({ speaker: "human", text: latestText });
  }
  turnIndex += 1;
  if (turnIndex >= maxTurns()) {
    finalizeAndHangup("max_turns");
    return;
  }
  requestNextTurn(latestText);
}

function speak(text, callback) {
  pendingAfterPlayback = callback;
  call.say(trimForTts(text), {
    voice: resolveVoice(task.voice || DEFAULT_VOICE),
  });
}

function onPlaybackFinished() {
  const callback = pendingAfterPlayback;
  pendingAfterPlayback = null;
  if (callback) {
    callback();
  }
}

function stopListening() {
  clearNoInputTimer();
  clearTimer("pause");
  clearTimer("utterance");
  if (call && asr) {
    try {
      call.stopMediaTo(asr);
    } catch (error) {
      Logger.write(`stopMediaTo failed: ${error}`);
    }
  }
  if (asr) {
    try {
      asr.stop();
    } catch (error) {
      Logger.write(`ASR stop failed: ${error}`);
    }
  }
  asr = null;
}

function finalizeAndHangup(reason) {
  if (finalized) {
    return;
  }
  finalized = true;
  stopListening();
  postWorker("/dialogue-final", finalPayload(reason), (error) => {
    if (error) {
      Logger.write(`dialogue-final failed: ${error}`);
    }
    try {
      call.hangup();
    } finally {
      VoxEngine.terminate();
    }
  });
}

function finalizeAndTerminate(reason) {
  if (finalized) {
    return;
  }
  finalized = true;
  stopListening();
  postWorker("/dialogue-final", finalPayload(reason), () => VoxEngine.terminate());
}

function dialoguePayload(latestText) {
  return {
    requestId: task.requestId || "",
    targetName: task.targetName || "Организация",
    goal: task.goal || "",
    questions: Array.isArray(task.questions) ? task.questions : [],
    latestText: latestText || "",
    turns,
    turnIndex,
    maxTurns: maxTurns(),
  };
}

function finalPayload(reason) {
  const payload = dialoguePayload("");
  payload.reason = reason;
  payload.recordingUrl = recordingUrl;
  payload.destination = task.destination || "";
  payload.callerId = task.callerId || "";
  payload.transport = callTransport();
  payload.sipUri = task.sipUri || "";
  payload.ownerTelegramChatId = task.ownerTelegramChatId || "";
  return payload;
}

function postWorker(path, payload, callback) {
  const workerUrl = String(task.workerUrl || "").replace(/\/+$/, "");
  const token = VoxEngine.getSecretValue(task.workerSecretName || DEFAULT_WORKER_SECRET);
  if (!workerUrl || !token) {
    callback("missing_worker_url_or_token", null);
    return;
  }
  Net.httpRequest(
    workerUrl + path,
    (result) => {
      if (result.code < 200 || result.code >= 300) {
        callback(`http_${result.code}_${result.error || result.text || ""}`, null);
        return;
      }
      try {
        callback(null, JSON.parse(result.text || "{}"));
      } catch (error) {
        callback(`bad_json_${error}`, null);
      }
    },
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "telegram-secretary-voximplant/0.1",
      },
      postData: JSON.stringify(payload),
      timeout: 25,
      enableSystemLog: false,
      externalLogging: false,
    },
  );
}

function fallbackDecision(latestText) {
  if (!latestText) {
    return {
      say: "Извините, я плохо расслышал. Можете повторить чуть короче?",
      done: false,
    };
  }
  const questions = Array.isArray(task.questions) ? task.questions : [];
  const nextQuestion = questions[Math.min(turnIndex + 1, questions.length - 1)];
  if (nextQuestion && turnIndex < maxTurns() - 1) {
    return {
      say: `Понял. Ещё уточните, пожалуйста: ${ensureQuestionMark(nextQuestion)}`,
      done: false,
    };
  }
  return {
    say: "Спасибо, я всё записал и передам Матвею.",
    done: true,
  };
}

function resolveAsrLanguage(name) {
  const values = {
    "ASRLanguage.RUSSIAN_RU": ASRLanguage.RUSSIAN_RU,
    "ASRLanguage.ENGLISH_US": ASRLanguage.ENGLISH_US,
  };
  return values[name] || ASRLanguage.RUSSIAN_RU;
}

function resolveVoice(name) {
  const values = {
    "VoiceList.Yandex.ru_RU_oksana": VoiceList.Yandex.ru_RU_oksana,
    "VoiceList.Yandex.ru_RU_jane": VoiceList.Yandex.ru_RU_jane,
    "VoiceList.Yandex.ru_RU_ermil": VoiceList.Yandex.ru_RU_ermil,
    "VoiceList.Yandex.ru_RU_zahar": VoiceList.Yandex.ru_RU_zahar,
  };
  return values[name] || VoiceList.Yandex.ru_RU_oksana;
}

function failFast(reason) {
  Logger.write(`Secretary dialogue scenario failed: ${reason}`);
  VoxEngine.terminate();
}

function normalizeTask(value) {
  const normalized = Object.assign({}, value || {});
  normalized.requestId = normalized.requestId || normalized.r || "";
  normalized.workerUrl = normalized.workerUrl || normalized.u || "";
  normalized.workerSecretName = normalized.workerSecretName || normalized.s || DEFAULT_WORKER_SECRET;
  normalized.transport = normalized.transport || normalized.t || "pstn";
  return normalized;
}

function hasDialTarget() {
  if (callTransport() === "sip") {
    return Boolean(task.sipUri);
  }
  return Boolean(task.destination && task.callerId);
}

function callTransport() {
  const value = String(task.transport || "pstn").toLowerCase();
  return value === "sip" ? "sip" : "pstn";
}

function sipCallParameters() {
  const parameters = {};
  const callerId = task.sipCallerId || task.callerId || "";
  if (callerId) {
    parameters.callerid = callerId;
  }
  if (task.sipDisplayName) {
    parameters.displayName = String(task.sipDisplayName);
  }
  if (task.sipRegId) {
    const regId = parseInt(task.sipRegId, 10);
    if (Number.isFinite(regId)) {
      parameters.regId = regId;
    }
  }
  if (task.sipAuthUser) {
    parameters.authUser = String(task.sipAuthUser);
  }
  if (task.sipPasswordSecretName) {
    const password = VoxEngine.getSecretValue(String(task.sipPasswordSecretName));
    if (password) {
      parameters.password = password;
    }
  }
  if (task.sipOutboundProxy) {
    parameters.outProxy = String(task.sipOutboundProxy);
  }
  return parameters;
}

function maxTurns() {
  const value = parseInt(task.maxTurns || 8, 10);
  if (!Number.isFinite(value)) {
    return 8;
  }
  return Math.max(1, Math.min(value, 20));
}

function clearNoInputTimer() {
  clearTimer("noInput");
}

function clearTimer(name) {
  const timer =
    name === "noInput"
      ? noInputTimer
      : name === "pause"
        ? pauseTimer
        : name === "startWait"
          ? startWaitTimer
          : utteranceTimer;
  if (timer) {
    clearTimeout(timer);
  }
  if (name === "noInput") {
    noInputTimer = null;
  } else if (name === "pause") {
    pauseTimer = null;
  } else if (name === "startWait") {
    startWaitTimer = null;
  } else {
    utteranceTimer = null;
  }
}

function mergeText(existing, next) {
  if (!existing) {
    return next;
  }
  if (existing.endsWith(next)) {
    return existing;
  }
  return `${existing} ${next}`;
}

function trimForTts(text) {
  const cleaned = cleanText(text);
  if (cleaned.length <= 900) {
    return cleaned;
  }
  return `${cleaned.slice(0, 897)}...`;
}

function ensureQuestionMark(text) {
  const cleaned = cleanText(text);
  return /[?.!]$/.test(cleaned) ? cleaned : `${cleaned}?`;
}

function cleanText(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}
