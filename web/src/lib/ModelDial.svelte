<script>
  import { app } from "./store.svelte.js";
  import { api } from "./api.js";

  const NAMES = ["cheap", "balanced", "smart"];
  const LABELS = { cheap: "Cheap", balanced: "Balanced", smart: "Smart" };
  const R = 14; // thumb radius: the track is as tall as the thumb, which sits flush inside it

  let open = $state(false);
  let drag = $state(null); // 0..1 while the thumb is being dragged
  let chosen = $state(null); // the stop just picked, held until the server confirms so the thumb never jumps back
  let root;
  let track = $state();

  const s = $derived(app.settings);
  const ready = $derived(s && NAMES.every((n) => s.tiers?.[n]));
  const index = $derived(ready ? NAMES.findIndex((n) => s.tiers[n] === s.models[s.provider]) : -1);
  const stop = $derived(chosen ?? (index < 0 ? 1 : index));
  const shown = $derived(drag === null ? stop : Math.round(drag * 2));
  const at = $derived(drag === null ? stop / 2 : drag);
  const x = (f, off = R) => `calc(${f} * (100% - ${2 * R}px) + ${off}px)`;

  async function pick(i) {
    if (i === index) {
      chosen = null;
      return;
    }
    chosen = i;
    try {
      app.settings = await api("/api/settings", { method: "PUT", body: { models: { [s.provider]: s.tiers[NAMES[i]] } } });
    } catch {}
    chosen = null;
  }

  const fraction = (e) => {
    const r = track.getBoundingClientRect();
    return Math.min(1, Math.max(0, (e.clientX - r.left - R) / (r.width - 2 * R)));
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
    chosen = i; // set before the drag ends, so the thumb glides to the stop from where it was let go
    drag = null;
    pick(i);
  }

  function key(e) {
    const cur = stop;
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
      <div class="dial-title" title={s.tiers[NAMES[shown]]}>
        {LABELS[NAMES[shown]]}
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m10 7 5 5-5 5" /></svg>
      </div>
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
        <span class="fill" style="width:{x(at, R - 3)}"></span>
        {#each NAMES as n, i}<span class="stop" class:passed={i / 2 <= at + 0.001} style="left:{x(i / 2)}"></span>{/each}
        <span class="thumb" style="left:{x(at)}"></span>
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
    position: absolute; bottom: calc(100% + 12px); right: -6px; width: 232px; padding: 12px 14px 14px;
    border-radius: 16px; border: 1px solid var(--line); background: var(--card);
    box-shadow: 0 6px 22px rgba(0, 0, 0, 0.1);
    opacity: 0; transform: translateY(6px) scale(0.97); transform-origin: bottom right; pointer-events: none;
    transition: opacity .18s ease, transform .22s cubic-bezier(.2, .9, .3, 1);
  }
  .open .dial-pop { opacity: 1; transform: none; pointer-events: auto; }
  .dial-title { display: flex; align-items: center; justify-content: center; gap: 2px; font-size: 14px; font-weight: 500; }
  .dial-title svg { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; opacity: 0.6; }
  .track {
    position: relative; height: 28px; margin-top: 12px; border-radius: 14px; background: var(--line);
    cursor: pointer; touch-action: none; outline: 0; user-select: none;
  }
  .track:focus-visible { outline: 2px solid var(--ink); outline-offset: 3px; }
  .fill { position: absolute; left: 3px; top: 3px; height: 22px; border-radius: 11px 0 0 11px; background: #3b82f6; transition: width .25s cubic-bezier(.3, .8, .3, 1); }
  .stop { position: absolute; top: 12.5px; width: 3px; height: 3px; margin-left: -1.5px; border-radius: 50%; background: var(--ink-soft); opacity: 0.55; }
  .stop.passed { background: #fff; opacity: 0.6; }
  .thumb {
    position: absolute; top: 2px; width: 24px; height: 24px; margin-left: -12px; border-radius: 50%;
    background: #fff; box-shadow: 0 1px 4px rgba(0, 0, 0, 0.28);
    transition: left .25s cubic-bezier(.3, .8, .3, 1), transform .15s;
  }
  .dragging .thumb, .dragging .fill { transition: none; }
  @media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
