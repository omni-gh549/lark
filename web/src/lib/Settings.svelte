<script>
  import { api } from "./api.js";
  import { app } from "./store.svelte.js";
  import { getBackdrop, setBackdrop } from "./backdrop.js";

  const s = $derived(app.settings);
  let backdrop = $state(getBackdrop());
  const chooseBackdrop = (v) => setBackdrop((backdrop = v));
  let keyInput = $state({});
  let note = $state({}); // key row messages, per provider: { text, kind }
  let modelNote = $state({}); // model row messages, per provider
  let models = $state({}); // per-provider list for the suggestions
  let busy = $state({});

  const say = (name, text, kind = "") => (note[name] = { text, kind });
  const sayModel = (name, text, kind = "") => (modelNote[name] = { text, kind });

  async function run(name, fn) {
    busy[name] = true;
    try {
      await fn();
    } catch (e) {
      say(name, e.message, "bad");
    } finally {
      busy[name] = false;
    }
  }

  async function choose(provider) {
    app.settings = await api("/api/settings", { method: "PUT", body: { provider } });
  }

  async function saveModel(name, value) {
    if (value.trim() === s.models[name]) return;
    app.settings = await api("/api/settings", { method: "PUT", body: { models: { [name]: value } } });
  }

  const saveKey = (name) =>
    run(name, async () => {
      app.settings = await api(`/api/keys/${name}`, { method: "PUT", body: { key: keyInput[name] } });
      keyInput[name] = "";
      say(name, "Saved.", "good");
    });

  const testKey = (name) =>
    run(name, async () => {
      say(name, "Checking…");
      const r = await api(`/api/keys/${name}/test`, { method: "POST" });
      say(name, r.detail, "good");
    });

  const removeKey = (name) =>
    run(name, async () => {
      app.settings = await api(`/api/keys/${name}`, { method: "DELETE" });
      say(name, "Removed.");
    });

  async function chooseSearch(search) {
    app.settings = await api("/api/settings", { method: "PUT", body: { search } });
  }

  let tg = $state(null); // { configured, bot, owner, contacts, error }
  let tgNote = $state({ text: "", kind: "" });
  let tgLink = $state("");
  let tgCommand = $state("");
  let invite = $state({ name: "", policy: "draft", scope: "" });
  let inviteUrl = $state("");
  let inviteCommand = $state("");
  const policyLabels = { draft: "Ask me first", auto: "Reply on its own", relay: "Just forward", blocked: "Blocked" };

  const tgSay = (text, kind = "") => (tgNote = { text, kind });

  async function loadTelegram() {
    try {
      tg = await api("/api/telegram");
    } catch (e) {
      tg = null;
    }
  }

  async function tgDo(fn) {
    try {
      await fn();
      tgNote = { text: "", kind: "" };
    } catch (e) {
      tgSay(e.message, "bad");
    }
  }

  const makeLink = () => tgDo(async () => { const r = await api("/api/telegram/link", { method: "POST" }); tgLink = r.url; tgCommand = r.command; });
  const unlink = () => tgDo(async () => { await api("/api/telegram/owner", { method: "DELETE" }); tgLink = ""; await loadTelegram(); });
  const makeInvite = () =>
    tgDo(async () => {
      const r = await api("/api/telegram/invites", { method: "POST", body: invite });
      inviteUrl = r.url;
      inviteCommand = r.command;
      invite = { name: "", policy: invite.policy, scope: "" };
    });
  const editContact = (c, body) =>
    tgDo(async () => {
      Object.assign(c, body);
      await api(`/api/telegram/contacts/${c.id}`, { method: "PUT", body });
    });
  const removeContact = (c) =>
    tgDo(async () => {
      await api(`/api/telegram/contacts/${c.id}`, { method: "DELETE" });
      await loadTelegram();
    });

  $effect(() => {
    // refresh when the bot token is added or removed
    s?.telegram?.key_hint;
    loadTelegram();
  });

  let browserNote = $state({ text: "", kind: "" });

  async function setCookies(on) {
    try {
      app.settings = await api("/api/settings", { method: "PUT", body: { browser_cookies: on } });
      browserNote = { text: on ? "Cookies and logins will be kept." : "Saved cookies deleted.", kind: "good" };
    } catch (e) {
      browserNote = { text: e.message, kind: "bad" };
    }
  }

  async function setTitleModel(value) {
    try {
      app.settings = await api("/api/settings", { method: "PUT", body: { title_model: value } });
    } catch {
      /* the field keeps what was typed; try again */
    }
  }

  async function setUi(on) {
    try {
      app.settings = await api("/api/settings", { method: "PUT", body: { generative_ui: on } });
    } catch {
      /* the switch just stays where it was */
    }
  }

  async function clearBrowser() {
    try {
      await api("/api/browser/clear", { method: "POST" });
      browserNote = { text: "Browser data cleared.", kind: "good" };
    } catch (e) {
      browserNote = { text: e.message, kind: "bad" };
    }
  }

  let sandbox = $state(null); // { configured, ok, home, disk_free_mb, error }
  let sandboxNote = $state({ text: "", kind: "" });
  let confirmWipe = $state(false);
  let confirmReset = $state(false);
  let resetting = $state(false);
  let wiping = $state(false);

  async function loadSandbox() {
    try {
      sandbox = await api("/api/sandbox");
    } catch (e) {
      sandbox = { configured: true, ok: false, error: e.message };
    }
  }

  async function wipe() {
    wiping = true;
    try {
      await api("/api/sandbox/wipe", { method: "POST" });
      sandboxNote = { text: "Sandbox files cleared.", kind: "good" };
    } catch (e) {
      sandboxNote = { text: e.message, kind: "bad" };
    } finally {
      wiping = false;
      confirmWipe = false;
      loadSandbox();
    }
  }

  async function resetAll() {
    resetting = true;
    confirmReset = false;
    sandboxNote = { text: "Resetting the sandbox…", kind: "" };
    try {
      await api("/api/sandbox/reset", { method: "POST" });
      // the server rebuilds the container; wait until it answers again
      for (let i = 0; i < 40; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        await loadSandbox();
        if (sandbox?.ok && i > 0) break;
      }
      sandboxNote = sandbox?.ok
        ? { text: "Sandbox reset to a fresh install.", kind: "good" }
        : { text: "The reset is taking longer than expected. Check the server.", kind: "bad" };
    } catch (e) {
      sandboxNote = { text: e.message, kind: "bad" };
    } finally {
      resetting = false;
    }
  }

  $effect(() => {
    if (s?.sandbox) loadSandbox();
  });

  async function loadModels(name) {
    busy[name] = true;
    sayModel(name, "Loading models…");
    try {
      models[name] = (await api(`/api/providers/${name}/models`)).models;
      sayModel(name, `${models[name].length} models available.`);
    } catch (e) {
      sayModel(name, e.message, "bad");
    } finally {
      busy[name] = false;
    }
  }
</script>

{#snippet keyRow(name, p)}
  <div class="row">
    <div class="row-head">
      <span class="row-title">{p.label}</span>
      <span class="meta">
        {#if p.key_hint}Saved, ends in {p.key_hint}{:else}Not set{/if}
        · <a href={p.keys_url} target="_blank" rel="noopener noreferrer">Get a key</a>
      </span>
    </div>
    <form class="field" onsubmit={(e) => { e.preventDefault(); saveKey(name); }}>
      <input
        type="password"
        bind:value={keyInput[name]}
        placeholder={p.key_hint ? "Paste a new key to replace it" : "Paste key"}
        aria-label="{p.label} API key"
        autocomplete="off"
      />
      <button class="btn primary" disabled={busy[name] || !(keyInput[name] ?? "").trim()}>Save</button>
      <button type="button" class="btn" disabled={busy[name] || !p.key_hint} onclick={() => testKey(name)}>Test</button>
      <button type="button" class="btn" disabled={busy[name] || !p.key_hint} onclick={() => removeKey(name)}>Remove</button>
    </form>
    <p class="status {note[name]?.kind ?? ''}">{note[name]?.text ?? ""}</p>
  </div>
{/snippet}

<section class="page">
  <h1>Settings</h1>

  {#if s}
    <div class="group">
      <h2>Model</h2>
      <div class="rows">
        <div class="row">
          <div class="row-head">
            <span class="row-title">Provider</span>
            <div class="seg" role="group" aria-label="Provider">
              {#each Object.entries(s.providers) as [name, p]}
                <button aria-pressed={s.provider === name} onclick={() => choose(name)}>{p.label}</button>
              {/each}
            </div>
          </div>
        </div>
        <div class="row">
          <label class="row-title" for="model">Model for {s.providers[s.provider].label}</label>
          <div class="field">
            <input
              id="model"
              list="model-list"
              value={s.models[s.provider]}
              placeholder="provider/model-name"
              autocomplete="off"
              spellcheck="false"
              onchange={(e) => saveModel(s.provider, e.currentTarget.value).catch((err) => sayModel(s.provider, err.message, "bad"))}
            />
            <button class="btn" disabled={busy[s.provider]} onclick={() => loadModels(s.provider)}>Load models</button>
          </div>
          <datalist id="model-list">
            {#each models[s.provider] ?? [] as id}<option value={id}></option>{/each}
          </datalist>
          <p class="status {modelNote[s.provider]?.kind ?? ''}">{modelNote[s.provider]?.text ?? ""}</p>
        </div>
        <div class="row">
          <label class="row-title" for="tmodel">Chat titles model (optional)</label>
          <div class="field">
            <input id="tmodel" value={s.title_model ?? ""} placeholder="A cheap model that names your chats, e.g. qwen/qwen3.7-flash" autocomplete="off" spellcheck="false"
              onchange={(e) => setTitleModel(e.currentTarget.value)} />
          </div>
          <p class="meta" style="margin:0">
            Names each chat after its first reply so your history is easy to scan. Defaults to a cheap one on OpenRouter; clear it to use your chat model.
          </p>
        </div>
      </div>
    </div>

    {#if s.search_providers}
    <div class="group">
      <h2>Web search</h2>
      <div class="rows">
        <div class="row">
          <div class="row-head">
            <span class="row-title">Search provider</span>
            <div class="seg" role="group" aria-label="Search provider">
              {#each Object.entries(s.search_providers) as [name, p]}
                <button aria-pressed={s.search === name} onclick={() => chooseSearch(name)}>{p.label}</button>
              {/each}
            </div>
          </div>
          <p class="meta" style="margin:0">
            {s.search_providers[s.search].key_hint ? "Lark can search the web." : "Add a key below to let Lark search the web."}
          </p>
        </div>
        {@render keyRow(s.search, s.search_providers[s.search])}
      </div>
    </div>

    <div class="group">
      <h2>Sandbox</h2>
      <div class="rows">
        <div class="row">
          {#if !s.sandbox}
            <span class="row-title">Not set up</span>
            <p class="meta" style="margin:0">
              A sandbox gives Lark a private Linux machine to run commands and keep files. See the README to set one up on your server.
            </p>
          {:else if !sandbox}
            <span class="row-title">Checking…</span>
          {:else}
            <div class="row-head">
              <span class="row-title">{sandbox.ok ? "Running" : "Not reachable"}</span>
              {#if sandbox.ok}<span class="meta">{sandbox.disk_free_mb} MB free</span>{/if}
            </div>
            {#if sandbox.error}<p class="status bad">{sandbox.error}</p>{/if}
            <div class="row-actions">
              {#if confirmWipe}
                <span class="meta">Delete everything in {sandbox.home ?? "the sandbox"}?</span>
                <button class="btn danger" disabled={wiping} onclick={wipe}>Delete files</button>
                <button class="btn" onclick={() => (confirmWipe = false)}>Cancel</button>
              {:else if confirmReset}
                <span class="meta">Delete everything in the sandbox, including your files and installed programs?</span>
                <button class="btn danger" disabled={resetting} onclick={resetAll}>Reset sandbox</button>
                <button class="btn" onclick={() => (confirmReset = false)}>Cancel</button>
              {:else}
                <button class="btn" disabled={!sandbox.ok || resetting} onclick={() => ((confirmWipe = true), (sandboxNote = { text: "", kind: "" }))}>Clear files</button>
                {#if sandbox.reset_available}
                  <button class="btn" disabled={resetting} onclick={() => ((confirmReset = true), (sandboxNote = { text: "", kind: "" }))}>Reset everything</button>
                {/if}
              {/if}
            </div>
            <p class="status {sandboxNote.kind}">{sandboxNote.text}</p>
          {/if}
        </div>
        <div class="row">
          <div class="row-head">
            <span class="row-title">Background</span>
            <div class="seg" role="group" aria-label="Background">
              <button aria-pressed={backdrop === "plain"} onclick={() => chooseBackdrop("plain")}>Plain</button>
              <button aria-pressed={backdrop === "mesh"} onclick={() => chooseBackdrop("mesh")}>Grain + mesh</button>
            </div>
          </div>
          <p class="meta" style="margin:0">Remembered on this device.</p>
        </div>
        <div class="row">
          <div class="row-head">
            <span class="row-title">Interfaces in replies</span>
            <div class="seg" role="group" aria-label="Interfaces in replies">
              <button aria-pressed={!s.generative_ui} onclick={() => setUi(false)}>Off</button>
              <button aria-pressed={s.generative_ui} onclick={() => setUi(true)}>On</button>
            </div>
          </div>
          <p class="meta" style="margin:0">
            When a table, plan or checklist is clearer than text, Lark can show it as a small interface in the web chat (OpenUI). Everything else stays plain text, and Telegram always gets text.
          </p>
        </div>
        {#if s.sandbox}
          <div class="row">
            <div class="row-head">
              <span class="row-title">Keep browser cookies and logins</span>
              <div class="seg" role="group" aria-label="Keep browser cookies">
                <button aria-pressed={!s.browser_cookies} onclick={() => setCookies(false)}>Off</button>
                <button aria-pressed={s.browser_cookies} onclick={() => setCookies(true)}>On</button>
              </div>
            </div>
            <p class="meta" style="margin:0">
              Saved logins sit in the sandbox, where Lark's commands can read them, and a malicious web page could try to misuse them. Best for low-stakes sites.
            </p>
            <div class="row-actions">
              <button class="btn" onclick={clearBrowser}>Clear browser data</button>
            </div>
            <p class="status {browserNote.kind}">{browserNote.text}</p>
          </div>
        {/if}
      </div>
    </div>

    {/if}

    {#if s.telegram}
    <div class="group">
      <h2>Telegram</h2>
      <div class="rows">
        {@render keyRow("telegram", s.telegram)}
        {#if tg?.configured}
          <div class="row">
            <div class="row-head">
              <span class="row-title">{tg.owner ? `Linked to ${tg.owner.name}` : "Your account"}</span>
              <span class="meta">{tg.bot ? `@${tg.bot}` : ""}</span>
            </div>
            {#if tg.error}<p class="status bad">{tg.error}</p>{:else if !tg.polling}<p class="status bad">Not receiving messages yet. Check that the token is right and the server can reach Telegram, then reload this page.</p>{/if}
            <p class="meta" style="margin:0">
              {tg.owner ? "Chat with Lark in Telegram like here. /new starts over, /stop cancels." : "Link your Telegram so you can chat with Lark there and approve its messages to other people."}
            </p>
            <div class="row-actions">
              <button class="btn" onclick={makeLink}>{tg.owner ? "Link a different account" : "Link my Telegram"}</button>
              {#if tg.owner}<button class="btn" onclick={unlink}>Unlink</button>{/if}
            </div>
            {#if tgLink}
              <p class="meta" style="margin:0">Open this in Telegram within 15 minutes and press Start: <a href={tgLink} target="_blank" rel="noopener noreferrer">{tgLink}</a>. No Telegram app on this device? Open the bot in Telegram on your phone or at web.telegram.org and send it <code>{tgCommand}</code></p>
            {/if}
          </div>
          <div class="row">
            <span class="row-title">People</span>
            <p class="meta" style="margin:0">
              Lark can message people you invite, from its own account. They only see an AI assistant. Anything they write goes to a model with no tools and no access to your data.
            </p>
            {#each tg.contacts as c (c.id)}
              <div class="field" style="flex-wrap:wrap">
                <span class="row-title" style="min-width:90px">{c.name}</span>
                <select aria-label="What Lark does with messages from {c.name}" value={c.policy} onchange={(e) => editContact(c, { policy: e.currentTarget.value })}>
                  {#each Object.entries(policyLabels) as [v, l]}<option value={v}>{l}</option>{/each}
                </select>
                <input value={c.scope} placeholder="What Lark may help them with" aria-label="Scope for {c.name}" maxlength="500"
                  onchange={(e) => editContact(c, { scope: e.currentTarget.value })} />
                <button class="btn" onclick={() => removeContact(c)}>Remove</button>
              </div>
            {/each}
            <form class="field" style="flex-wrap:wrap" onsubmit={(e) => { e.preventDefault(); makeInvite(); }}>
              <input bind:value={invite.name} placeholder="Name" aria-label="Name" maxlength="40" required />
              <select bind:value={invite.policy} aria-label="Policy">
                {#each Object.entries(policyLabels) as [v, l]}<option value={v}>{l}</option>{/each}
              </select>
              <input bind:value={invite.scope} placeholder="What Lark may help them with" aria-label="Scope" maxlength="500" />
              <button class="btn primary" disabled={!invite.name.trim() || !tg.bot}>Create invite link</button>
            </form>
            {#if inviteUrl}
              <p class="meta" style="margin:0">Send them this link (works once, for a week): <a href={inviteUrl} target="_blank" rel="noopener noreferrer">{inviteUrl}</a>. Or they can open the bot and send <code>{inviteCommand}</code></p>
            {/if}
            <p class="status {tgNote.kind}">{tgNote.text}</p>
          </div>
        {/if}
      </div>
    </div>
    {/if}

    <div class="group">
      <h2>API keys</h2>
      <div class="rows">
        {#each Object.entries(s.providers) as [name, p]}
          {@render keyRow(name, p)}
        {/each}
      </div>
      <p class="meta" style="margin:8px 4px 0">
        Keys are encrypted on the server and never sent back to the browser.
      </p>
    </div>
  {/if}
</section>
