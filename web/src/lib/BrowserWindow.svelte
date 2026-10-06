<script>
  // The sandbox browser, live: the server streams screenshots a few times a second while a page is open.
  let { url = "" } = $props();
  const src = `/api/browser/stream?t=${Date.now()}`;
</script>

<div class="window" role="img" aria-label="Live view of the browser">
  <div class="window-bar">
    <span class="lights" aria-hidden="true"><i></i><i></i><i></i></span>
    <span class="window-url">{url || "Browser"}</span>
  </div>
  <div class="window-view"><img {src} alt="" /></div>
</div>
