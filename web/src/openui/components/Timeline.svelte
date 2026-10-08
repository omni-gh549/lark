<script>
  let { props, renderNode } = $props();
</script>

<ol>
  {#each props.items ?? [] as item}{@render renderNode(item)}{/each}
</ol>

<style>
  ol { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
</style>
