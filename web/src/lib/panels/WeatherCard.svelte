<script>
  let { place = "", condition = "", temp = 0, feels = 0, high = 0, low = 0 } = $props();
</script>

<article class="card">
  <header class="card-head"><span>Weather{place ? `, ${place}` : ""}</span></header>
  <div class="tiles">
    <div class="tile">
      <span class="meta">{condition}</span>
      <span class="big">{Math.round(temp)}°</span>
      <span class="meta">feels {Math.round(feels)}°</span>
    </div>
    <div class="tile">
      <span class="meta">High</span>
      <span class="big">{Math.round(high)}°</span>
      <span class="meta">low {Math.round(low)}°</span>
    </div>
  </div>
</article>
