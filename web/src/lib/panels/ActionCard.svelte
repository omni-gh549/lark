<script>
  let { verb = "", text = "", actions = [], onaction = () => {} } = $props();
</script>

<article class="card">
  <p><span class="verb">{verb}</span> {text}</p>
  {#if actions.length}
    <div class="actions">
      {#each actions as a}
        <button class="btn" class:primary={a.primary} onclick={() => onaction(a.id ?? a.label)}>{a.label}</button>
      {/each}
    </div>
  {/if}
</article>
