<script>
  import { app } from "./store.svelte.js";
  import { api } from "./api.js";

  const NAMES = ["cheap", "balanced", "smart"];
  const LABELS = { cheap: "Cheap", balanced: "Balanced", smart: "Smart" };

  let open = $state(false);
  let saving = $state(false);
  let root;

  const s = $derived(app.settings);
  const ready = $derived(s && NAMES.every((n) => s.tiers?.[n]));
  const index = $derived(ready ? NAMES.findIndex((n) => s.tiers[n] === s.models[s.provider]) : -1);

  async function pick(i) {
    if (saving || i === index) return;
    saving = true;
    try {
      app.settings = await api("/api/settings", { method: "PUT", body: { models: { [s.provider]: s.tiers[NAMES[i]] } } });
    } catch {}
    saving = false;
  }

  function key(e) {
    const at = index < 0 ? 1 : index;
    if (e.key === "ArrowRight" || e.key === "ArrowUp") { e.preventDefault(); pick(Math.min(2, at + 1)); }
    else if (e.key === "ArrowLeft" || e.key === "ArrowDown") { e.preventDefault(); pick(Math.max(0, at - 1)); }
    else if (e.key === "Escape") open = false;
  }

  function outside(e) {
    if (open && root && !root.contains(e.target)) open = false;
  }
</script>

<svelte:window onpointerdown={outside} />

{#if ready}
  <div class="dial" class:open bind:this={root} onkeydown={key} role="group" aria-label="Model">
    <button type="button" class="dial-btn" aria-expanded={open} aria-label="Choose model: {index < 0 ? 'custom' : NAMES[index]}" onclick={() => (open = !open)}>
      <svg viewBox="0 0 24 24" aria-hidden="true">
        {#each [0, 1, 2] as i}
          <path class="bar" class:on={i <= index} d="M{6 + i * 6} 18V{15 - i * 4}" />
        {/each}
      </svg>
      <span>{index < 0 ? "Custom" : LABELS[NAMES[index]]}</span>
    </button>
    <div class="dial-pop" aria-hidden={!open} inert={!open}>
      <div class="track">
        <span class="fill" style="width:{index < 0 ? 50 : index * 50}%"></span>
        <span class="knob" class:none={index < 0} style="left:{index < 0 ? 50 : index * 50}%"></span>
        {#each NAMES as n, i}
          <button type="button" class="stop" style="left:{i * 50}%" aria-label={LABELS[n]} title="{LABELS[n]}: {s.tiers[n]}" onclick={() => pick(i)}></button>
        {/each}
      </div>
      <div class="names">
        {#each NAMES as n, i}<span class:active={i === index}>{LABELS[n]}</span>{/each}
      </div>
    </div>
  </div>
{/if}

<style>
  .dial { position: relative; display: flex; justify-content: flex-end; margin: 0 4px 8px; }
  .dial-btn {
    display: inline-flex; align-items: center; gap: 6px; padding: 5px 11px 5px 9px; border-radius: 999px;
    border: 1px solid var(--line); background: var(--glass); backdrop-filter: blur(20px);
    color: var(--ink-soft); font-size: 12.5px; transition: color .2s, background .2s, transform .2s;
  }
  .dial-btn:hover, .open .dial-btn { color: var(--ink); background: var(--glass-strong); }
  .dial-btn:active { transform: scale(0.96); }
  .dial-btn svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 2.4; stroke-linecap: round; }
  .bar { opacity: 0.35; transition: opacity .3s, transform .3s; transform-origin: center bottom; }
  .bar.on { opacity: 1; }
  .open .bar.on { transform: scaleY(1.12); }
  .dial-pop {
    position: absolute; right: 0; bottom: calc(100% + 8px); width: 210px; padding: 14px 20px 10px;
    border-radius: 18px; border: 1px solid var(--line); background: var(--glass-strong); backdrop-filter: blur(24px);
    opacity: 0; transform: translateY(8px) scale(0.94); transform-origin: bottom right; pointer-events: none;
    transition: opacity .22s ease, transform .3s cubic-bezier(.2, .9, .3, 1.2);
  }
  .open .dial-pop { opacity: 1; transform: none; pointer-events: auto; }
  .track { position: relative; height: 22px; margin: 0 6px; }
  .track::before { content: ""; position: absolute; left: 0; right: 0; top: 10px; height: 2px; border-radius: 2px; background: var(--line); }
  .fill { position: absolute; left: 0; top: 10px; height: 2px; border-radius: 2px; background: var(--accent); transition: width .35s cubic-bezier(.3, .8, .3, 1); }
  .knob {
    position: absolute; top: 4px; width: 14px; height: 14px; margin-left: -7px; border-radius: 50%; background: var(--accent);
    transition: left .35s cubic-bezier(.3, .8, .3, 1), opacity .2s; pointer-events: none;
  }
  .knob.none { opacity: 0.35; }
  .stop { position: absolute; top: 0; width: 34px; height: 22px; margin-left: -17px; background: none; border: 0; cursor: pointer; }
  .names { display: flex; justify-content: space-between; margin: 6px -8px 0; font-size: 11.5px; color: var(--ink-soft); }
  .names .active { color: var(--ink); }
  @media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
