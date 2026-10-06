<script>
  import { onMount } from "svelte";

  // The sandbox browser, live: the server streams screenshots a few times a second while a page is open,
  // and a cursor glides to wherever Lark last clicked or typed.
  let { url = "" } = $props();
  const src = `/api/browser/stream?t=${Date.now()}`;

  let cursor = $state(null);
  let ripple = $state(0);

  onMount(() => {
    let stop = false;
    let seq = 0;
    async function poll() {
      while (!stop) {
        try {
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

<div class="window" role="img" aria-label="Live view of the browser">
  <div class="window-bar">
    <span class="lights" aria-hidden="true"><i></i><i></i><i></i></span>
    <span class="window-url">{url || "Browser"}</span>
  </div>
  <div class="window-view">
    <img {src} alt="" />
    {#if cursor}
      <div class="pointer" style="left: {cursor.x * 100}%; top: {cursor.y * 100}%">
        {#key ripple}{#if ripple}<span class="ripple"></span>{/if}{/key}
        <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d="M5 3l14 7.5-6 1.8-2.4 6.2z" /></svg>
      </div>
    {/if}
    <div class="glow" aria-hidden="true"></div>
  </div>
</div>
