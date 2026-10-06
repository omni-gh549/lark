import { errorMessage } from "./api.js";
import { loadSettings } from "./store.svelte.js";

const KEY = "lark.chat";

function restore() {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || "[]");
    if (!Array.isArray(saved)) return [];
    // older saves had no parts
    return saved.map((m) => (m.role === "assistant" && !m.parts ? { ...m, parts: [{ type: "text" }] } : m));
  } catch {
    return [];
  }
}

export const chat = $state({ messages: restore(), busy: false });

function persist() {
  try {
    const keep = chat.messages.filter((m) => !m.error && (m.content || m.parts?.some((p) => p.type === "tool")));
    localStorage.setItem(KEY, JSON.stringify(keep));
  } catch {
    /* storage unavailable: the chat still works, it just isn't remembered */
  }
}

export function newChat() {
  stop();
  chat.messages = [];
  persist();
}

let controller = null;
export function stop() {
  controller?.abort();
}

function addText(reply, text) {
  reply.content += text;
  const last = reply.parts.at(-1);
  if (last?.type === "text") last.text += text;
  else reply.parts.push({ type: "text", text });
}

// a stopped or failed turn leaves no tool spinning
function finishTools(reply) {
  for (const p of reply.parts ?? []) if (p.type === "tool" && p.state === "running") p.state = "error";
}

export async function send(text) {
  if (chat.busy || !text.trim()) return;
  chat.messages.push({ role: "user", content: text.trim() });
  const history = chat.messages
    .filter((m) => !m.error && m.content)
    .map(({ role, content }) => ({ role, content }));
  chat.messages.push({ role: "assistant", content: "", parts: [] });
  const reply = chat.messages[chat.messages.length - 1];
  chat.busy = true;
  controller = new AbortController();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: history }),
      signal: controller.signal,
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      if (res.status === 401) await loadSettings();
      throw new Error(errorMessage(data, res.status));
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let end;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        const line = buffer.slice(0, end).trim();
        buffer = buffer.slice(end + 2);
        if (!line.startsWith("data:")) continue;
        const event = JSON.parse(line.slice(5));
        if (event.text) addText(reply, event.text);
        if (event.tool_start) reply.parts.push({ type: "tool", state: "running", output: "", ...event.tool_start });
        if (event.tool_end) {
          const part = reply.parts.find((p) => p.type === "tool" && p.id === event.tool_end.id);
          if (part) Object.assign(part, { state: event.tool_end.ok ? "ok" : "error", output: event.tool_end.output });
        }
        if (event.error) throw new Error(event.error);
      }
    }
    if (!reply.content && !reply.parts.length) throw new Error("The model sent an empty reply.");
  } catch (e) {
    if (e.name === "AbortError") {
      if (!reply.content && !reply.parts.length) chat.messages.pop();
      else finishTools(reply);
    } else {
      finishTools(reply);
      reply.error = true;
      reply.content = reply.content ? `${reply.content}\n\n${e.message}` : e.message;
    }
  } finally {
    chat.busy = false;
    controller = null;
    persist();
  }
}

