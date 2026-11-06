<script>
  import { api } from "./api.js";
  import { app } from "./store.svelte.js";

  let u = $state(null);
  let error = $state("");
  let loading = $state(false);
  let go = $state(null);
  let goError = $state("");

  const has = $derived(Boolean(app.settings?.providers?.openrouter?.key_hint));
  const hasGo = $derived(Boolean(app.settings?.providers?.opencode?.key_hint));
  const resets = (w) => {
    const ms = w.resets_at ? new Date(w.resets_at) - Date.now() : w.resets_in != null ? w.resets_in * 1000 : null;
    if (ms == null || Number.isNaN(ms) || ms <= 0) return "";
    const h = Math.floor(ms / 36e5);
    return h >= 48 ? `resets in ${Math.round(h / 24)} days` : h >= 1 ? `resets in ${h} h` : `resets in ${Math.max(1, Math.round(ms / 6e4))} min`;
  };
  const money = (n) => (n == null ? "–" : `$${n >= 100 ? n.toFixed(0) : n >= 1 ? n.toFixed(2) : n.toFixed(4)}`);

  async function load() {
    loading = true;
    error = goError = "";
    await Promise.all([
      has ? api("/api/usage").then((r) => (u = r), (e) => (error = e.message)) : null,
      hasGo ? api("/api/usage/opencode").then((r) => (go = r), (e) => (goError = e.message)) : null,
    ]);
    loading = false;
  }

  $effect(() => {
    if (has || hasGo) load();
  });
</script>

{#if !has && !hasGo}
  <div class="group">
    <div class="rows">
      <div class="row"><p class="meta" style="margin:0">Add an OpenRouter or OpenCode Go key under API keys to see your usage.</p></div>
    </div>
  </div>
{:else}
  {#if has}
  <div class="group">
    <h2>OpenRouter credits</h2>
    <div class="rows">
      <div class="row">
        <div class="row-head">
          <span class="row-title">Remaining</span>
          <span class="figure">{money(u?.remaining)}</span>
        </div>
        <div class="row-head">
          <span class="meta">Used of {money(u?.total)} bought</span>
          <span class="meta">{money(u?.used)}</span>
        </div>
        {#if u?.total}<div class="bar" aria-hidden="true"><span style="width:{Math.min(100, (u.used / u.total) * 100)}%"></span></div>{/if}
      </div>
      {#if u?.key_limit != null}
        <div class="row">
          <div class="row-head"><span class="row-title">This key's limit</span><span class="figure">{money(u.key_remaining)} left</span></div>
          <span class="meta">{money(u.key_used)} used of {money(u.key_limit)}</span>
        </div>
      {/if}
    </div>
  </div>

  <div class="group">
    <h2>Recent spend</h2>
    <div class="rows">
      <div class="row"><div class="row-head"><span class="row-title">Today</span><span>{money(u?.day)}</span></div></div>
      <div class="row"><div class="row-head"><span class="row-title">This week</span><span>{money(u?.week)}</span></div></div>
      <div class="row"><div class="row-head"><span class="row-title">This month</span><span>{money(u?.month)}</span></div></div>
    </div>
    <p class="meta" style="margin:8px 4px 0">Spend by this API key. Per-model totals need an OpenRouter management key, so they are not shown.</p>
  </div>

  {/if}

  {#if hasGo}
  <div class="group">
    <h2>OpenCode Go</h2>
    <div class="rows">
      {#each go?.windows ?? [] as w (w.id)}
        <div class="row">
          <div class="row-head"><span class="row-title">{w.label}</span><span class="figure">{Math.round(w.percent)}%</span></div>
          <div class="bar" aria-hidden="true"><span style="width:{w.percent}%"></span></div>
          {#if resets(w)}<span class="meta">{resets(w)}</span>{/if}
        </div>
      {:else}
        <div class="row"><p class="meta" style="margin:0">{goError ? "" : "Loading"}</p></div>
      {/each}
    </div>
    <p class="meta" style="margin:8px 4px 0">Share of your plan's allowance used. Past 100% requests stop, unless Extra Usage is on in the OpenCode Console.</p>
    <p class="status bad">{goError}</p>
  </div>
  {/if}

  <div class="row-actions">
    <button class="btn" disabled={loading} onclick={load}>{loading ? "Refreshing…" : "Refresh"}</button>
  </div>
  <p class="status bad">{error}</p>
{/if}

<style>
  .figure { font-size: 20px; font-weight: 500; font-variant-numeric: tabular-nums; }
  .bar { height: 6px; border-radius: 3px; background: var(--line); overflow: hidden; }
  .bar span { display: block; height: 100%; background: var(--accent); }
</style>
