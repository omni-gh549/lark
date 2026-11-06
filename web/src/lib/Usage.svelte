<script>
  import { api } from "./api.js";
  import { app } from "./store.svelte.js";

  let u = $state(null);
  let error = $state("");
  let loading = $state(false);

  const has = $derived(Boolean(app.settings?.providers?.openrouter?.key_hint));
  const money = (n) => (n == null ? "–" : `$${n >= 100 ? n.toFixed(0) : n >= 1 ? n.toFixed(2) : n.toFixed(4)}`);

  async function load() {
    loading = true;
    error = "";
    try {
      u = await api("/api/usage");
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    if (has) load();
  });
</script>

{#if !has}
  <div class="group">
    <div class="rows">
      <div class="row"><p class="meta" style="margin:0">Add an OpenRouter key under API keys to see your credit and spend.</p></div>
    </div>
  </div>
{:else}
  <div class="group">
    <h2>Credits</h2>
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
