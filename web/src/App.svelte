<script>
  import { onMount } from "svelte";
  import { api } from "./lib/api.js";
  import { app, loadSettings } from "./lib/store.svelte.js";
  import { chat, newChat, openChat, deleteChat, refreshList } from "./lib/chat.svelte.js";
  import Chat from "./lib/Chat.svelte";
  import Projects from "./lib/Projects.svelte";
  import Settings from "./lib/Settings.svelte";
  import Memory from "./lib/Memory.svelte";
  import Gallery from "./lib/Gallery.svelte";
  import Panels from "./lib/Panels.svelte";
  import Login from "./lib/Login.svelte";
  import { watchMesh } from "./lib/backdrop.js";

  const ROUTES = ["chat", "projects", "memory", "settings", "components"];
  const fromHash = () => {
    const h = location.hash.slice(1);
    return ROUTES.includes(h) ? h : "chat";
  };

  let route = $state(fromHash());
  let panelsOpen = $state(false);
  let menuOpen = $state(false);
  let menuEl = $state();
  let historyOpen = $state(false);
  let historyEl = $state();
  let meshEl = $state();
  let probe = $state(""); // triple-tap the Chat/Projects switch to show how the browser measures the screen
  let taps = [];
  let probing = 0;
  const measure = () => {
    const vv = window.visualViewport;
    const a = document.querySelector("#app")?.getBoundingClientRect();
    const cs = getComputedStyle(document.documentElement);
    const tip = document.createElement("div");
    tip.style.cssText = "position:fixed;bottom:0;height:env(safe-area-inset-bottom)";
    document.body.append(tip);
    const sab = tip.getBoundingClientRect().height;
    tip.remove();
    probe = `inner ${innerWidth}x${innerHeight}  vv ${Math.round(vv.width)}x${Math.round(vv.height)} top ${Math.round(vv.offsetTop)}  screen ${screen.height}  app ${Math.round(a.top)}..${Math.round(a.bottom)}  sab ${sab}  kb ${document.documentElement.dataset.kb ?? "no"}  appH ${cs.getPropertyValue("--app-h") || "100%"}  scrollY ${scrollY}  standalone ${navigator.standalone}`;
  };
  const tripleTap = () => {
    const now = Date.now();
    taps = [...taps.filter((t) => now - t < 700), now];
    if (taps.length < 3) return;
    taps = [];
    if (probing) {
      clearInterval(probing);
      probing = 0;
      probe = "";
    } else {
      measure();
      probing = setInterval(measure, 300);
    }
  };

  $effect(() => watchMesh(meshEl));

  const toggleHistory = () => {
    historyOpen = !historyOpen;
    if (historyOpen) refreshList();
  };
  const pick = async (id) => {
    historyOpen = false;
    await openChat(id);
    go("chat");
  };
  const when = (t) => {
    const d = new Date(t * 1000);
    const days = Math.floor((Date.now() - d) / 864e5);
    if (days < 1 && new Date().getDate() === d.getDate()) return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    if (days < 7) return d.toLocaleDateString([], { weekday: "short" });
    return d.toLocaleDateString([], { day: "numeric", month: "short" });
  };

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
      if (historyOpen && historyEl && !historyEl.contains(e.target)) historyOpen = false;
    };
    const onkey = (e) => {
      if (e.key !== "Escape") return;
      if (menuOpen) menuOpen = false;
      else if (historyOpen) historyOpen = false;
      else panelsOpen = false;
    };
    // iOS keeps the layout viewport put and pans the page when the keyboard opens. While the keyboard is closed the app
    // simply fills the screen (never trust visualViewport for that: in a home-screen app it can stop short of the
    // home indicator). While it is open the app is pinned to the visible area, so nothing scrolls or has to be put back.
    const vv = window.visualViewport;
    const root = document.documentElement;
    let kbHeight = 334; // a typical iPhone keyboard; replaced by what this device really shows
    try {
      kbHeight = Number(localStorage.getItem("lark.kb")) || kbHeight;
    } catch {}
    const open = (visible, top) => {
      root.style.setProperty("--vh", `${Math.round(visible)}px`);
      root.style.setProperty("--app-h", `${Math.round(visible)}px`);
      root.style.setProperty("--vt", `${Math.round(top)}px`);
      root.dataset.kb = "";
    };
    const close = () => {
      root.style.removeProperty("--vh");
      root.style.removeProperty("--app-h");
      root.style.setProperty("--vt", "0px");
      delete root.dataset.kb;
    };
    const FIELD = "textarea, input:not([type=file]), select";
    const fielded = () => !!document.activeElement?.matches?.(FIELD);
    // iOS can leave visualViewport stale after the keyboard goes away, so a closed keyboard is decided by focus, not by size
    const fit = () => {
      const lost = innerHeight - vv.height;
      if (fielded() && lost > 120) {
        kbHeight = lost;
        savekb();
        open(vv.height, vv.offsetTop);
      } else if (!fielded() || lost <= 120) close();
    };
    const typing = (e) => e.target.matches?.(FIELD) && matchMedia("(pointer: coarse)").matches;
    // shrink the app the moment a field is focused, before iOS decides it has to pan to show it
    const focusin = (e) => {
      if (typing(e) && kbHeight) open(innerHeight - kbHeight, 0);
    };
    const settle = () => [60, 350, 800, 1500].forEach((ms) => setTimeout(fit, ms));
    const focusout = (e) => {
      if (typing(e)) settle();
    };
    const savekb = () => {
      try {
        if (kbHeight > 120) localStorage.setItem("lark.kb", String(Math.round(kbHeight)));
      } catch {}
    };
    if (vv) {
      fit();
      vv.addEventListener("resize", fit);
      vv.addEventListener("scroll", fit);
      addEventListener("resize", fit);
      addEventListener("orientationchange", settle);
      document.addEventListener("focusin", focusin);
      document.addEventListener("focusout", focusout);
      addEventListener("pagehide", savekb);
      document.addEventListener("visibilitychange", savekb);
    }
    addEventListener("hashchange", onhash);
    document.addEventListener("click", onclick);
    document.addEventListener("keydown", onkey);
    return () => {
      document.removeEventListener("focusin", focusin);
      document.removeEventListener("focusout", focusout);
      removeEventListener("resize", fit);
      vv?.removeEventListener("resize", fit);
      vv?.removeEventListener("scroll", fit);
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

<div class="backdrop" aria-hidden="true"><canvas bind:this={meshEl}></canvas></div>

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
          <div class="history" bind:this={historyEl}>
            <button class="icon-btn" aria-label="Chat history" aria-expanded={historyOpen} aria-controls="history-menu" onclick={toggleHistory}>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4.5 12a7.5 7.5 0 1 0 2.4-5.5L4.5 8.8"/><path d="M4.5 4.5v4.3h4.3M12 8v4.2l2.8 1.8"/></svg>
            </button>
            {#if historyOpen}
              <div class="menu history-menu" id="history-menu">
                {#each chat.list as c (c.id)}
                  <div class="history-row" class:current={c.id === chat.id}>
                    <button class="history-pick" onclick={() => pick(c.id)}>
                      <span class="history-title">{c.title}</span>
                      <span class="menu-sub">{#if c.running}<span class="dot live"></span> Replying…{:else}{when(c.updated)}{/if}</span>
                    </button>
                    <button class="history-del" aria-label="Delete chat" onclick={() => deleteChat(c.id)}>
                      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17"/></svg>
                    </button>
                  </div>
                {:else}
                  <p class="menu-sub" style="margin:8px 10px">No saved chats yet.</p>
                {/each}
              </div>
            {/if}
          </div>
        {/if}
      </div>

      <nav class="modes" aria-label="View" onclick={tripleTap}>
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
            <button onclick={() => go("memory")}>Memory</button>
            <button onclick={() => go("settings")}>Settings</button>
            {#if app.settings?.auth}
              <hr />
              <button onclick={signOut}>Sign out</button>
            {/if}
          </div>
        {/if}
      </div>
    </header>

    {#if probe}<div class="probe">{probe}</div>{/if}
    {#if panelsOpen}
      <button class="scrim" aria-label="Close panels" tabindex="-1" onclick={() => (panelsOpen = false)}></button>
      <Panels />
    {/if}

    <main>
      {#if route === "chat"}
        <Chat onsettings={() => go("settings")} />
      {:else if route === "projects"}
        <Projects />
      {:else if route === "memory"}
        <Memory onopen={async (id) => { await openChat(id); go("chat"); }} />
      {:else if route === "settings"}
        <Settings />
      {:else}
        <Gallery />
      {/if}
    </main>
  </div>
{/if}
