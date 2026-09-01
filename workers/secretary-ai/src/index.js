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

  const result = await env.AI.run(env.TEXT_MODEL || "@cf/meta/llama-3.1-8b-instruct", {
    messages,
    temperature: 0.1,
    max_tokens: 700,
  });

  return json({
    text: textFromResult(result),
  });
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

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
    },
  });
}
