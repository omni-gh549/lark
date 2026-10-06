<script>
  let { title = "", summary = "", bars = [] } = $props();
  const max = $derived(Math.max(1, ...bars.map((b) => b.value)));
</script>

<article class="card">
  <header class="card-head">
    <span>{title}</span>
    {#if summary}<span class="meta num">{summary}</span>{/if}
  </header>
  <div class="bars" role="img" aria-label={bars.map((b) => `${b.label} ${b.value}`).join(", ")}>
    {#each bars as b}
      <div><span style="height:{(b.value / max) * 100}%"></span><small>{b.label}</small></div>
    {/each}
  </div>
</article>
