<script>
  let { props } = $props();
  const values = $derived((props.values ?? []).map(Number).filter(Number.isFinite));
  const labels = $derived(props.labels ?? []);
  const max = $derived(Math.max(1e-9, ...values));
  const min = $derived(Math.min(0, ...values));
  const span = $derived(max - min || 1);
  const W = 300, H = 120, pad = 6;
  const x = (i) => (values.length < 2 ? W / 2 : pad + (i * (W - 2 * pad)) / (values.length - 1));
  const y = (v) => H - pad - ((v - min) / span) * (H - 2 * pad);
  const fmt = (v) => `${v}${props.unit ? " " + props.unit : ""}`;
</script>

{#if values.length}
  <figure>
    {#if props.kind === "line"}
      <svg viewBox="0 0 {W} {H}" role="img" aria-label={values.map((v, i) => `${labels[i] ?? i + 1} ${fmt(v)}`).join(", ")}>
        <polyline points={values.map((v, i) => `${x(i)},${y(v)}`).join(" ")} fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
        {#each values as v, i}<circle cx={x(i)} cy={y(v)} r="3" fill="currentColor"><title>{labels[i] ?? ""} {fmt(v)}</title></circle>{/each}
      </svg>
    {:else}
      <div class="ubars" role="img" aria-label={values.map((v, i) => `${labels[i] ?? i + 1} ${fmt(v)}`).join(", ")}>
        {#each values as v, i}
          <div title="{labels[i] ?? ''} {fmt(v)}"><span style="height:{Math.max(2, ((v - min) / span) * 100)}%"></span></div>
        {/each}
      </div>
    {/if}
    <div class="labels">{#each labels.slice(0, values.length) as l}<small>{l}</small>{/each}</div>
    {#if props.unit}<small class="unit">{props.unit}</small>{/if}
  </figure>
{/if}

<style>
  figure { margin: 0; display: flex; flex-direction: column; gap: 4px; color: var(--ink); }
  svg { width: 100%; height: 120px; display: block; }
  .ubars { display: flex; align-items: flex-end; gap: 6px; height: 120px; }
  .ubars div { flex: 1; height: 100%; display: flex; flex-direction: column; justify-content: flex-end; }
  .ubars span { display: block; align-self: center; width: 100%; max-width: 36px; border-radius: 4px 4px 0 0; background: currentColor; opacity: 0.85; }
  .labels { display: flex; justify-content: space-between; gap: 6px; }
  .labels small { flex: 1; text-align: center; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  small { color: var(--ink-soft); font-size: 12px; }
  .unit { text-align: right; }
</style>
