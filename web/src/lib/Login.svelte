<script>
  import { api } from "./api.js";
  import { loadSettings } from "./store.svelte.js";

  let password = $state("");
  let error = $state("");
  let busy = $state(false);

  async function submit(e) {
    e.preventDefault();
    busy = true;
    error = "";
    try {
      await api("/api/login", { method: "POST", body: { password } });
      await loadSettings();
    } catch (err) {
      error = err.message;
    } finally {
      busy = false;
    }
  }
</script>

<div class="centre">
  <form class="login" onsubmit={submit}>
    <h1>Lark</h1>
    <div class="field">
      <input type="password" bind:value={password} placeholder="Password" aria-label="Password" autocomplete="current-password" />
      <button class="btn primary" disabled={busy || !password}>Sign in</button>
    </div>
    <p class="status bad">{error}</p>
  </form>
</div>
