<script>
  let { props } = $props();
</script>

<div class="wrap">
  <table>
    <thead><tr>{#each props.columns ?? [] as c}<th>{c}</th>{/each}</tr></thead>
    <tbody>
      {#each props.rows ?? [] as row}
        <tr>{#each row as cell}<td>{cell}</td>{/each}</tr>
      {/each}
    </tbody>
  </table>
</div>

<style>
  .wrap { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; font-size: 14px; }
  th, td { padding: 6px 12px 6px 0; text-align: left; vertical-align: top; border-bottom: 1px solid var(--line); }
  th { font-weight: 500; color: var(--ink-soft); font-size: 12px; }
  tr:last-child td { border-bottom: 0; }
</style>
