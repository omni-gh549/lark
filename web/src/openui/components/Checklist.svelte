<script>
  let { props } = $props();
  let done = $state({});
</script>

<ul>
  {#each props.items ?? [] as item, i}
    <li><label><input type="checkbox" bind:checked={done[i]} /><span class:done={done[i]}>{item}</span></label></li>
  {/each}
</ul>

<style>
  ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  label { display: flex; gap: 10px; align-items: baseline; cursor: pointer; }
  input { accent-color: var(--accent); margin: 0; }
  .done { text-decoration: line-through; color: var(--ink-soft); }
</style>
