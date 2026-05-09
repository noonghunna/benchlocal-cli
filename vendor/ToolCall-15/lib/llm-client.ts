import { SYSTEM_PROMPT, UNIVERSAL_TOOLS } from "@/lib/benchmark";
import type { ModelConfig } from "@/lib/models";

export type ModelMessage = {
  role: "system" | "user" | "assistant" | "tool";
  content: string;
  reasoning_content?: string;
  tool_calls?: ProviderToolCall[];
  tool_call_id?: string;
};

export type ProviderToolCall = {
  id: string;
  type: "function";
  function: {
    name: string;
    arguments: string;
  };
};

export type AssistantResponse = {
  content: string;
  toolCalls: ProviderToolCall[];
  reasoning?: string;
  reasoningContent?: string;
};

export type GenerationParams = {
  temperature?: number;
  top_p?: number;
  top_k?: number;
  min_p?: number;
  repetition_penalty?: number;
  tools_format?: "default" | "lfm";
  request_timeout_seconds?: number;
  signal?: AbortSignal;
};

const DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS = 30;

type ChatResponse = {
  choices?: Array<{
    message?: {
      content?: string | Array<{ type?: string; text?: string }>;
      reasoning_content?: string;
      reasoning?: string;
      tool_calls?: Array<{
        id?: string;
        type?: string;
        function?: {
          name?: string;
          arguments?: string | Record<string, unknown>;
        };
      }>;
    };
  }>;
  error?: {
    message?: string;
  };
};

type ProviderMessage = NonNullable<NonNullable<ChatResponse["choices"]>[number]["message"]>;
type ProviderContent = ProviderMessage["content"];

function normalizeBaseUrl(baseUrl: string): string {
  return baseUrl.endsWith("/") ? baseUrl.slice(0, -1) : baseUrl;
}

function normalizeContent(content: ProviderContent): string {
  if (typeof content === "string") {
    return content;
  }

  if (Array.isArray(content)) {
    return content
      .map((part) => (part?.type === "text" ? part.text ?? "" : ""))
      .join("")
      .trim();
  }

  return "";
}

function normalizeToolCalls(message: ProviderMessage): ProviderToolCall[] {
  return (
    message?.tool_calls?.map((call: NonNullable<ProviderMessage["tool_calls"]>[number], index: number) => ({
      id: call.id ?? `tool_call_${index + 1}`,
      type: "function",
      function: {
        name: call.function?.name ?? "unknown_tool",
        arguments:
          typeof call.function?.arguments === "string"
            ? call.function.arguments
            : JSON.stringify(call.function?.arguments ?? {})
      }
    })) ?? []
  );
}

// --- LFM helpers ---

function toPythonValue(val: unknown): string {
  if (typeof val === "string") return `"${val.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
  if (typeof val === "boolean") return val ? "True" : "False";
  if (val === null || val === undefined) return "None";
  if (typeof val === "number") return String(val);
  return JSON.stringify(val);
}

function serializeToolCallsToLfm(toolCalls: ProviderToolCall[]): string {
  const callsStr = toolCalls.map((tc) => {
    let args: Record<string, unknown> = {};
    try {
      const parsed = JSON.parse(tc.function.arguments || "{}");
      if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
        args = parsed as Record<string, unknown>;
      }
    } catch { /* ignore */ }
    const argsStr = Object.entries(args).map(([k, v]) => `${k}=${toPythonValue(v)}`).join(", ");
    return `${tc.function.name}(${argsStr})`;
  }).join(", ");
  return `<|tool_call_start|>[${callsStr}]<|tool_call_end|>`;
}

function buildLfmMessages(messages: ModelMessage[]): ModelMessage[] {
  const toolList = UNIVERSAL_TOOLS.map((t) => t.function);
  const injection = `\n\nList of tools: ${JSON.stringify(toolList)}\n`;

  return messages.map((msg): ModelMessage => {
    if (msg.role === "system") {
      return { ...msg, content: msg.content + injection };
    }

    // Re-serialize assistant tool calls back into LFM format for multi-turn history.
    // The LFM model is not trained on the OpenAI structured tool_calls field.
    if (msg.role === "assistant" && msg.tool_calls && msg.tool_calls.length > 0) {
      const lfmBlock = serializeToolCallsToLfm(msg.tool_calls);
      return {
        role: "assistant",
        content: msg.content ? `${msg.content}\n${lfmBlock}` : lfmBlock
      };
    }

    return msg;
  });
}

// --- Pythonic call parser ---

function splitTopLevel(input: string, delimiter: string): string[] {
  const parts: string[] = [];
  let current = "";
  let inString = false;
  let stringChar = "";
  let parenDepth = 0;
  let bracketDepth = 0;
  let braceDepth = 0;

  for (let index = 0; index < input.length; index += 1) {
    const ch = input[index];

    if (inString) {
      if (ch === "\\" && index + 1 < input.length) {
        current += ch + input[index + 1];
        index += 1;
      } else if (ch === stringChar) {
        inString = false;
        current += ch;
      } else {
        current += ch;
      }
      continue;
    }

    if (ch === '"' || ch === "'") {
      inString = true; stringChar = ch; current += ch;
      continue;
    }

    if (ch === "(") {
      parenDepth += 1;
    } else if (ch === ")" && parenDepth > 0) {
      parenDepth -= 1;
    } else if (ch === "[") {
      bracketDepth += 1;
    } else if (ch === "]" && bracketDepth > 0) {
      bracketDepth -= 1;
    } else if (ch === "{") {
      braceDepth += 1;
    } else if (ch === "}" && braceDepth > 0) {
      braceDepth -= 1;
    } else if (ch === delimiter && parenDepth === 0 && bracketDepth === 0 && braceDepth === 0) {
      const part = current.trim();
      if (part) {
        parts.push(part);
      }
      current = "";
      continue;
    }

    current += ch;
  }

  if (current.trim()) {
    parts.push(current.trim());
  }

  return parts;
}

function findTopLevelChar(input: string, target: string): number {
  let inString = false;
  let stringChar = "";
  let parenDepth = 0;
  let bracketDepth = 0;
  let braceDepth = 0;

  for (let index = 0; index < input.length; index += 1) {
    const ch = input[index];

    if (inString) {
      if (ch === "\\" && index + 1 < input.length) {
        index += 1;
      } else if (ch === stringChar) {
        inString = false;
      }
      continue;
    }

    if (ch === '"' || ch === "'") {
      inString = true;
      stringChar = ch;
      continue;
    }

    if (ch === "(") {
      parenDepth += 1;
      continue;
    }

    if (ch === ")" && parenDepth > 0) {
      parenDepth -= 1;
      continue;
    }

    if (ch === "[") {
      bracketDepth += 1;
      continue;
    }

    if (ch === "]" && bracketDepth > 0) {
      bracketDepth -= 1;
      continue;
    }

    if (ch === "{") {
      braceDepth += 1;
      continue;
    }

    if (ch === "}" && braceDepth > 0) {
      braceDepth -= 1;
      continue;
    }

    if (ch === target && parenDepth === 0 && bracketDepth === 0 && braceDepth === 0) {
      return index;
    }
  }

  return -1;
}

function parsePythonValue(raw: string): unknown {
  const t = raw.trim();
  if ((t.startsWith('"') && t.endsWith('"')) || (t.startsWith("'") && t.endsWith("'"))) {
    return t.slice(1, -1).replace(/\\n/g, "\n").replace(/\\t/g, "\t").replace(/\\"/g, '"').replace(/\\'/g, "'").replace(/\\\\/g, "\\");
  }
  if (t.startsWith("[") && t.endsWith("]")) {
    const inner = t.slice(1, -1).trim();
    return inner ? splitTopLevel(inner, ",").map((part) => parsePythonValue(part)) : [];
  }
  if (t.startsWith("{") && t.endsWith("}")) {
    const inner = t.slice(1, -1).trim();
    const objectValue: Record<string, unknown> = {};

    if (!inner) {
      return objectValue;
    }

    for (const entry of splitTopLevel(inner, ",")) {
      const separatorIndex = findTopLevelChar(entry, ":");

      if (separatorIndex === -1) {
        continue;
      }

      const keyValue = parsePythonValue(entry.slice(0, separatorIndex).trim());
      const objectKey = typeof keyValue === "string" ? keyValue : String(keyValue);
      objectValue[objectKey] = parsePythonValue(entry.slice(separatorIndex + 1).trim());
    }

    return objectValue;
  }
  if (t === "True") return true;
  if (t === "False") return false;
  if (t === "None") return null;
  const num = Number(t);
  if (!isNaN(num) && t !== "") return num;
  return t;
}

function parsePythonicCalls(text: string): Array<{ name: string; args: Record<string, unknown> }> {
  const calls: Array<{ name: string; args: Record<string, unknown> }> = [];
  let i = 0;

  while (i < text.length) {
    while (i < text.length && /[\s,]/.test(text[i])) i++;
    if (i >= text.length) break;

    const nameStart = i;
    while (i < text.length && /\w/.test(text[i])) i++;
    const name = text.slice(nameStart, i);
    if (!name) { i++; continue; }

    while (i < text.length && text[i] === " ") i++;
    if (i >= text.length || text[i] !== "(") continue;
    i++;

    const argsStart = i;
    let depth = 1;
    let inString = false;
    let stringChar = "";
    while (i < text.length && depth > 0) {
      const ch = text[i];
      if (inString) {
        if (ch === "\\" && i + 1 < text.length) { i += 2; continue; }
        if (ch === stringChar) inString = false;
      } else if (ch === '"' || ch === "'") {
        inString = true; stringChar = ch;
      } else if (ch === "(") {
        depth++;
      } else if (ch === ")") {
        depth--;
        if (depth === 0) break;
      }
      i++;
    }
    const argsStr = text.slice(argsStart, i);
    i++;

    const args: Record<string, unknown> = {};
    for (const part of splitTopLevel(argsStr, ",")) {
      const eq = findTopLevelChar(part, "=");
      if (eq === -1) continue;
      const key = part.slice(0, eq).trim();
      if (key) args[key] = parsePythonValue(part.slice(eq + 1).trim());
    }
    calls.push({ name, args });
  }
  return calls;
}

// ---

function parseLfmResponse(content: string): { content: string; toolCalls: ProviderToolCall[] } {
  const toolCalls: ProviderToolCall[] = [];
  let callIndex = 0;
  const blocks = content.match(/<\|tool_call_start\|>([\s\S]*?)<\|tool_call_end\|>/g) ?? [];

  for (const block of blocks) {
    const inner = block.replace(/<\|tool_call_start\|>/, "").replace(/<\|tool_call_end\|>/, "").trim();
    try {
      const parsed = JSON.parse(inner) as Array<{ name?: string; arguments?: unknown }>;
      for (const call of Array.isArray(parsed) ? parsed : [parsed]) {
        if (call?.name) {
          toolCalls.push({
            id: `tool_call_${++callIndex}`,
            type: "function",
            function: {
              name: call.name,
              arguments: typeof call.arguments === "string" ? call.arguments : JSON.stringify(call.arguments ?? {})
            }
          });
        }
      }
    } catch {
      // ignore malformed blocks
    }
  }

  if (toolCalls.length > 0) {
    return {
      content: content.replace(/<\|tool_call_start\|>[\s\S]*?<\|tool_call_end\|>/g, "").trim(),
      toolCalls
    };
  }

  // Fallback: server stripped the special tokens, leaving bare content.
  // Try JSON first, then pythonic format.
  const trimmed = content.trim();
  if (trimmed.startsWith("[")) {
    try {
      const parsed = JSON.parse(trimmed) as Array<{ name?: string; arguments?: unknown }>;
      for (const call of Array.isArray(parsed) ? parsed : []) {
        if (call?.name) {
          toolCalls.push({
            id: `tool_call_${++callIndex}`,
            type: "function",
            function: {
              name: call.name,
              arguments: typeof call.arguments === "string" ? call.arguments : JSON.stringify(call.arguments ?? {})
            }
          });
        }
      }
      if (toolCalls.length > 0) return { content: "", toolCalls };
    } catch {
      // not JSON — fall through to pythonic
    }

    const inner = trimmed.slice(1, -1).trim();
    for (const call of parsePythonicCalls(inner)) {
      toolCalls.push({
        id: `tool_call_${++callIndex}`,
        type: "function",
        function: { name: call.name, arguments: JSON.stringify(call.args) }
      });
    }
    if (toolCalls.length > 0) return { content: "", toolCalls };
  }

  return { content, toolCalls };
}

function isTimeoutError(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false;
  }

  return error.name === "TimeoutError" || error.name === "AbortError";
}

function isAbortError(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false;
  }

  return error.name === "AbortError";
}

function resolveRequestTimeoutMs(params?: GenerationParams): number {
  if (params?.request_timeout_seconds !== undefined) {
    const requested = Math.trunc(params.request_timeout_seconds);
    if (Number.isFinite(requested) && requested > 0) {
      return requested * 1000;
    }
  }

  const rawTimeout = process.env.MODEL_REQUEST_TIMEOUT_SECONDS?.trim();

  if (!rawTimeout) {
    return DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS * 1000;
  }

  const parsed = Number.parseInt(rawTimeout, 10);

  if (!Number.isFinite(parsed) || parsed <= 0) {
    return DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS * 1000;
  }

  return parsed * 1000;
}

export async function callModel(model: ModelConfig, messages: ModelMessage[], params?: GenerationParams): Promise<AssistantResponse> {
  const baseUrl = normalizeBaseUrl(model.baseUrl);
  const requestTimeoutMs = resolveRequestTimeoutMs(params);
  const headers: Record<string, string> = {
    "Content-Type": "application/json"
  };

  if (model.apiKey) {
    headers.Authorization = `Bearer ${model.apiKey}`;
  }

  const useLfmFormat = (params?.tools_format ?? "default") === "lfm";
  const body: Record<string, unknown> = {
    model: model.model,
    messages: useLfmFormat ? buildLfmMessages(messages) : messages,
    ...(useLfmFormat ? {} : { parallel_tool_calls: true, tool_choice: "auto", tools: UNIVERSAL_TOOLS })
  };

  if (params?.temperature !== undefined) {
    body.temperature = params.temperature;
  }

  if (params?.top_p !== undefined) {
    body.top_p = params.top_p;
  }

  if (params?.top_k !== undefined) {
    body.top_k = params.top_k;
  }

  if (params?.min_p !== undefined) {
    body.min_p = params.min_p;
  }

  if (params?.repetition_penalty !== undefined) {
    body.repetition_penalty = params.repetition_penalty;
  }

  const timeoutController = new AbortController();
  const timeoutHandle = setTimeout(() => timeoutController.abort(), requestTimeoutMs);
  const parentSignal = params?.signal;
  const abortController = new AbortController();

  const abortFromParent = () => abortController.abort(parentSignal?.reason);
  const abortFromTimeout = () => abortController.abort(new DOMException("Request timed out.", "TimeoutError"));

  parentSignal?.addEventListener("abort", abortFromParent, { once: true });
  timeoutController.signal.addEventListener("abort", abortFromTimeout, { once: true });

  let response: Response;
  let payload: ChatResponse;

  try {
    response = await fetch(`${baseUrl}/chat/completions`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal: abortController.signal
    });
    payload = (await response.json()) as ChatResponse;
  } catch (error) {
    if (isAbortError(error) && parentSignal?.aborted) {
      throw new Error("Request aborted.");
    }

    if (isTimeoutError(error)) {
      throw new Error(`Request timed out after ${requestTimeoutMs / 1000}s.`);
    }

    throw error;
  } finally {
    clearTimeout(timeoutHandle);
    parentSignal?.removeEventListener("abort", abortFromParent);
  }

  if (!response.ok) {
    throw new Error(payload.error?.message || `Provider request failed with ${response.status}.`);
  }

  const message = payload.choices?.[0]?.message;

  if (!message) {
    throw new Error("Provider returned no assistant message.");
  }

  if (useLfmFormat) {
    const parsed = parseLfmResponse(normalizeContent(message.content));
    // The inference server intercepts <|tool_call_start|> special tokens and moves them
    // to message.tool_calls before we ever see them in content — check there as fallback.
    if (parsed.toolCalls.length === 0 && (message.tool_calls?.length ?? 0) > 0) {
      return { content: parsed.content, toolCalls: normalizeToolCalls(message) };
    }
    return { content: parsed.content, toolCalls: parsed.toolCalls };
  }

  return {
    content: normalizeContent(message.content),
    toolCalls: normalizeToolCalls(message),
    reasoning: message.reasoning_content ?? message.reasoning,
    reasoningContent: message.reasoning_content
  };
}

export function createInitialMessages(userMessage: string): ModelMessage[] {
  return [
    { role: "system", content: `${SYSTEM_PROMPT}\n\nBenchmark context: today is 2026-03-20 (Friday). Use this date for any relative time request.` },
    { role: "user", content: userMessage }
  ];
}
