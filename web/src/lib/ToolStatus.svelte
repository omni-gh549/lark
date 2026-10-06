<script>
  import { toolIcon } from "./toolIcons.js";

  // `tool` is null while the model is thinking, or { name, title, detail } while a tool runs.
  let { tool = null } = $props();

  const wanted = $derived(tool ? { title: tool.title, detail: tool.detail } : { title: "Thinking…", detail: "" });
  let shown = $state({ title: "Thinking…", detail: "" });
  let blurred = $state(false);
  let timer;

  // The label blurs out, swaps while hidden, then comes back into focus.
  $effect(() => {
    const next = wanted;
    if (next.title === shown.title && next.detail === shown.detail) return;
    blurred = true;
    clearTimeout(timer);
    timer = setTimeout(() => {
      shown = next;
      blurred = false;
    }, 170);
    return () => clearTimeout(timer);
  });
</script>

<div class="tool-status" role="status" aria-live="polite">
  <span class="mark" class:is-tool={!!tool}>
    <span class="dots" aria-hidden="true"><i></i><i></i><i></i></span>
    {#if tool}
      {#key tool.name}
        <svg class="glyph" viewBox="0 0 24 24" aria-hidden="true">{@html toolIcon(tool.name)}</svg>
      {/key}
    {/if}
  </span>
  <span class="label" class:blurred>
    {shown.title}{#if shown.detail}<span class="detail">{shown.detail}</span>{/if}
  </span>
</div>
