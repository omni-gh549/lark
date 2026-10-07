<script>
  import { onMount, tick } from "svelte";
  import { chat, init, send, stop } from "./chat.svelte.js";
  import { configured } from "./store.svelte.js";
  import { render } from "./markdown.js";
  import ToolStatus from "./ToolStatus.svelte";
  import ToolChip from "./ToolChip.svelte";
  import BrowserWindow from "./BrowserWindow.svelte";
  import { uploadImage } from "./upload.js";

  let { onsettings } = $props();
  onMount(() => {
    init().then(() => tick()).then(scrollDown);
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
    if ((!t.trim() && !ids.length) || chat.busy || uploading) return;
    text = "";
    attachments = [];
    note = "";
    await tick(); // let the cleared value reach the textarea before measuring it
    resize();
    const p = send(t, ids);
    await tick();
    scrollDown();
    await p;
  }

  function scrollDown() {
    if (thread) thread.scrollTop = thread.scrollHeight;
  }

  function resize() {
    if (!box) return;
    box.style.height = "0px";
    box.style.height = Math.min(Math.max(box.scrollHeight, 40), 160) + "px";
  }

  function onkey(e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      submit();
    }
  }

  // follow the stream unless the reader has scrolled up
  $effect(() => {
    const last = chat.messages.at(-1);
    last?.content;
    last?.parts?.length;
    last?.parts?.at(-1)?.state;
    if (!thread) return;
    const near = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 120;
    if (near) tick().then(scrollDown);
  });
</script>

<section class="view chat">
  <div class="thread" bind:this={thread}>
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
        {@const live = chat.busy && m === chat.messages.at(-1)}
        {@const lastPart = m.parts?.at(-1)}
        {@const active = (m.parts ?? []).filter((p) => p.type === "tool" && p.state === "running")}
        <div class="turn">
          {#each m.parts ?? [{ type: "text", text: m.content }] as p}
            {#if p.type === "text"}
              {#if p.text}<div class="msg md">{@html render(p.text)}</div>{/if}
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

  <div>
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
      {#if chat.busy}
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
