<script>
  import { onMount } from "svelte";
  import { api } from "./api.js";
  import { app } from "./store.svelte.js";

  let { onopen } = $props();

  const KINDS = ["fact", "preference", "person", "project", "plan", "event"];
  let facts = $state([]);
  let counts = $state({ facts: 0, messages: 0, chats: 0 });
  let q = $state("");
  let kind = $state("");
  let draft = $state({ text: "", kind: "fact" });
  let editing = $state(null); // { id, text }
  let note = $state({ text: "", kind: "" });
  let confirmClear = $state(false);
  let convoQ = $state("");
  let hits = $state(null);
  let timer;
  let convoTimer;

  const m = $derived(app.settings?.memory);
  const say = (text, k = "") => (note = { text, kind: k });
  const day = (t) => new Date(t * 1000).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });

  async function load() {
    try {
      const r = await api(`/api/memory?q=${encodeURIComponent(q)}&kind=${encodeURIComponent(kind)}`);
      facts = r.facts;
      counts = r.counts;
    } catch (e) {
      say(e.message, "bad");
    }
  }

  const later = () => {
    clearTimeout(timer);
    timer = setTimeout(load, 250);
  };

  async function setting(body) {
    try {
      app.settings = await api("/api/settings", { method: "PUT", body });
      say("");
    } catch (e) {
      say(e.message, "bad");
    }
  }

  async function add() {
    try {
      await api("/api/memory", { method: "POST", body: draft });
      draft = { text: "", kind: draft.kind };
      await load();
    } catch (e) {
      say(e.message, "bad");
    }
  }

  async function save(f, body) {
    try {
      const r = await api(`/api/memory/${f.id}`, { method: "PUT", body });
      Object.assign(f, r);
      say("");
    } catch (e) {
      say(e.message, "bad");
    }
  }

  async function commit(f) {
    const text = editing?.text.trim();
    editing = null;
    if (text && text !== f.text) await save(f, { text });
  }

  async function remove(f) {
    await api(`/api/memory/${f.id}`, { method: "DELETE" });
    facts = facts.filter((x) => x.id !== f.id);
    counts.facts--;
  }

  async function clearAll() {
    try {
      const r = await api("/api/memory?confirm=all", { method: "DELETE" });
      confirmClear = false;
      say(`Forgot ${r.deleted} ${r.deleted === 1 ? "memory" : "memories"}.`, "good");
      await load();
    } catch (e) {
      say(e.message, "bad");
    }
  }

  function searchChats() {
    clearTimeout(convoTimer);
    convoTimer = setTimeout(async () => {
      if (!convoQ.trim()) return (hits = null);
      try {
        hits = (await api(`/api/memory/search?q=${encodeURIComponent(convoQ)}`)).hits;
      } catch (e) {
        say(e.message, "bad");
      }
    }, 250);
  }

  onMount(load);
</script>

<section class="page">
  <h1>Memory</h1>

  <div class="group">
    <h2>How it works</h2>
    <div class="rows">
      <div class="row">
        <div class="row-head">
          <span class="row-title">Remember across chats</span>
          <div class="seg" role="group" aria-label="Remember across chats">
            <button aria-pressed={!m?.use} onclick={() => setting({ memory_use: false })}>Off</button>
            <button aria-pressed={m?.use} onclick={() => setting({ memory_use: true })}>On</button>
          </div>
        </div>
        <p class="meta" style="margin:0">
          Lark gets the notes below and can search every past conversation, so it knows what you mean when you refer back to something.
        </p>
      </div>
      <div class="row">
        <div class="row-head">
          <span class="row-title">Learn from conversations</span>
          <div class="seg" role="group" aria-label="Learn from conversations">
            <button aria-pressed={!m?.learn} onclick={() => setting({ memory_learn: false })}>Off</button>
            <button aria-pressed={m?.learn} onclick={() => setting({ memory_learn: true })}>On</button>
          </div>
        </div>
        <p class="meta" style="margin:0">
          After each reply, Lark adds or corrects notes with one extra model call. Turn it off to keep only what you or Lark save on purpose.
        </p>
      </div>
      <div class="row">
        <label class="row-title" for="mmodel">Memory model (optional)</label>
        <div class="field">
          <input id="mmodel" value={m?.memory_model ?? ""} placeholder="A cheaper model for note-taking, e.g. qwen/qwen3.7-flash" autocomplete="off" spellcheck="false"
            onchange={(e) => setting({ memory_model: e.currentTarget.value })} />
        </div>
        <p class="meta" style="margin:0">
          Used only for the note-taking pass after each reply. Defaults to a cheap one on OpenRouter; clear it to use your chat model.
        </p>
      </div>
      <div class="row">
        <label class="row-title" for="embed">Match by meaning (optional)</label>
        <div class="field">
          <input id="embed" value={m?.embedding_model ?? ""} placeholder="e.g. qwen/qwen3-embedding-8b" autocomplete="off" spellcheck="false"
            onchange={(e) => setting({ embedding_model: e.currentTarget.value })} />
        </div>
        <p class="meta" style="margin:0">
          Needs an embeddings model on your chosen provider. Clear it and notes are found by words only, which still works well.
        </p>
      </div>
    </div>
  </div>

  <div class="group">
    <h2>What Lark remembers · {counts.facts}</h2>
    <div class="rows">
      <div class="row">
        <div class="field">
          <input bind:value={q} oninput={later} placeholder="Search memories" aria-label="Search memories" />
          <select bind:value={kind} onchange={load} aria-label="Kind">
            <option value="">All kinds</option>
            {#each KINDS as k}<option value={k}>{k}</option>{/each}
          </select>
        </div>
      </div>
      {#each facts as f (f.id)}
        <div class="row">
          {#if editing?.id === f.id}
            <form class="field" onsubmit={(e) => { e.preventDefault(); commit(f); }}>
              <input bind:value={editing.text} aria-label="Edit memory" maxlength="300" />
              <button class="btn primary">Save</button>
              <button type="button" class="btn" onclick={() => (editing = null)}>Cancel</button>
            </form>
          {:else}
            <button class="fact" onclick={() => (editing = { id: f.id, text: f.text })} aria-label="Edit: {f.text}">{f.text}</button>
          {/if}
          <div class="row-head">
            <span class="meta">
              {f.kind}{f.subject ? ` · ${f.subject}` : ""} · {day(f.updated)}
              {#if f.source_title}· from <button class="link" onclick={() => onopen(f.source_chat)}>{f.source_title}</button>{/if}
            </span>
            <span class="row-actions" style="margin:0">
              <button class="btn" aria-pressed={f.pinned} onclick={() => save(f, { pinned: !f.pinned })}>{f.pinned ? "Unpin" : "Pin"}</button>
              <button class="btn" onclick={() => remove(f)}>Delete</button>
            </span>
          </div>
        </div>
      {:else}
        <div class="row"><span class="meta">{q || kind ? "Nothing matches." : "Nothing yet. Lark adds notes as you talk, or add one yourself below."}</span></div>
      {/each}
      <div class="row">
        <form class="field" onsubmit={(e) => { e.preventDefault(); add(); }}>
          <input bind:value={draft.text} placeholder="Tell Lark something to remember" aria-label="New memory" maxlength="300" />
          <select bind:value={draft.kind} aria-label="Kind of memory">
            {#each KINDS as k}<option value={k}>{k}</option>{/each}
          </select>
          <button class="btn primary" disabled={!draft.text.trim()}>Add</button>
        </form>
        <p class="status {note.kind}">{note.text}</p>
      </div>
    </div>
    <p class="meta" style="margin:8px 4px 0">Pinned notes and important ones are always in Lark's mind; the rest are found when they come up.</p>
  </div>

  <div class="group">
    <h2>Search conversations · {counts.messages} messages in {counts.chats} chats</h2>
    <div class="rows">
      <div class="row">
        <input class="wide" bind:value={convoQ} oninput={searchChats} placeholder="Search everything you've said to Lark" aria-label="Search conversations" />
        {#if hits}
          {#each hits as h}
            <button class="hit" onclick={() => onopen(h.chat_id)}>
              <span class="meta">{day(h.ts)} · {h.title}{h.contact ? " · Telegram contact" : ""} · {h.role === "user" ? "you" : "Lark"}</span>
              <span>{h.snippet}</span>
            </button>
          {:else}
            <span class="meta">Nothing found.</span>
          {/each}
        {/if}
      </div>
    </div>
  </div>

  <div class="group">
    <h2>Forget</h2>
    <div class="rows">
      <div class="row">
        {#if confirmClear}
          <span class="meta">Delete all {counts.facts} notes and chat summaries? Your chats stay.</span>
          <div class="row-actions">
            <button class="btn danger" onclick={clearAll}>Forget everything</button>
            <button class="btn" onclick={() => (confirmClear = false)}>Cancel</button>
          </div>
        {:else}
          <div class="row-actions" style="margin:0">
            <button class="btn" disabled={!counts.facts} onclick={() => (confirmClear = true)}>Forget everything</button>
          </div>
        {/if}
      </div>
    </div>
  </div>
</section>

<style>
  .fact {
    text-align: left; padding: 2px 0; width: 100%; line-height: 1.45; border-radius: 8px;
  }
  .fact:hover { background: var(--glass); }
  .link { text-decoration: underline; color: inherit; padding: 0; }
  .wide {
    width: 100%; padding: 9px 14px; border-radius: 999px; border: 1px solid var(--line); background: var(--glass-strong); outline: 0;
  }
  .hit { display: flex; flex-direction: column; gap: 2px; text-align: left; padding: 8px 10px; border-radius: 12px; width: 100%; }
  .hit:hover { background: var(--glass); }
  .btn[aria-pressed="true"] { background: var(--accent); color: var(--on-accent); }
</style>
