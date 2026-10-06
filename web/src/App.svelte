<script>
  import { onMount } from "svelte";
  import { api } from "./lib/api.js";
  import { app, loadSettings } from "./lib/store.svelte.js";
  import { chat, newChat } from "./lib/chat.svelte.js";
  import Chat from "./lib/Chat.svelte";
  import Projects from "./lib/Projects.svelte";
  import Settings from "./lib/Settings.svelte";
  import Gallery from "./lib/Gallery.svelte";
  import Panels from "./lib/Panels.svelte";
  import Login from "./lib/Login.svelte";

  const ROUTES = ["chat", "projects", "settings", "components"];
  const fromHash = () => {
    const h = location.hash.slice(1);
    return ROUTES.includes(h) ? h : "chat";
  };

  let route = $state(fromHash());
  let panelsOpen = $state(false);
  let menuOpen = $state(false);
  let menuEl = $state();

  const go = (r) => {
    route = r;
    menuOpen = false;
    if (location.hash.slice(1) !== r) history.replaceState(null, "", r === "chat" ? location.pathname : `#${r}`);
  };

  onMount(() => {
    loadSettings();
    const onhash = () => (route = fromHash());
    const onclick = (e) => {
      if (menuOpen && menuEl && !menuEl.contains(e.target)) menuOpen = false;
    };
    const onkey = (e) => {
      if (e.key !== "Escape") return;
      if (menuOpen) menuOpen = false;
      else panelsOpen = false;
    };
    addEventListener("hashchange", onhash);
    document.addEventListener("click", onclick);
    document.addEventListener("keydown", onkey);
    return () => {
      removeEventListener("hashchange", onhash);
      document.removeEventListener("click", onclick);
      document.removeEventListener("keydown", onkey);
    };
  });

  async function signOut() {
    await api("/api/logout", { method: "POST" });
    menuOpen = false;
    await loadSettings();
  }

  const active = $derived(app.settings ? app.settings.models[app.settings.provider] || "No model chosen" : "");
</script>

{#if app.blocked}
  <div class="centre"><p class="status bad">{app.blocked}</p></div>
{:else if app.locked}
  <Login />
{:else}
  <div class="app">
    <header class="top">
      <div class="top-left">
        <button class="icon-btn" aria-label="Panels" aria-expanded={panelsOpen} aria-controls="panels" onclick={() => (panelsOpen = !panelsOpen)}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="15" rx="3"/><path d="M9.5 4.5v15"/></svg>
        </button>
        {#if route === "chat"}
          <button class="icon-btn" aria-label="New chat" onclick={newChat} disabled={!chat.messages.length}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20h8M4 20l1-4L16 5l3 3L8 19z"/></svg>
          </button>
        {/if}
      </div>

      <nav class="modes" aria-label="View">
        <button aria-current={route === "chat" ? "page" : undefined} onclick={() => go("chat")}>Chat</button>
        <button aria-current={route === "projects" ? "page" : undefined} onclick={() => go("projects")}>Projects</button>
      </nav>

      <div class="account" bind:this={menuEl}>
        <button class="icon-btn" aria-label="Account" aria-expanded={menuOpen} aria-controls="account-menu" onclick={() => (menuOpen = !menuOpen)}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="9" r="3.5"/><path d="M5 20c1-3.6 3.8-5.5 7-5.5s6 1.9 7 5.5"/></svg>
        </button>
        {#if menuOpen}
          <div class="menu" id="account-menu">
            <div class="menu-head">
              <span class="menu-sub">{active}</span>
            </div>
            <button onclick={() => go("settings")}>Settings</button>
            {#if app.settings?.auth}
              <hr />
              <button onclick={signOut}>Sign out</button>
            {/if}
          </div>
        {/if}
      </div>
    </header>

    {#if panelsOpen}<Panels />{/if}

    <main>
      {#if route === "chat"}
        <Chat onsettings={() => go("settings")} />
      {:else if route === "projects"}
        <Projects />
      {:else if route === "settings"}
        <Settings />
      {:else}
        <Gallery />
      {/if}
    </main>
  </div>
{/if}
