<script>
  let { props } = $props();
</script>

<div class="tags">{#each props.items ?? [] as t}<span>{t}</span>{/each}</div>

<style>
  .tags { display: flex; flex-wrap: wrap; gap: 6px; }
  span { padding: 2px 10px; border-radius: 999px; background: var(--glass-strong); font-size: 12px; }
</style>
