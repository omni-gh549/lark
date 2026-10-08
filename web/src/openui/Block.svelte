<script>
  import { Renderer } from "@openuidev/svelte-lang";
  import { library } from "./library.js";

  // One interface from a reply. `open` is true while the model is still writing it.
  let { source, open = false, onsend } = $props();
  let failed = $state(false);

  function onAction(e) {
    if (e.type === "continue_conversation") onsend?.(e.humanFriendlyMessage || String(e.params?.message ?? ""));
  }
</script>

{#if failed}
  <pre class="ui-raw">{source}</pre>
{:else}
  <div class="ui-block">
    <svelte:boundary onerror={() => (failed = true)}>
      <Renderer response={source} {library} isStreaming={open} {onAction} />
    </svelte:boundary>
  </div>
{/if}

<style>
  .ui-block { max-width: 100%; }
  .ui-raw { margin: 0; white-space: pre-wrap; font: 12px var(--mono); color: var(--ink-soft); }
</style>
