function joinUrl(baseUrl, urlPath) {
  return new URL(urlPath.replace(/^\//, ""), baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`).toString();
}

function buildHeaders(model) {
  const headers = {
    "content-type": "application/json",
    accept: "application/json"
  };

  if (model.authMode === "bearer" && model.apiKey) {
    headers.authorization = `Bearer ${model.apiKey}`;
  }

  return headers;
}

export function extractTextContent(content) {
  if (typeof content === "string") {
    return content;
  }

  if (Array.isArray(content)) {
    return content
      .map((part) => {
        if (typeof part === "string") {
          return part;
        }
        if (part && typeof part.text === "string") {
          return part.text;
        }
        return "";
      })
      .join("");
  }

  return "";
}

export function normalizeToolCalls(message) {
  if (Array.isArray(message?.tool_calls) && message.tool_calls.length > 0) {
    return message.tool_calls.map((toolCall, index) => ({
      id: toolCall.id ?? `toolcall_${index}`,
      name: toolCall.function?.name ?? "",
      arguments: toolCall.function?.arguments ?? "{}"
    }));
  }

  if (message?.function_call?.name) {
    return [
      {
        id: "function_call_0",
        name: message.function_call.name,
        arguments: message.function_call.arguments ?? "{}"
      }
    ];
  }

  return [];
}

export async function createChatCompletion(model, payload, { timeoutMs = 300_000, signal } = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(new Error(`Model request timed out after ${timeoutMs}ms.`)), timeoutMs);

  if (signal) {
    if (signal.aborted) {
      controller.abort(signal.reason);
    } else {
      signal.addEventListener("abort", () => controller.abort(signal.reason), { once: true });
    }
  }

  try {
    const response = await fetch(joinUrl(model.inferenceBaseUrl, "/chat/completions"), {
      method: "POST",
      headers: buildHeaders(model),
      body: JSON.stringify({
        model: model.exposedModel,
        messages: payload.messages,
        tools: payload.tools,
        tool_choice: payload.tools ? "auto" : undefined,
        parallel_tool_calls: false,
        temperature: payload.temperature,
        top_p: payload.top_p,
        stream: false
      }),
      signal: controller.signal
    });

    if (!response.ok) {
      const detail = await response.text();
      throw new Error(`Inference request failed with ${response.status} ${response.statusText}: ${detail}`.trim());
    }

    const json = await response.json();
    const choice = json?.choices?.[0];

    if (!choice?.message) {
      throw new Error("Inference response did not include a message choice.");
    }

    return {
      raw: json,
      message: choice.message,
      text: extractTextContent(choice.message.content ?? "").trim(),
      toolCalls: normalizeToolCalls(choice.message)
    };
  } finally {
    clearTimeout(timeout);
  }
}
