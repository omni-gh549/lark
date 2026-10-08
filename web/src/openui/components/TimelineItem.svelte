<script>
  let { props } = $props();
</script>

<li>
  <span class="time">{props.time ?? ""}</span>
  <span class="dot" aria-hidden="true"></span>
  <span class="what"><strong>{props.title}</strong>{#if props.detail}<small>{props.detail}</small>{/if}</span>
</li>

<style>
  li { display: grid; grid-template-columns: 52px 10px 1fr; gap: 10px; padding-bottom: 12px; position: relative; }
  li:last-child { padding-bottom: 0; }
  .time { color: var(--ink-soft); font-size: 12px; text-align: right; padding-top: 3px; font-variant-numeric: tabular-nums; }
  .dot { width: 8px; height: 8px; margin-top: 6px; border-radius: 50%; background: var(--ink); justify-self: center; position: relative; }
  li:not(:last-child) .dot::after { content: ""; position: absolute; left: 3.5px; top: 12px; width: 1px; height: calc(100% + 20px); background: var(--line); }
  .what { display: flex; flex-direction: column; gap: 1px; }
  strong { font-weight: 500; }
  small { color: var(--ink-soft); font-size: 13px; }
</style>
