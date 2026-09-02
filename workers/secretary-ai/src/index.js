const MAX_AUDIO_BYTES = 25 * 1024 * 1024;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/health") {
      return json({
        status: "ok",
        service: "secretary-ai",
        text_model: env.TEXT_MODEL,
        asr_model: env.ASR_MODEL,
      });
    }

    if (!authorized(request, env)) {
      return json({ error: "unauthorized" }, 401);
    }

    if (request.method !== "POST") {
      return json({ error: "method_not_allowed" }, 405);
    }

    if (url.pathname === "/asr") {
      return transcribeAudio(request, env);
    }

    if (url.pathname === "/dialogue-turn") {
      return dialogueTurn(request, env);
    }

    if (url.pathname === "/dialogue-final") {
      return dialogueFinal(request, env);
    }

    if (url.pathname === "/" || url.pathname === "/call-analysis") {
      return analyzeCall(request, env);
    }

    return json({ error: "not_found" }, 404);
  },
};

async function transcribeAudio(request, env) {
  const audio = await request.arrayBuffer();
  if (audio.byteLength === 0) {
    return json({ error: "empty_audio" }, 400);
  }
  if (audio.byteLength > MAX_AUDIO_BYTES) {
    return json({ error: "audio_too_large" }, 413);
  }

  const result = await env.AI.run(env.ASR_MODEL || "@cf/openai/whisper", {
    audio: Array.from(new Uint8Array(audio)),
  });
  return json({
    text: result.text || "",
    word_count: result.word_count || 0,
    vtt: result.vtt || "",
  });
}

async function analyzeCall(request, env) {
  const payload = await request.json();
  const messages = Array.isArray(payload.messages)
    ? payload.messages
    : buildCallMessages(payload);

  return json({
    text: await runTextModel(env, messages, 700, 0.1),
  });
}

async function dialogueTurn(request, env) {
  const payload = await request.json();
  const decision = await buildDialogueDecision(payload, env);
  return json(decision);
}

async function dialogueFinal(request, env) {
  const payload = await request.json();
  const transcript = dialogueTranscript(payload);
  const analysisPayload = {
    request_id: payload.requestId || "",
    target_name: payload.targetName || "Организация",
    goal: payload.goal || "",
    questions: Array.isArray(payload.questions) ? payload.questions : [],
    transcript,
    recording_url: payload.recordingUrl || "",
    ended_reason: payload.reason || "",
  };
  let text = "";
  try {
    text = await runTextModel(env, buildCallMessages(analysisPayload), 900, 0.1);
  } catch (error) {
    text = JSON.stringify(fallbackExtraction(analysisPayload, String(error)));
  }
  const extraction = extractJsonObject(text) || fallbackExtraction(analysisPayload, "");
  const telegram = await sendTelegramSummary(env, payload, text, extraction);
  return json({
    text,
    extraction,
    telegram_sent: telegram.sent,
    telegram_error: telegram.error || "",
  });
}

async function buildDialogueDecision(payload, env) {
  const turns = normalizeTurns(payload.turns);
  const latestText = cleanText(payload.latestText || "");
  const questions = cleanList(payload.questions);
  const turnIndex = clampInt(payload.turnIndex, turns.length, 0, 100);
  const maxTurns = clampInt(payload.maxTurns, 8, 1, 20);

  if (turns.length === 0 && !latestText) {
    return {
      say: firstDialogueQuestion(payload, questions),
      done: false,
      missing_items: questions,
      collected_facts: [],
      confidence: 0.2,
    };
  }

  if (turnIndex >= maxTurns) {
    return {
      say: "Спасибо, я всё записал и передам Матвею.",
      done: true,
      missing_items: [],
      collected_facts: [],
      confidence: 0.45,
    };
  }

  try {
    const text = await runTextModel(
      env,
      buildDialogueMessages(payload, turns, latestText, questions, turnIndex, maxTurns),
      500,
      0.2,
    );
    const parsed = extractJsonObject(text);
    const decision = safeDialogueDecision(parsed);
    if (decision.say) {
      return decision;
    }
  } catch (_error) {
    // Fall through to a deterministic prompt so the live call keeps moving.
  }

  return fallbackDialogueDecision(payload, turns, latestText, questions, turnIndex, maxTurns);
}

function buildDialogueMessages(payload, turns, latestText, questions, turnIndex, maxTurns) {
  return [
    {
      role: "system",
      content:
        "Ты русскоязычный телефонный секретарь Матвея. Веди короткий человеческий диалог, чтобы узнать факты по цели звонка. Не бронируй и не обещай оплату, если это явно не попросили. Каждый ответ верни только JSON: {\"say\":\"фраза для звонка\",\"done\":false,\"missing_items\":[],\"collected_facts\":[{\"name\":\"\",\"value\":\"\",\"evidence\":\"\",\"confidence\":0.0}],\"confidence\":0.0}. Задавай один главный вопрос за реплику. Не выдумывай факты.",
    },
    {
      role: "user",
      content: JSON.stringify(
        {
          target_name: payload.targetName || "Организация",
          goal: payload.goal || "",
          questions,
          latest_answer: latestText,
          turn_index: turnIndex,
          max_turns: maxTurns,
          dialogue_so_far: turns,
        },
        null,
        2,
      ),
    },
  ];
}

function buildCallMessages(payload) {
  return [
    {
      role: "system",
      content:
        "Ты разбираешь транскрипт телефонного разговора. Верни только JSON с summary, facts, missing_items, next_actions, confidence. Не придумывай факты.",
    },
    {
      role: "user",
      content: JSON.stringify(payload),
    },
  ];
}

async function runTextModel(env, messages, maxTokens, temperature) {
  const result = await env.AI.run(env.TEXT_MODEL || "@cf/meta/llama-3.1-8b-instruct", {
    messages,
    temperature,
    max_tokens: maxTokens,
  });
  return textFromResult(result);
}

function authorized(request, env) {
  const token = env.SECRETARY_AI_TOKEN || "";
  const header = request.headers.get("Authorization") || "";
  return token && header === `Bearer ${token}`;
}

function textFromResult(result) {
  if (typeof result.response === "string") {
    return result.response;
  }
  if (typeof result.text === "string") {
    return result.text;
  }
  const firstChoice = result.choices && result.choices[0];
  const content = firstChoice && firstChoice.message && firstChoice.message.content;
  if (typeof content === "string" && content) {
    return content;
  }
  return JSON.stringify(result);
}

function firstDialogueQuestion(payload, questions) {
  const targetName = cleanText(payload.targetName || "организацию");
  const goal = cleanText(payload.goal || "");
  const firstQuestion = questions[0] || goal || "подскажите актуальные условия";
  return [
    `Здравствуйте. Это автоматический помощник Матвея, звоню в ${targetName}.`,
    "Разговор записывается, чтобы точно передать ответ.",
    `Подскажите, пожалуйста: ${ensureQuestionMark(firstQuestion)}`,
  ].join(" ");
}

function fallbackDialogueDecision(payload, turns, latestText, questions, turnIndex, maxTurns) {
  if (!latestText && turnIndex > 0) {
    return {
      say: "Извините, я плохо расслышал. Можете повторить чуть короче?",
      done: false,
      missing_items: questions,
      collected_facts: [],
      confidence: 0.2,
    };
  }

  const humanAnswers = turns.filter((turn) => turn.speaker === "human" && turn.text).length;
  const nextQuestion = questions[Math.min(humanAnswers, Math.max(questions.length - 1, 0))];
  if (nextQuestion && turnIndex < maxTurns - 1) {
    return {
      say: `Понял. Ещё уточните, пожалуйста: ${ensureQuestionMark(nextQuestion)}`,
      done: false,
      missing_items: questions.slice(humanAnswers + 1),
      collected_facts: [],
      confidence: 0.3,
    };
  }

  return {
    say: "Спасибо, я всё записал и передам Матвею.",
    done: true,
    missing_items: [],
    collected_facts: [],
    confidence: 0.4,
  };
}

function safeDialogueDecision(value) {
  if (!value || typeof value !== "object") {
    return fallbackDialogueDecision({}, [], "", [], 0, 8);
  }
  return {
    say: cleanText(value.say || "").slice(0, 500),
    done: Boolean(value.done),
    missing_items: cleanList(value.missing_items).slice(0, 10),
    collected_facts: safeFacts(value.collected_facts || value.facts),
    confidence: clampConfidence(value.confidence),
  };
}

function safeFacts(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => {
      if (!item || typeof item !== "object") {
        return null;
      }
      const name = cleanText(item.name || item.field || item.key || "");
      const factValue = cleanText(item.value || "");
      if (!name || !factValue) {
        return null;
      }
      return {
        name,
        value: factValue,
        evidence: cleanText(item.evidence || "").slice(0, 240),
        confidence: clampConfidence(item.confidence),
      };
    })
    .filter(Boolean)
    .slice(0, 12);
}

function dialogueTranscript(payload) {
  return normalizeTurns(payload.turns)
    .map((turn) => {
      const role = turn.speaker === "bot" ? "Робот" : "Собеседник";
      return `${role}: ${turn.text}`;
    })
    .join("\n");
}

async function sendTelegramSummary(env, payload, text, extraction) {
  const botToken = env.TELEGRAM_BOT_TOKEN || "";
  const chatId = payload.ownerTelegramChatId || env.SECRETARY_OWNER_TELEGRAM_ID || "";
  if (!botToken || !chatId) {
    return { sent: false };
  }

  const message = telegramMessage(payload, text, extraction);
  try {
    const response = await fetch(`https://api.telegram.org/bot${botToken}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: chatId,
        text: message,
        disable_web_page_preview: true,
      }),
    });
    if (response.ok) {
      return { sent: true };
    }
    return { sent: false, error: await response.text() };
  } catch (error) {
    return { sent: false, error: String(error) };
  }
}

function telegramMessage(payload, text, extraction) {
  if (!extraction || typeof extraction !== "object") {
    return truncateTelegram(`Разбор звонка ${payload.requestId || ""}\n${text}`);
  }
  const lines = [
    `Разбор звонка ${payload.requestId || ""}`,
    cleanText(extraction.summary || "ИИ не вернул краткое резюме."),
  ];
  const facts = safeFacts(extraction.facts || extraction.fields);
  if (facts.length) {
    lines.push("Факты:");
    for (const fact of facts) {
      lines.push(`- ${fact.name}: ${fact.value}`);
    }
  }
  const missing = cleanList(extraction.missing_items).slice(0, 8);
  if (missing.length) {
    lines.push("Не выяснено:");
    for (const item of missing) {
      lines.push(`- ${item}`);
    }
  }
  const nextActions = cleanList(extraction.next_actions).slice(0, 5);
  if (nextActions.length) {
    lines.push("Дальше:");
    for (const action of nextActions) {
      lines.push(`- ${action}`);
    }
  }
  lines.push(`Уверенность: ${clampConfidence(extraction.confidence).toFixed(2)}`);
  return truncateTelegram(lines.join("\n"));
}

function fallbackExtraction(payload, error) {
  return {
    summary: payload.transcript
      ? `Разговор завершён. Транскрипт получен, но AI-разбор ограничен.${error ? ` Ошибка: ${error}` : ""}`
      : `Разговор завершён без распознанного текста.${error ? ` Ошибка: ${error}` : ""}`,
    facts: [],
    missing_items: cleanList(payload.questions),
    next_actions: ["Проверить запись/транскрипт вручную"],
    confidence: payload.transcript ? 0.2 : 0.05,
  };
}

function extractJsonObject(text) {
  if (!text || typeof text !== "string") {
    return null;
  }
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const candidate = fenced ? fenced[1] : text;
  const start = candidate.indexOf("{");
  const end = candidate.lastIndexOf("}");
  if (start === -1 || end === -1 || end <= start) {
    return null;
  }
  try {
    return JSON.parse(candidate.slice(start, end + 1));
  } catch (_error) {
    return null;
  }
}

function normalizeTurns(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => {
      if (!item || typeof item !== "object") {
        return null;
      }
      const speaker = item.speaker === "bot" ? "bot" : "human";
      const text = cleanText(item.text || "");
      if (!text) {
        return null;
      }
      return { speaker, text };
    })
    .filter(Boolean)
    .slice(-40);
}

function cleanList(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map(cleanText).filter(Boolean);
}

function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function ensureQuestionMark(text) {
  const cleaned = cleanText(text);
  return /[?.!]$/.test(cleaned) ? cleaned : `${cleaned}?`;
}

function clampConfidence(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return 0;
  }
  return Math.max(0, Math.min(1, number));
}

function clampInt(value, fallback, min, max) {
  const number = Number.parseInt(value, 10);
  if (!Number.isFinite(number)) {
    return fallback;
  }
  return Math.max(min, Math.min(max, number));
}

function truncateTelegram(text) {
  if (text.length <= 3900) {
    return text;
  }
  return `${text.slice(0, 3897)}...`;
}

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
    },
  });
}
