<script>
  import { toolIcon } from "./toolIcons.js";

  let { part } = $props();
</script>

<details class="chip" class:failed={part.state === "error"}>
  <summary>
    <svg viewBox="0 0 24 24" aria-hidden="true">{@html toolIcon(part.name)}</svg>
    <span class="chip-title">{part.title}</span>
    {#if part.detail}<span class="detail">{part.detail}</span>{/if}
    {#if part.state === "error"}<span class="chip-flag">failed</span>{/if}
  </summary>
  <pre>{part.output || "No output."}</pre>
</details>
