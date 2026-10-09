<script>
  import { onMount, tick } from "svelte";
  import { chat, init, send, stop } from "./chat.svelte.js";
  import { configured } from "./store.svelte.js";
  import { render, segments } from "./markdown.js";
  import Block from "../openui/Block.svelte";
  import ToolStatus from "./ToolStatus.svelte";
  import ToolChip from "./ToolChip.svelte";
  import BrowserWindow from "./BrowserWindow.svelte";
  import { uploadImage } from "./upload.js";

  let { onsettings } = $props();
  onMount(() => {
    init().then(() => tick()).then(jump);
  });
  let text = $state("");
  let box = $state();
  let thread = $state();
  let attachments = $state([]); // [{ id, name }] uploaded and waiting to be sent
  let uploading = $state(0);
  let picker = $state();
  let note = $state("");

  async function addFiles(list) {
    note = "";
    for (const file of [...list].filter((f) => f.type.startsWith("image/")).slice(0, 8 - attachments.length)) {
      uploading++;
      try {
        attachments.push({ id: await uploadImage(file), name: file.name });
      } catch (e) {
        note = e.message;
      } finally {
        uploading--;
      }
    }
  }

  // the address of the latest page Lark opened (the detail of a "goto" step)
  const browserUrl = (parts) => parts.findLast((p) => p.name === "browser" && /^\S+\.\S+$/.test(p.detail ?? ""))?.detail ?? "";

  function onpaste(e) {
    const files = [...(e.clipboardData?.files ?? [])].filter((f) => f.type.startsWith("image/"));
    if (files.length) {
      e.preventDefault();
      addFiles(files);
    }
  }

  async function submit() {
    const t = text;
    const ids = attachments.map((a) => a.id);
    if ((!t.trim() && !ids.length) || uploading) return;
    text = "";
    attachments = [];
    note = "";
    await tick(); // let the cleared value reach the textarea before measuring it
    resize();
    jump(); // sending puts you back at the bottom
    const p = send(t, ids);
    await tick();
    jump();
    await p;
  }

  function resize() {
    if (!box) return;
    box.style.height = "0px";
    box.style.height = Math.min(Math.max(box.scrollHeight, 40), 160) + "px";
  }

  function onkey(e) {
    // on a phone the keyboard's Enter is a new line; the send button sends
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && !matchMedia("(pointer: coarse)").matches) {
      e.preventDefault();
      submit();
    }
  }

  // Follow the conversation while the reader is at the bottom, whatever grows: text, tool cards, images, tables, the browser window.
  // "At the bottom" comes from the reader's own scrolling, not from measuring after the page has already grown.
  let stuck = true;
  let unseen = $state(false); // new content arrived while the reader is up in the history
  let frame = 0;
  const atBottom = () => !thread || thread.scrollHeight - thread.scrollTop - thread.clientHeight < 80;
  function follow() {
    if (!thread) return;
    if (!stuck) {
      unseen = true;
      return;
    }
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => thread && (thread.scrollTop = thread.scrollHeight)); // instant: smooth scrolling fights fast updates
  }
  // the custom scrollbar on the right edge of the page
  let rail = $state(), bar = $state({ top: 0, size: 0 });
  function measure() {
    if (!thread || !rail) return;
    const { scrollTop, scrollHeight, clientHeight } = thread;
    const room = rail.clientHeight;
    if (scrollHeight <= clientHeight + 1) return void (bar = { top: 0, size: 0 });
    const size = Math.max(36, (clientHeight / scrollHeight) * room);
    bar = { top: (scrollTop / (scrollHeight - clientHeight)) * (room - size), size };
  }
  function onscroll() {
    stuck = atBottom();
    if (stuck) unseen = false;
    measure();
  }
  function drag(e) {
    e.preventDefault();
    const startY = e.clientY, startTop = thread.scrollTop;
    const ratio = (thread.scrollHeight - thread.clientHeight) / Math.max(1, rail.clientHeight - bar.size);
    const move = (ev) => (thread.scrollTop = startTop + (ev.clientY - startY) * ratio);
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }
  function railclick(e) {
    if (e.target !== rail) return;
    thread.scrollBy({ top: (e.clientY < rail.getBoundingClientRect().top + bar.top ? -1 : 1) * thread.clientHeight * 0.9, behavior: "smooth" });
  }
  function jump() {
    stuck = true;
    unseen = false;
    follow();
  }
  let shown = null;
  $effect(() => {
    // opening or switching to a chat starts at the latest message, and stays there while it lays out
    const id = chat.id;
    if (thread && id !== shown) {
      shown = id;
      jump();
    }
  });
  $effect(() => {
    if (!thread) return;
    const watch = new ResizeObserver(follow);
    const watchAll = () => [...thread.children].forEach((c) => watch.observe(c));
    const changes = new MutationObserver(() => {
      watchAll();
      follow();
      measure();
    });
    watchAll();
    changes.observe(thread, { childList: true, subtree: true, characterData: true });
    const sized = new ResizeObserver(measure);
    sized.observe(thread);
    const loaded = () => follow(); // images finishing loading push everything down
    thread.addEventListener("load", loaded, true);
    // the wheel works anywhere on the page, not just over the centre column
    const wheel = (e) => {
      if (e.ctrlKey || e.defaultPrevented || thread.contains(e.target)) return;
      for (let el = e.target; el instanceof Element && el !== document.body; el = el.parentElement) {
        const y = getComputedStyle(el).overflowY;
        if ((y === "auto" || y === "scroll") && el.scrollHeight > el.clientHeight) return; // something else scrolls there
      }
      thread.scrollBy({ top: e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY });
    };
    window.addEventListener("wheel", wheel, { passive: true });
    return () => {
      window.removeEventListener("wheel", wheel);
      watch.disconnect();
      changes.disconnect();
      sized.disconnect();
      thread.removeEventListener("load", loaded, true);
      cancelAnimationFrame(frame);
    };
  });
</script>

<section class="view chat">
  <div class="thread" bind:this={thread} {onscroll}>
    {#each chat.messages as m}
      {#if m.role === "user"}
        <div class="msg me">
          {#if m.images?.length}
            <div class="pics">{#each m.images as id}<a href="/api/files/{id}" target="_blank" rel="noopener"><img src="/api/files/{id}" alt="Attached" /></a>{/each}</div>
          {/if}
          {m.content}
        </div>
      {:else if m.error}
        <div class="msg error">{m.content}</div>
      {:else}
        {@const live = chat.busy && m === chat.messages.findLast((x) => x.role === "assistant")}
        {@const lastPart = m.parts?.at(-1)}
        {@const active = (m.parts ?? []).filter((p) => p.type === "tool" && p.state === "running")}
        <div class="turn">
          {#each m.parts ?? [{ type: "text", text: m.content }] as p}
            {#if p.type === "text"}
              {#each p.text ? segments(p.text) : [] as s}
                {#if s.type === "ui"}
                  <Block source={s.text} open={s.open && live} onsend={(t) => send(t, [])} />
                {:else if s.text.trim()}
                  <div class="msg md">{@html render(s.text)}</div>
                {/if}
              {/each}
            {:else if p.state !== "running"}
              <ToolChip part={p} />
              {#each p.images ?? [] as id}
                <a class="shot" href="/api/files/{id}" target="_blank" rel="noopener"><img src="/api/files/{id}" alt="Shown by Lark" /></a>
              {/each}
            {/if}
          {/each}
          {#if live && m.parts?.some((p) => p.type === "tool" && (p.name === "browser" || p.name === "request_login"))}
            <BrowserWindow url={browserUrl(m.parts)} />
          {/if}
          {#if live && lastPart?.type !== "text"}
            <ToolStatus tool={active.length ? { ...active[0], detail: active.length > 1 ? `${active[0].detail} +${active.length - 1} more` : active[0].detail } : null} />
          {/if}
        </div>
      {/if}
    {/each}
  </div>

  <div class="rail" bind:this={rail} onpointerdown={railclick} aria-hidden="true">
    {#if bar.size}<div class="thumb-bar" style="top:{bar.top}px;height:{bar.size}px" onpointerdown={drag}></div>{/if}
  </div>

  <div class="composer-wrap">
    {#if unseen}
      <button type="button" class="newer" onclick={jump}>New messages ↓</button>
    {/if}
    {#if !configured()}
      <p class="notice">Add an API key in <a href="#settings" onclick={onsettings}>Settings</a> to start chatting.</p>
    {/if}
    {#if attachments.length || uploading || note}
      <div class="attached">
        {#each attachments as a (a.id)}
          <span class="thumb">
            <img src="/api/files/{a.id}" alt={a.name} />
            <button type="button" aria-label="Remove image" onclick={() => (attachments = attachments.filter((x) => x.id !== a.id))}>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17"/></svg>
            </button>
          </span>
        {/each}
        {#if uploading}<span class="thumb loading" aria-label="Uploading"></span>{/if}
        {#if note}<span class="status bad">{note}</span>{/if}
      </div>
    {/if}
    <form class="composer" onsubmit={(e) => { e.preventDefault(); submit(); }}
      ondragover={(e) => e.preventDefault()} ondrop={(e) => { e.preventDefault(); addFiles(e.dataTransfer.files); }}>
      <button type="button" class="attach" aria-label="Attach image" onclick={() => picker.click()}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11.500 12.500 19a4.800 4.800 0 0 1-6.800-6.800l7.500-7.500a3.200 3.200 0 0 1 4.500 4.500l-7.500 7.500a1.600 1.600 0 0 1-2.300-2.300l7-7"/></svg>
      </button>
      <input bind:this={picker} type="file" accept="image/*" multiple hidden onchange={(e) => { addFiles(e.currentTarget.files); e.currentTarget.value = ""; }} />
      <textarea
        bind:this={box}
        bind:value={text}
        oninput={resize}
        onkeydown={onkey}
        onpaste={onpaste}
        rows="1"
        placeholder="Message Lark"
        aria-label="Message Lark"
      ></textarea>
      {#if chat.busy && !text.trim() && !attachments.length}
        <button type="button" class="send" aria-label="Stop" onclick={stop}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor"/></svg>
        </button>
      {:else}
        <button type="submit" class="send" aria-label="Send" disabled={(!text.trim() && !attachments.length) || !!uploading}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5M6 11l6-6 6 6"/></svg>
        </button>
      {/if}
    </form>
  </div>
</section>
