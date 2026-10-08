<script>
  let { props } = $props();
</script>

<p class:muted={props.tone === "muted"}>{props.content}</p>

<style>
  p { margin: 0; white-space: pre-wrap; }
  .muted { color: var(--ink-soft); font-size: 13px; }
</style>
