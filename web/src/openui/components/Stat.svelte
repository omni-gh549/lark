<script>
  let { props } = $props();
</script>

<div class="stat">
  <small>{props.label}</small>
  <strong>{props.value}</strong>
  {#if props.note}<small>{props.note}</small>{/if}
</div>

<style>
  .stat { display: flex; flex-direction: column; gap: 2px; }
  strong { font-size: 24px; font-weight: 600; line-height: 1.15; }
  small { color: var(--ink-soft); font-size: 12px; }
</style>
