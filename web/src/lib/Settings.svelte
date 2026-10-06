<script>
  import { api } from "./api.js";
  import { app } from "./store.svelte.js";

  const s = $derived(app.settings);
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
      </div>
    </div>

    <div class="group">
      <h2>API keys</h2>
      <div class="rows">
        {#each Object.entries(s.providers) as [name, p]}
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
        {/each}
      </div>
      <p class="meta" style="margin:8px 4px 0">
        Keys are encrypted on the server and never sent back to the browser.
      </p>
    </div>
  {/if}
</section>
