<script>
  let { props, renderNode } = $props();
</script>

<section class="ui-card">
  {#if props.title}<h4>{props.title}</h4>{/if}
  {#each props.children ?? [] as child}{@render renderNode(child)}{/each}
</section>

<style>
  .ui-card { display: flex; flex-direction: column; gap: 12px; padding: 14px 16px; border-radius: 16px; background: var(--glass); border: 1px solid var(--line); max-width: 100%; text-align: left; }
  h4 { margin: 0; font-size: 14px; font-weight: 600; }
</style>
