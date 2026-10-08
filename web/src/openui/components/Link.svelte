<script>
  let { props } = $props();
  const safe = $derived(/^https:\/\//i.test(props.url ?? ""));
</script>

{#if safe}
  <a href={props.url} target="_blank" rel="noopener noreferrer">{props.label}</a>
{:else}
  <span>{props.label}</span>
{/if}

<style>
  a { align-self: flex-start; text-decoration: underline; text-underline-offset: 3px; }
</style>
