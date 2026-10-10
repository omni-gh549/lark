<script>
  import { onMount, tick } from "svelte";
  import { api } from "./api.js";
  import { app, loadSettings } from "./store.svelte.js";
  import { chat, newChat, openChat, deleteChat, refreshList } from "./chat.svelte.js";

  let { route, go, wide = $bindable(), drawer = $bindable(), panelsOpen = $bindable() } = $props();

  let menuOpen = $state(false);
  let searching = $state(false);
  let query = $state("");
  let searchEl = $state();

  const shown = $derived.by(() => {
    const q = query.trim().toLowerCase();
    return q ? chat.list.filter((c) => c.title.toLowerCase().includes(q)) : chat.list;
  });
  const active = $derived(app.settings ? app.settings.models[app.settings.provider] || "No model chosen" : "");

  const when = (t) => {
    const d = new Date(t * 1000);
    const days = Math.floor((Date.now() - d) / 864e5);
    if (days < 1 && new Date().getDate() === d.getDate()) return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    if (days < 7) return d.toLocaleDateString([], { weekday: "short" });
    return d.toLocaleDateString([], { day: "numeric", month: "short" });
  };

  const leave = () => {
    drawer = false;
    menuOpen = false;
  };
  const fresh = () => {
    newChat();
    go("chat");
    leave();
  };
  const pick = async (id) => {
    leave();
    await openChat(id);
    go("chat");
  };
  const section = (r) => {
    go(r);
    leave();
  };
  const togglePanels = () => {
    panelsOpen = !panelsOpen;
    leave();
  };
  async function find() {
    wide = true;
    searching = true;
    await tick();
    searchEl?.focus();
  }
  const endSearch = () => {
    searching = false;
    query = "";
  };
  async function signOut() {
    await api("/api/logout", { method: "POST" });
    menuOpen = false;
    await loadSettings();
  }

  onMount(() => {
    refreshList();
    const onclick = (e) => {
      if (menuOpen && !e.target.closest?.(".side-foot")) menuOpen = false;
    };
    const onkey = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === "b") {
        e.preventDefault();
        if (matchMedia("(max-width: 760px)").matches) drawer = !drawer;
        else wide = !wide;
        return;
      }
      if (e.key !== "Escape") return;
      if (menuOpen) menuOpen = false;
      else if (searching) endSearch();
      else if (drawer) drawer = false;
    };
    document.addEventListener("click", onclick);
    document.addEventListener("keydown", onkey);
    return () => {
      document.removeEventListener("click", onclick);
      document.removeEventListener("keydown", onkey);
    };
  });
</script>

{#if drawer}
  <button class="scrim side-scrim" aria-label="Close sidebar" tabindex="-1" onclick={() => (drawer = false)}></button>
{/if}

<aside class="side" class:wide class:drawer aria-label="Sidebar">
  <!-- slim rail: always there on wide screens while the panel is closed -->
  <div class="rail-icons">
    <button class="side-btn" aria-label="Sidebar" title={wide ? "Close sidebar" : "Open sidebar"} aria-expanded={wide} onclick={() => (wide = !wide)}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="15" rx="3"/><path d="M9.5 4.5v15"/></svg>
    </button>
    <button class="side-btn" aria-label="Home" title="Home" aria-current={route === "chat" ? "page" : undefined} onclick={() => section("chat")}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4.5 11 12 4.5l7.5 6.5V19a1 1 0 0 1-1 1H15v-5.5H9V20H5.5a1 1 0 0 1-1-1z"/></svg>
    </button>
    <button class="side-btn" aria-label="New chat" title="New chat" onclick={fresh}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20h8M4 20l1-4L16 5l3 3L8 19z"/></svg>
    </button>
    <button class="side-btn" aria-label="Search chats" title="Search chats" onclick={find}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/></svg>
    </button>
    <button class="side-btn" aria-label="Panels" title="Panels" aria-pressed={panelsOpen} onclick={togglePanels}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="7" height="7" rx="2"/><rect x="13" y="4" width="7" height="7" rx="2"/><rect x="4" y="13" width="7" height="7" rx="2"/><rect x="13" y="13" width="7" height="7" rx="2"/></svg>
    </button>
    <button class="side-btn" aria-label="Projects" title="Projects" aria-current={route === "projects" ? "page" : undefined} onclick={() => section("projects")}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7.5a2 2 0 0 1 2-2h3.2l2 2.2H18a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"/></svg>
    </button>
    <button class="side-btn" aria-label="Memory" title="Memory" aria-current={route === "memory" ? "page" : undefined} onclick={() => section("memory")}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 4.500 8 4-8 4-8-4z"/><path d="m4 12.500 8 4 8-4M4 16.500l8 4 8-4"/></svg>
    </button>
    <span class="grow"></span>
    <div class="side-foot rail-foot">
      <button class="side-btn avatar-btn" aria-label="Account" aria-expanded={menuOpen} onclick={() => (menuOpen = !menuOpen)}>
        <span class="avatar"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="9" r="3.5"/><path d="M5 20c1-3.6 3.8-5.5 7-5.5s6 1.9 7 5.5"/></svg></span>
      </button>
      {#if menuOpen}
        <div class="menu side-menu">
          <div class="menu-head"><span class="menu-sub">{active}</span></div>
          <button onclick={() => section("settings")}>Settings</button>
          {#if app.settings?.auth}<hr /><button onclick={signOut}>Sign out</button>{/if}
        </div>
      {/if}
    </div>
  </div>

  <!-- expanded panel (also the phone drawer) -->
  <div class="side-panel">
    <div class="side-head">
      <button class="side-name" onclick={() => section("chat")}>Lark</button>
      <span class="grow"></span>
      <button class="side-btn" aria-label="Search chats" title="Search chats" aria-pressed={searching} onclick={() => (searching ? endSearch() : find())}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/></svg>
      </button>
      <button class="side-btn collapse" aria-label="Close sidebar" title="Close sidebar" onclick={() => { wide = false; drawer = false; }}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="15" rx="3"/><path d="M9.5 4.5v15"/></svg>
      </button>
      <button class="side-btn drawer-close" aria-label="Close sidebar" onclick={() => (drawer = false)}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17"/></svg>
      </button>
    </div>

    {#if searching}
      <input class="side-search" bind:this={searchEl} bind:value={query} placeholder="Search chats" aria-label="Search chats" />
    {/if}

    <button class="side-new" onclick={fresh}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20h8M4 20l1-4L16 5l3 3L8 19z"/></svg>New chat
    </button>

    <nav class="side-nav phone-only" aria-label="Sections">
      <button aria-pressed={panelsOpen} onclick={togglePanels}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="7" height="7" rx="2"/><rect x="13" y="4" width="7" height="7" rx="2"/><rect x="4" y="13" width="7" height="7" rx="2"/><rect x="13" y="13" width="7" height="7" rx="2"/></svg>Panels
      </button>
      <button aria-current={route === "memory" ? "page" : undefined} onclick={() => section("memory")}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 4.5 8 4-8 4-8-4z"/><path d="m4 12.5 8 4 8-4M4 16.5l8 4 8-4"/></svg>Memory
      </button>
    </nav>

    <button class="side-label link" onclick={() => section("projects")}>Projects</button>
    <p class="side-empty">No projects</p>

    <div class="side-label">Recents</div>
    <div class="side-list">
      {#each shown as c (c.id)}
        <div class="side-row" class:current={c.id === chat.id && route === "chat"}>
          <button class="side-pick" onclick={() => pick(c.id)}>
            {#if c.running}<span class="dot live"></span>{/if}
            <span class="side-title">{c.title}</span>
          </button>
          <button class="side-del" aria-label="Delete chat" title={when(c.updated)} onclick={() => deleteChat(c.id)}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17"/></svg>
          </button>
        </div>
      {:else}
        <p class="side-empty">{query ? "No matching chats." : "No saved chats yet."}</p>
      {/each}
    </div>
  </div>

  <div class="side-foot panel-foot phone-only">
    <button class="side-me" aria-label="Account" aria-expanded={menuOpen} onclick={() => (menuOpen = !menuOpen)}>
      <span class="avatar"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="9" r="3.5"/><path d="M5 20c1-3.6 3.8-5.5 7-5.5s6 1.9 7 5.5"/></svg></span>
      <span class="side-me-text"><span>Account</span><small>{active}</small></span>
    </button>
    {#if menuOpen}
      <div class="menu side-menu">
        <button onclick={() => section("settings")}>Settings</button>
        {#if app.settings?.auth}<hr /><button onclick={signOut}>Sign out</button>{/if}
      </div>
    {/if}
  </div>
</aside>
