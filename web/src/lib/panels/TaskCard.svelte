<script>
  let { title = "", step = 0, total = 1, status = "", live = true, actions = [], onaction = () => {} } = $props();
  const pct = $derived(Math.min(100, Math.max(0, (step / total) * 100)));
</script>

<article class="card">
  <header class="card-head">
    <span class="dot" class:live aria-hidden="true"></span>
    <span>{title}</span>
  </header>
  <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax={total} aria-valuenow={step}>
    <span style="width:{pct}%"></span>
  </div>
  <p class="meta">Step {step} of {total}{status ? `, ${status}` : ""}</p>
  {#if actions.length}
    <div class="actions">
      {#each actions as a}
        <button class="btn" onclick={() => onaction(a.id ?? a.label)}>{a.label}</button>
      {/each}
    </div>
  {/if}
</article>
