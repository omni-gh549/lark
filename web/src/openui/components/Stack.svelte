<script>
  let { props, renderNode } = $props();
</script>

<div class="ustack" class:urow={props.direction === "row"}>
  {#each props.children ?? [] as child}{@render renderNode(child)}{/each}
</div>

<style>
  .ustack { display: flex; flex-direction: column; gap: 12px; }
  .ustack.urow { flex-direction: row; flex-wrap: wrap; gap: 20px; }
</style>
