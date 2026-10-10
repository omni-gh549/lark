<script>
  import { app } from "./store.svelte.js";
  import { api } from "./api.js";

  const NAMES = ["cheap", "balanced", "smart"];
  const LABELS = { cheap: "Cheap", balanced: "Balanced", smart: "Smart" };

  let open = $state(false);
  let saving = $state(false);
  let drag = $state(null); // 0..1 while the thumb is being dragged
  let root;
  let track = $state();

  const s = $derived(app.settings);
  const ready = $derived(s && NAMES.every((n) => s.tiers?.[n]));
  const index = $derived(ready ? NAMES.findIndex((n) => s.tiers[n] === s.models[s.provider]) : -1);
  const shown = $derived(drag === null ? (index < 0 ? 1 : index) : Math.round(drag * 2)); // the stop the thumb is on or nearest
  const at = $derived(drag === null ? (index < 0 ? 0.5 : index / 2) : drag);

  async function pick(i) {
    if (saving || i === index) return;
    saving = true;
    try {
      app.settings = await api("/api/settings", { method: "PUT", body: { models: { [s.provider]: s.tiers[NAMES[i]] } } });
    } catch {}
    saving = false;
  }

  const fraction = (e) => {
    const r = track.getBoundingClientRect();
    return Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
  };
  function down(e) {
    if (e.button > 0) return;
    track.setPointerCapture(e.pointerId);
    drag = fraction(e);
  }
  function move(e) {
    if (drag !== null) drag = fraction(e);
  }
  function up(e) {
    if (drag === null) return;
    const i = Math.round(fraction(e) * 2);
    drag = null;
    pick(i);
  }

  function key(e) {
    const cur = index < 0 ? 1 : index;
    if (e.key === "ArrowRight" || e.key === "ArrowUp") { e.preventDefault(); pick(Math.min(2, cur + 1)); }
    else if (e.key === "ArrowLeft" || e.key === "ArrowDown") { e.preventDefault(); pick(Math.max(0, cur - 1)); }
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
      <span>{index < 0 ? "Custom" : LABELS[NAMES[index]]}</span>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 10 5 5 5-5" /></svg>
    </button>
    <div class="dial-pop" aria-hidden={!open} inert={!open}>
      <div class="dial-title">{LABELS[NAMES[shown]]}</div>
      <div class="dial-model">{s.tiers[NAMES[shown]]}</div>
      <div
        class="track"
        class:dragging={drag !== null}
        bind:this={track}
        role="slider"
        tabindex="0"
        aria-label="Model tier"
        aria-valuemin="0"
        aria-valuemax="2"
        aria-valuenow={shown}
        aria-valuetext={LABELS[NAMES[shown]]}
        onpointerdown={down}
        onpointermove={move}
        onpointerup={up}
        onpointercancel={() => (drag = null)}
      >
        <span class="groove"></span>
        <span class="fill" class:easing={drag === null} style="width:{at * 100}%"></span>
        {#each NAMES as n, i}<span class="stop" class:passed={i / 2 <= at} style="left:{i * 50}%"></span>{/each}
        <span class="thumb" class:none={index < 0 && drag === null} style="left:{at * 100}%"></span>
      </div>
    </div>
  </div>
{/if}

<style>
  .dial { position: relative; display: flex; align-self: center; flex: none; }
  .dial-btn {
    display: inline-flex; align-items: center; gap: 2px; padding: 5px 6px 5px 11px; border-radius: 999px;
    border: 0; background: none;
    color: var(--ink-soft); font-size: 13.5px; transition: color .2s, background .2s;
  }
  .dial-btn:hover, .open .dial-btn { color: var(--ink); background: var(--glass); }
  .dial-btn svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; transition: transform .25s; }
  .open .dial-btn svg { transform: rotate(180deg); }
  .dial-pop {
    position: absolute; bottom: calc(100% + 14px); right: -8px; width: 244px; padding: 16px 20px 18px;
    border-radius: 22px; border: 1px solid var(--line); background: var(--card); text-align: center;
    box-shadow: 0 12px 32px rgba(0, 0, 0, 0.12);
    opacity: 0; transform: translateY(6px) scale(0.97); transform-origin: bottom right; pointer-events: none;
    transition: opacity .18s ease, transform .22s cubic-bezier(.2, .9, .3, 1);
  }
  .open .dial-pop { opacity: 1; transform: none; pointer-events: auto; }
  .dial-title { font-size: 17px; font-weight: 600; }
  .dial-model { margin-top: 2px; text-align: center; font: 12px/1.4 var(--mono); color: var(--ink-soft); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .track { position: relative; height: 28px; margin: 16px 8px 0; cursor: pointer; touch-action: none; outline: 0; user-select: none; }
  .track:focus-visible { outline: 2px solid var(--ink); outline-offset: 6px; border-radius: 14px; }
  .groove { position: absolute; left: 0; right: 0; top: 11px; height: 6px; border-radius: 3px; background: var(--line); }
  .fill { position: absolute; left: 0; top: 11px; height: 6px; border-radius: 3px; background: var(--accent); }
  .stop { position: absolute; top: 12.5px; width: 3px; height: 3px; margin-left: -1.5px; border-radius: 50%; background: var(--ink-soft); opacity: 0.6; }
  .stop.passed { background: var(--on-accent); opacity: 0.7; }
  .fill.easing { transition: width .25s cubic-bezier(.3, .8, .3, 1); }
  .thumb {
    position: absolute; top: 2px; width: 24px; height: 24px; margin-left: -12px; border-radius: 50%;
    background: #fff; box-shadow: 0 1px 6px rgba(0, 0, 0, 0.3), 0 0 0 1px rgba(0, 0, 0, 0.06);
    transition: left .25s cubic-bezier(.3, .8, .3, 1), opacity .2s, transform .15s;
  }
  .dragging .thumb { transition: none; transform: scale(1.08); }
  .thumb.none { opacity: 0.35; }
  @media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
