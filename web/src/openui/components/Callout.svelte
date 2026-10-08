<script>
  let { props } = $props();
</script>

<p class:warn={props.tone === "warning"}>{props.text}</p>

<style>
  p { margin: 0; padding: 8px 12px; border-radius: 10px; background: var(--glass-strong); border-left: 3px solid var(--ink-soft); font-size: 14px; }
  .warn { border-left-color: var(--danger); }
</style>
