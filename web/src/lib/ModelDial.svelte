<script>
  import { app } from "./store.svelte.js";
  import { api } from "./api.js";
  import { chat } from "./chat.svelte.js";

  const NAMES = ["cheap", "balanced", "smart", "swarm"];
  const LABELS = { cheap: "Cheap", balanced: "Balanced", smart: "Smart", swarm: "Agent Swarm" };
  const BLURB = {
    cheap: "Lower cost, for quick questions.",
    balanced: "Everyday tasks at a steady pace.",
    smart: "Complex reasoning, coding and writing.",
    swarm: "A mode, not a model: many agents research and cross-check together. Slower, and costs more.",
  };
  const LAST = NAMES.length - 1;
  const SWARM = "swarm"; // stored as the chat's model while the slider is on Agent Swarm
  const R = 12; // thumb radius: the track is as tall as the thumb, which sits flush inside it

  let open = $state(false);
  let drag = $state(null); // 0..1 while the thumb is being dragged
  let chosen = $state(null); // the stop just picked, held until the server confirms so the thumb never jumps back
  let root;
  let track = $state();

  const s = $derived(app.settings);
  const ready = $derived(s && NAMES.slice(0, LAST).every((n) => s.tiers?.[n]));
  const current = $derived(chat.model === SWARM ? SWARM : (chat.id && chat.messages.length ? chat.model : null) ?? s?.models?.[s?.provider]); // this chat's own model, else the default
  const index = $derived(current === SWARM ? LAST : ready ? NAMES.findIndex((n) => s.tiers[n] === current) : -1);
  const stop = $derived(chosen ?? (index < 0 ? 1 : index));
  const shown = $derived(drag === null ? stop : Math.round(drag * LAST));
  const at = $derived(shown / LAST); // the thumb sits on a stop, even mid-drag
  const x = (f, off = R) => `calc(${f} * (100% - ${2 * R}px) + ${off}px)`;
  const swarm = $derived(shown === LAST);

  // "openai/gpt-6.1-sol" reads as "GPT 6.1 Sol"
  const pretty = (id) =>
    (id || "").split("/").pop().split(/[-_]/).filter(Boolean).map((w) => (/^(gpt|glm|ai|llm|moe)$/i.test(w) ? w.toUpperCase() : w[0].toUpperCase() + w.slice(1))).join(" ");

  async function pick(i) {
    if (i === index) {
      chosen = null;
      return;
    }
    chosen = i;
    try {
      if (i === LAST) {
        // Agent Swarm belongs to this chat only; it never becomes the default for new chats
        if (chat.id && chat.messages.length) await api(`/api/chats/${chat.id}/model`, { method: "PUT", body: { model: SWARM } });
        chat.model = SWARM;
      } else {
        const model = s.tiers[NAMES[i]];
        if (chat.id && chat.messages.length) {
          await api(`/api/chats/${chat.id}/model`, { method: "PUT", body: { model } }); // just this chat...
          chat.model = model;
        } else if (chat.model === SWARM) {
          chat.model = null; // a chat that hasn't started leaves the swarm and follows the default again
        }
        app.settings = await api("/api/settings", { method: "PUT", body: { models: { [s.provider]: model } } }); // ...and the default for new chats
      }
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
    const i = Math.round(fraction(e) * LAST);
    chosen = i; // set before the drag ends, so the thumb glides to the stop from where it was let go
    drag = null;
    pick(i);
  }

  function key(e) {
    const cur = stop;
    if (e.key === "ArrowRight" || e.key === "ArrowUp") { e.preventDefault(); pick(Math.min(LAST, cur + 1)); }
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
    <button type="button" class="dial-btn" aria-expanded={open} aria-label="Choose model: {index < 0 ? 'custom' : LABELS[NAMES[index]]}" onclick={() => (open = !open)}>
      {#if current && current !== SWARM}<span>{pretty(current)}</span><span class="dial-name">{index < 0 ? "Custom" : LABELS[NAMES[index]]}</span>{:else}<span>{index < 0 ? "Custom" : LABELS[NAMES[index]]}</span>{/if}
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 10 5 5 5-5" /></svg>
    </button>
    <div class="dial-pop" aria-hidden={!open} inert={!open}>
      <div class="dial-title" class:swarm>{LABELS[NAMES[shown]]}</div>
      {#if shown !== LAST && s.tiers?.[NAMES[shown]]}
        <a class="dial-model" href="#settings/model" onclick={() => (open = false)} title="Change which model this stop uses">{s.tiers[NAMES[shown]]}<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6" /></svg></a>
      {:else}
        <div class="dial-model plain">Several models at once</div>
      {/if}
      <div class="dial-wrap" class:swarm>
        <span class="dial-glow" style="left:{x(at)}"></span>
        <div
          class="track"
          class:dragging={drag !== null}
          bind:this={track}
          role="slider"
          tabindex="0"
          aria-label="Model tier"
          aria-valuemin="0"
          aria-valuemax={LAST}
          aria-valuenow={shown}
          aria-valuetext={LABELS[NAMES[shown]]}
          onpointerdown={down}
          onpointermove={move}
          onpointerup={up}
          onpointercancel={() => (drag = null)}
        >
          <span class="fill" style="width:{x(at, R - 3)}"></span>
          {#each NAMES as n, i}<span class="stop" class:passed={i / LAST <= at + 0.001} style="left:{x(i / LAST)}"></span>{/each}
          <span class="thumb" style="left:{x(at)}"></span>
        </div>
      </div>
      <div class="dial-labels">{#each NAMES as n, i}<span class:on={i === shown} class:first={i === 0} class:last={i === LAST} role="button" tabindex="-1" onclick={() => pick(i)} onkeydown={() => {}} style="left:{x(i / LAST)}">{i === LAST ? "Swarm" : LABELS[n]}</span>{/each}</div>
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
  .dial-btn:hover, .open .dial-btn { color: var(--ink); background: var(--sel); }
  .dial-btn svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; transition: transform .25s; }
  .open .dial-btn svg { transform: rotate(180deg); }
  .dial-pop {
    position: absolute; bottom: calc(100% + 12px); right: -6px; width: 244px; max-width: calc(100vw - 24px); padding: 12px 14px 10px;
    border-radius: 16px; border: 1px solid var(--line); background: var(--card);
    box-shadow: 0 6px 22px rgba(0, 0, 0, 0.1);
    opacity: 0; transform: translateY(6px) scale(0.97); transform-origin: bottom right; pointer-events: none;
    transition: opacity .18s ease, transform .22s cubic-bezier(.2, .9, .3, 1);
  }
  .open .dial-pop { opacity: 1; transform: none; pointer-events: auto; }
  .dial-title { text-align: center; font-size: 14.5px; font-weight: 600; color: #3b82f6; letter-spacing: -0.005em; }
  .dial-title.swarm { color: var(--ink); }
  .dial-model { display: flex; justify-content: center; align-items: center; gap: 2px; margin: 1px 0 10px; font-size: 12px; color: var(--ink); text-decoration: none; overflow: hidden; white-space: nowrap; }
  .dial-model:hover { color: var(--ink-soft); }
  .dial-model.plain { color: var(--ink-soft); }
  .dial-model svg { width: 14px; height: 14px; stroke: var(--ink-faint); }
  .dial-name { color: var(--ink-faint); margin-left: 6px; max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  @media (max-width: 480px) { .dial-name { display: none; } }
  .dial-labels span { cursor: pointer; padding: 4px 6px; margin-top: -4px; }
  .dial-wrap { position: relative; margin-top: 0; }
  /* a soft glow under the thumb, only on the fourth stop */
  .dial-glow {
    position: absolute; top: 50%; height: 44px; width: 84px; margin: -22px 0 0 -42px; border-radius: 50%; pointer-events: none;
    background: radial-gradient(closest-side, rgba(195, 103, 214, 0.55), rgba(123, 92, 240, 0.28) 55%, transparent);
    filter: blur(9px); transform: translateY(5px); opacity: 0; transition: opacity .35s ease, left .25s cubic-bezier(.3, .8, .3, 1);
  }
  .swarm .dial-glow { opacity: 0.9; }
  .track {
    position: relative; height: 24px; border-radius: 12px; background: var(--line);
    cursor: pointer; touch-action: none; outline: 0; user-select: none;
  }
  .track:focus-visible { outline: 2px solid var(--ink); outline-offset: 3px; }
  .fill {
    position: absolute; left: 2px; top: 2px; height: 20px; border-radius: 10px 0 0 10px; background: #3b82f6; overflow: hidden;
    transition: width .25s cubic-bezier(.3, .8, .3, 1), box-shadow .35s ease;
  }
  /* blue through violet to a warm peach, fading in as the thumb reaches Agent Swarm */
  .fill::before {
    content: ""; position: absolute; inset: 0; opacity: 0; transition: opacity .35s ease;
    background: linear-gradient(90deg, #3b82f6 0, #5a6ff2 30%, #7b5cf0 58%, #c367d6 82%, #f6a06b 100%);
  }
  .swarm .fill::before { opacity: 1; }
  .swarm .fill { box-shadow: 0 0 14px rgba(140, 90, 240, 0.35); }
  .stop { position: absolute; top: 10px; width: 4px; height: 4px; margin-left: -2px; border-radius: 50%; background: var(--ink-soft); opacity: 0.8; }
  .stop.passed { background: #fff; opacity: 0.9; }
  .thumb {
    position: absolute; top: 2px; width: 20px; height: 20px; margin-left: -10px; border-radius: 50%;
    background: #fff; box-shadow: 0 1px 4px rgba(0, 0, 0, 0.28);
    transition: left .25s cubic-bezier(.3, .8, .3, 1), transform .15s;
  }
  .dial-labels { position: relative; height: 14px; display: block; font-size: 11.5px; padding: 0; color: var(--ink-soft); margin-top: 9px; padding: 0 2px; }
  .dial-labels span { position: absolute; top: 0; transform: translateX(-50%); white-space: nowrap; transition: color .25s; }
  .dial-labels .first { transform: none; left: 0 !important; }
  .dial-labels .last { transform: none; left: auto !important; right: 0; }
  .dial-labels .on { color: var(--ink); }
  @media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
