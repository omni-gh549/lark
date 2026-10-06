<script>
  import { tick } from "svelte";
  import { chat, send, stop } from "./chat.svelte.js";
  import { configured } from "./store.svelte.js";
  import { render } from "./markdown.js";
  import ToolStatus from "./ToolStatus.svelte";
  import ToolChip from "./ToolChip.svelte";

  let { onsettings } = $props();
  let text = $state("");
  let box = $state();
  let thread = $state();

  async function submit() {
    const t = text;
    if (!t.trim() || chat.busy) return;
    text = "";
    resize();
    const p = send(t);
    await tick();
    scrollDown();
    await p;
  }

  function scrollDown() {
    thread.scrollTop = thread.scrollHeight;
  }

  function resize() {
    if (!box) return;
    box.style.height = "auto";
    box.style.height = Math.min(box.scrollHeight, 160) + "px";
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
        <div class="msg me">{m.content}</div>
      {:else if m.error}
        <div class="msg error">{m.content}</div>
      {:else}
        {@const live = chat.busy && m === chat.messages.at(-1)}
        {@const lastPart = m.parts?.at(-1)}
        <div class="turn">
          {#each m.parts ?? [{ type: "text", text: m.content }] as p}
            {#if p.type === "text"}
              {#if p.text}<div class="msg md">{@html render(p.text)}</div>{/if}
            {:else if p.state !== "running"}
              <ToolChip part={p} />
            {/if}
          {/each}
          {#if live && lastPart?.type !== "text"}
            <ToolStatus tool={lastPart?.state === "running" ? lastPart : null} />
          {/if}
        </div>
      {/if}
    {/each}
  </div>

  <div>
    {#if !configured()}
      <p class="notice">Add an API key in <a href="#settings" onclick={onsettings}>Settings</a> to start chatting.</p>
    {/if}
    <form class="composer" onsubmit={(e) => { e.preventDefault(); submit(); }}>
      <textarea
        bind:this={box}
        bind:value={text}
        oninput={resize}
        onkeydown={onkey}
        rows="1"
        placeholder="Message Lark"
        aria-label="Message Lark"
      ></textarea>
      {#if chat.busy}
        <button type="button" class="send" aria-label="Stop" onclick={stop}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor"/></svg>
        </button>
      {:else}
        <button type="submit" class="send" aria-label="Send" disabled={!text.trim()}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5M6 11l6-6 6 6"/></svg>
        </button>
      {/if}
    </form>
  </div>
</section>
