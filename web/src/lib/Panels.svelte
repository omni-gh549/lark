<script>
  import { app } from "./store.svelte.js";
  import { registry } from "./registry.js";
</script>

<aside class="panels" id="panels">
  {#each app.panels as p (p.id)}
    {@const entry = registry[p.type]}
    {#if entry}
      <entry.component {...p.props} />
    {/if}
  {:else}
    <p class="empty">No panels yet. They show up here when Lark makes one.</p>
  {/each}
</aside>
