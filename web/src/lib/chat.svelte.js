import { api, errorMessage } from "./api.js";
import { loadSettings } from "./store.svelte.js";

const LEGACY = "lark.chat"; // chats used to live in the browser
const CURRENT = "lark.chat.id";

export const chat = $state({ id: null, messages: [], busy: false, list: [], ready: false });

const remember = (id) => {
  try {
    id ? localStorage.setItem(CURRENT, id) : localStorage.removeItem(CURRENT);
  } catch {
    /* fine: the newest chat just won't reopen by itself */
  }
};

export async function refreshList() {
  try {
    chat.list = (await api("/api/chats")).chats;
  } catch {
    /* leave the old list */
  }
}

async function save(id, messages) {
  const keep = messages.filter((m) => !m.error && (m.content || m.parts?.some((p) => p.type === "tool")));
  if (!keep.length) return;
  try {
    await api(`/api/chats/${id}`, { method: "PUT", body: { messages: keep } });
    refreshList();
  } catch {
    /* the chat is still on screen; the next turn tries again */
  }
}

// older assistant messages had no parts
const upgrade = (messages) =>
  messages.map((m) => (m.role === "assistant" && !m.parts ? { ...m, parts: [{ type: "text", text: m.content }] } : m));

export async function init() {
  if (chat.ready) return;
  chat.ready = true;
  try {
    const old = JSON.parse(localStorage.getItem(LEGACY) || "[]");
    if (Array.isArray(old) && old.length) {
      chat.id = crypto.randomUUID();
      chat.messages = upgrade(old);
      remember(chat.id);
      await save(chat.id, chat.messages);
    }
    localStorage.removeItem(LEGACY);
  } catch {
    /* nothing to migrate */
  }
  await refreshList();
  let id = null;
  try {
    id = localStorage.getItem(CURRENT);
  } catch {
    /* ignore */
  }
  if (id && !chat.messages.length) await openChat(id);
}

export async function openChat(id) {
  stop();
  try {
    const doc = await api(`/api/chats/${id}`);
    chat.id = doc.id;
    chat.messages = upgrade(doc.messages);
    remember(doc.id);
  } catch {
    remember(null);
  }
}

export async function deleteChat(id) {
  try {
    await api(`/api/chats/${id}`, { method: "DELETE" });
  } catch {
    return;
  }
  if (chat.id === id) newChat();
  refreshList();
}

export function newChat() {
  stop();
  chat.id = null;
  chat.messages = [];
  remember(null);
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
  const id = (chat.id ??= crypto.randomUUID());
  remember(id);
  const messages = chat.messages;
  messages.push({ role: "user", content: text.trim() });
  const history = messages
    .filter((m) => !m.error && m.content)
    .map(({ role, content }) => ({ role, content }));
  messages.push({ role: "assistant", content: "", parts: [] });
  const reply = messages[messages.length - 1];
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
      if (!reply.content && !reply.parts.length) messages.pop();
      else finishTools(reply);
    } else {
      finishTools(reply);
      reply.error = true;
      reply.content = reply.content ? `${reply.content}\n\n${e.message}` : e.message;
    }
  } finally {
    chat.busy = false;
    controller = null;
    save(id, messages);
  }
}

