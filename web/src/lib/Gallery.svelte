<script>
  import { registry } from "./registry.js";
</script>

<section class="page">
  <h1>Components</h1>
  <div class="gallery">
    {#each Object.entries(registry) as [name, entry]}
      <div>
        <h2>{name}</h2>
        <entry.component {...entry.example} />
      </div>
    {/each}
  </div>
</section>
