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

// older assistant messages had no parts
const upgrade = (messages) =>
  messages.map((m) => (m.role === "assistant" && !m.parts ? { ...m, parts: [{ type: "text", text: m.content }] } : m));

export async function init() {
  if (chat.ready) return;
  chat.ready = true;
  try {
    const old = JSON.parse(localStorage.getItem(LEGACY) || "[]");
    const keep = Array.isArray(old) ? old.filter((m) => !m.error && m.content) : [];
    if (keep.length) {
      const id = crypto.randomUUID();
      await api(`/api/chats/${id}`, { method: "PUT", body: { messages: keep } });
      remember(id);
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
  if (id) await openChat(id);
  addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && chat.id && !watching) openChat(chat.id);
  });
}

// ---- following a run that lives on the server ----
let controller = null; // our stream for the open chat
let watching = false;

function detach() {
  controller?.abort();
  controller = null;
  watching = false;
  chat.busy = false;
}

function addText(reply, text) {
  reply.content += text;
  const last = reply.parts.at(-1);
  if (last?.type === "text") last.text += text;
  else reply.parts.push({ type: "text", text });
}

function finishTools(reply) {
  for (const p of reply.parts) if (p.type === "tool" && p.state === "running") p.state = "error";
}

function apply(reply, event) {
  if (event.text) addText(reply, event.text);
  if (event.tool_start) reply.parts.push({ type: "tool", state: "running", output: "", ...event.tool_start });
  if (event.tool_end) {
    const part = reply.parts.find((p) => p.type === "tool" && p.id === event.tool_end.id);
    if (part) {
      Object.assign(part, { state: event.tool_end.ok ? "ok" : "error", output: event.tool_end.output });
      if (event.tool_end.steps) part.steps = event.tool_end.steps;
    }
  }
}

// Attach to the chat's live run: the server replays everything so far, then streams the rest.
async function attach(id, messages) {
  detach();
  const mine = (controller = new AbortController());
  watching = true;
  chat.busy = true;
  messages.push({ role: "assistant", content: "", parts: [] });
  const reply = messages[messages.length - 1];
  let failed = null;
  let idle = false;
  try {
    const res = await fetch(`/api/chats/${id}/events`, { signal: mine.signal });
    if (!res.ok) throw new Error(errorMessage(await res.json().catch(() => ({})), res.status));
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
        if (event.error) failed = event.error;
        if (event.idle) idle = true;
        apply(reply, event);
      }
    }
  } catch (e) {
    if (e.name === "AbortError") return; // we left this chat; the run carries on
    // the connection dropped (sleeping phone, flaky network); the run is fine, so reconnect instead of erroring
    if (controller === mine) {
      detach();
      setTimeout(() => chat.id === id && !watching && openChat(id), 1500);
    }
    return;
  }
  if (controller !== mine) return;
  if (idle) {
    // the run ended just before we attached; the saved chat has the answer
    detach();
    return openChat(id);
  }
  finishTools(reply);
  if (failed) {
    reply.error = true;
    reply.content = reply.content ? `${reply.content}\n\n${failed}` : failed;
  } else if (!reply.content && !reply.parts.length) {
    messages.pop();
  }
  controller = null;
  watching = false;
  chat.busy = false;
  refreshList();
}

export async function openChat(id) {
  detach();
  try {
    const doc = await api(`/api/chats/${id}`);
    chat.id = doc.id;
    chat.messages = upgrade(doc.messages);
    remember(doc.id);
    if (doc.running) attach(doc.id, chat.messages);
    else if (doc.error) chat.messages.push({ role: "assistant", content: doc.error, error: true });
  } catch (e) {
    if (e.status === 404) remember(null);
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
  detach();
  chat.id = null;
  chat.messages = [];
  remember(null);
}

export async function stop() {
  if (chat.id) await api(`/api/chats/${chat.id}/stop`, { method: "POST" }).catch(() => {});
}

export async function send(text) {
  if (chat.busy || !text.trim()) return;
  const id = (chat.id ??= crypto.randomUUID());
  remember(id);
  // always go through chat.messages: pushing to a local copy of the array wouldn't update the page
  chat.messages = chat.messages.filter((m) => !m.error);
  chat.messages.push({ role: "user", content: text.trim() });
  chat.busy = true;
  try {
    await api(`/api/chats/${id}/send`, { method: "POST", body: { content: text.trim() } });
  } catch (e) {
    chat.busy = false;
    if (e.status === 401) await loadSettings();
    chat.messages.push({ role: "assistant", content: e.message, error: true });
    return;
  }
  refreshList();
  await attach(id, chat.messages);
}
