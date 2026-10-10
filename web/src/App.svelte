<script>
  import { onMount } from "svelte";
  import { app, loadSettings } from "./lib/store.svelte.js";
  import { chat, newChat, openChat } from "./lib/chat.svelte.js";
  import Chat from "./lib/Chat.svelte";
  import Projects from "./lib/Projects.svelte";
  import Settings from "./lib/Settings.svelte";
  import Memory from "./lib/Memory.svelte";
  import Gallery from "./lib/Gallery.svelte";
  import Panels from "./lib/Panels.svelte";
  import Sidebar from "./lib/Sidebar.svelte";
  import Login from "./lib/Login.svelte";
  import { watchMesh } from "./lib/backdrop.js";

  const ROUTES = ["chat", "projects", "memory", "settings", "components"];
  const fromHash = () => {
    const h = location.hash.slice(1);
    return ROUTES.includes(h) ? h : "chat";
  };

  let route = $state(fromHash());
  let panelsOpen = $state(false);
  let drawer = $state(false); // the sidebar as a drawer, on phones
  // wide screens: the sidebar is a slim rail that opens into a panel; remembered per browser
  let wide = $state(
    (() => {
      try {
        const v = localStorage.getItem("lark.side");
        if (v !== null) return v === "1";
      } catch {}
      return innerWidth >= 1000;
    })(),
  );
  $effect(() => {
    try {
      localStorage.setItem("lark.side", wide ? "1" : "0");
    } catch {}
  });
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

  const go = (r) => {
    route = r;
    if (location.hash.slice(1) !== r) history.replaceState(null, "", r === "chat" ? location.pathname : `#${r}`);
  };

  onMount(() => {
    loadSettings();
    const onhash = () => (route = fromHash());
    const onkey = (e) => {
      if (e.key === "Escape") panelsOpen = false;
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
    document.addEventListener("keydown", onkey);
    return () => {
      document.removeEventListener("focusin", focusin);
      document.removeEventListener("focusout", focusout);
      removeEventListener("resize", fit);
      vv?.removeEventListener("resize", fit);
      vv?.removeEventListener("scroll", fit);
      removeEventListener("hashchange", onhash);
      document.removeEventListener("keydown", onkey);
    };
  });

</script>

<div class="backdrop" aria-hidden="true"><canvas bind:this={meshEl}></canvas></div>

{#if app.blocked}
  <div class="centre"><p class="status bad">{app.blocked}</p></div>
{:else if app.locked}
  <Login />
{:else}
  <div class="app" class:side-wide={wide}>
    <Sidebar {route} {go} bind:wide bind:drawer bind:panelsOpen />
    <header class="top">
      <div class="top-left">
        <button class="icon-btn menu-btn" aria-label="Menu" aria-expanded={drawer} onclick={() => (drawer = true)}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="15" rx="3"/><path d="M9.5 4.5v15"/></svg>
        </button>
        {#if route === "chat"}
          <button class="icon-btn menu-btn" aria-label="New chat" onclick={newChat} disabled={!chat.messages.length}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20h8M4 20l1-4L16 5l3 3L8 19z"/></svg>
          </button>
        {/if}
      </div>

      <nav class="modes" aria-label="View" onclick={tripleTap}>
        <button aria-current={route === "chat" ? "page" : undefined} onclick={() => go("chat")}>Chat</button>
        <button aria-current={route === "projects" ? "page" : undefined} onclick={() => go("projects")}>Projects</button>
      </nav>

      <span class="top-end" aria-hidden="true"></span>
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
