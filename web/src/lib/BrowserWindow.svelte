<script>
  import { onMount } from "svelte";
  import { api } from "./api.js";

  // The sandbox browser, live: the server streams screenshots a few times a second while a page is open,
  // and a cursor glides to wherever Lark last clicked or typed.
  let { url = "" } = $props();
  const src = `/api/browser/stream?t=${Date.now()}`;

  let cursor = $state(null);
  let ripple = $state(0);
  let asked = $state(null); // Lark is waiting for you to sign in: { site, reason }
  let control = $state(false); // you're driving the browser
  let view, sink;
  let queue = Promise.resolve();

  // Your clicks and keys go straight to the browser and nowhere else: not to the model, the chat or memory.
  const send = (body) => (queue = queue.then(() => api("/api/browser/input", { method: "POST", body }).catch(() => {})));
  const SPECIAL = { Enter: "Enter", Backspace: "Backspace", Tab: "Tab", Escape: "Escape", Delete: "Delete", Home: "Home", End: "End",
    ArrowUp: "ArrowUp", ArrowDown: "ArrowDown", ArrowLeft: "ArrowLeft", ArrowRight: "ArrowRight", PageUp: "PageUp", PageDown: "PageDown" };

  function at(e) {
    const r = view.getBoundingClientRect();
    return { x: Math.min(Math.max((e.clientX - r.left) / r.width, 0), 1), y: Math.min(Math.max((e.clientY - r.top) / r.height, 0), 1) };
  }
  function onclick(e) {
    send({ type: "click", ...at(e) });
    sink?.focus();
  }
  function onwheel(e) {
    e.preventDefault();
    send({ type: "scroll", ...at(e), dy: e.deltaY });
  }
  function onkeydown(e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const key = SPECIAL[e.key];
    if (key) {
      e.preventDefault();
      send({ type: "key", key });
    }
  }
  function oninput(e) {
    const t = e.currentTarget.value;
    e.currentTarget.value = "";
    if (t) send({ type: "text", text: t });
  }
  async function take() {
    control = true;
    await Promise.resolve();
    sink?.focus();
  }
  async function done() {
    control = false;
    try {
      await api("/api/browser/login/done", { method: "POST" });
    } catch {}
  }

  onMount(() => {
    let stop = false;
    let seq = 0;
    let n = 0;
    async function poll() {
      while (!stop) {
        try {
          if (n++ % 4 === 0) {
            const p = (await (await fetch("/api/browser/login")).json()).pending;
            if (p && !asked) control = true; // Lark asked: hand over straight away
            asked = p;
          }
          const r = await fetch("/api/browser/cursor");
          const c = r.ok ? (await r.json()).cursor : null;
          if (c && c.seq !== seq) {
            seq = c.seq;
            cursor = c;
            if (c.click) setTimeout(() => (ripple = c.seq), 520);
          }
        } catch {}
        await new Promise((res) => setTimeout(res, 250));
      }
    }
    poll();
    return () => (stop = true);
  });
</script>

<div class="window" role="group" aria-label="Live view of the browser">
  <div class="window-bar">
    <span class="lights" aria-hidden="true"><i></i><i></i><i></i></span>
    <span class="window-url">{url || "Browser"}</span>
    {#if control}
      <button class="btn primary mini" onclick={done}>{asked ? "I'm signed in" : "Done"}</button>
    {:else}
      <button class="btn mini" onclick={take}>Take over</button>
    {/if}
  </div>
  {#if asked}
    <p class="ask">Lark needs you to sign in{asked.site ? ` to ${asked.site}` : ""}.{asked.reason ? ` ${asked.reason}` : ""}{control ? " Click and type in the window below, then press I'm signed in." : ""}</p>
  {/if}
  <div class="window-view" bind:this={view}>
    <img {src} alt="" />
    {#if cursor}
      <div class="pointer" style="left: {cursor.x * 100}%; top: {cursor.y * 100}%">
        {#key ripple}{#if ripple}<span class="ripple"></span>{/if}{/key}
        <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d="M5 3l14 7.5-6 1.8-2.4 6.2z" /></svg>
      </div>
    {/if}
    <div class="glow" aria-hidden="true"></div>
    {#if control}
      <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
      <div class="takeover" {onclick} {onwheel}></div>
      <textarea bind:this={sink} class="sink" rows="1" aria-label="Type into the browser" autocomplete="off" autocapitalize="off" spellcheck="false" {onkeydown} {oninput}></textarea>
    {/if}
  </div>
</div>
