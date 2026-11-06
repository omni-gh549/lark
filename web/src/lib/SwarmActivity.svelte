<script>
  import { onMount } from "svelte";
  import { api } from "./api.js";

  // `part` is the reply's swarm card, kept up to date in place by chat.svelte.js. `live` is true while this reply is running.
  let { part, live = false } = $props();

  const PHASES = ["Plan", "Research", "Verify", "Review", "Finish"];
  const KIND = {
    conductor: "Conductor", lead: "Squad lead", researcher: "Researcher", scout: "Scout", browser: "Browser worker", builder: "Builder",
    analyst: "Analyst", writer: "Writer", verifier: "Verifier", red_team: "Red team", critic: "Critic", finisher: "Writer",
  };
  const REVIEW = new Set(["verifier", "red_team", "critic", "finisher"]);
  const BLUE = "#3b82f6";

  let canvas = $state();
  let box = $state();
  let expanded = $state(false); // a finished card is one line until opened
  let now = $state(Date.now());
  let picked = $state(null);
  let detail = $state(null);
  let tip = $state({ on: false, x: 0, y: 0, text: "", sub: "" });

  const running = $derived(live && !part.done);
  const showField = $derived(running || expanded);
  const agents = $derived(part.agents.filter((a) => a.kind !== "conductor"));
  const working = $derived(agents.filter((a) => a.state === "work").length);
  const waiting = $derived(agents.filter((a) => a.state === "wait").length);
  const done = $derived(agents.filter((a) => a.state === "done").length);
  const elapsed = $derived(Math.max(0, Math.round((part.stats?.elapsed ?? 0) + (running ? (now - (part.at ?? now)) / 1000 : 0))));
  const spent = $derived(part.stats?.spent ?? 0);
  const clock = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  const took = (s) => (s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`);
  const summary = $derived(
    `${part.stats?.answered ? "Swarm worked" : "Swarm stopped"} ${took(elapsed)} · ${agents.length} agents` +
      (part.stats?.sources ? ` · ${part.stats.sources} sources` : "") +
      (part.stats?.capped ? " · stopped at its limit" : ""),
  );

  $effect(() => {
    if (!running) return;
    const t = setInterval(() => (now = Date.now()), 1000);
    return () => clearInterval(t);
  });

  // ---- the field: one node per agent, grouped in a cluster per squad ----
  const nodes = new Map(); // id -> { x, y, tx, ty, born, flash }
  let W = 0, H = 0, dpr = 1, raf = 0, hover = null;
  const reduce = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

  function size() {
    if (!canvas) return;
    dpr = window.devicePixelRatio || 1;
    W = canvas.clientWidth;
    H = canvas.clientHeight;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
  }

  const groupOf = (a) => (a.kind === "conductor" ? "root" : REVIEW.has(a.kind) ? "review" : "sq:" + (a.squad || "").split("/")[0]);

  function place() {
    const cx = W / 2, cy = H / 2;
    const groups = new Map();
    for (const a of part.agents) {
      const g = groupOf(a);
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(a);
    }
    const squads = [...groups.keys()].filter((g) => g.startsWith("sq:"));
    const n = Math.max(1, squads.length);
    const rx = W * (n > 5 ? 0.33 : 0.3), ry = H * (n > 5 ? 0.3 : 0.26);
    const cr = Math.min(Math.max(34, Math.min(rx, ry) * Math.sin(Math.PI / Math.max(2, n)) * 1.7), 110);
    const target = (a, x, y) => {
      let r = nodes.get(a.id);
      if (!r) {
        const p = nodes.get(a.parent) ?? { x: cx, y: cy };
        r = { x: p.x, y: p.y, tx: x, ty: y, born: performance.now() - (reduce ? 1000 : 0), flash: 0 };
        nodes.set(a.id, r);
      }
      r.tx = Math.max(12, Math.min(W - 12, x));
      r.ty = Math.max(12, Math.min(H - 12, y));
      if (reduce) { r.x = r.tx; r.y = r.ty; }
    };
    for (const a of groups.get("root") ?? []) target(a, cx, cy);
    squads.forEach((g, i) => {
      const ang = (i / n) * Math.PI * 2 - Math.PI / 2;
      const sx = cx + (n === 1 ? 0 : Math.cos(ang) * rx), sy = cy + (n === 1 ? -ry * 0.05 : Math.sin(ang) * ry);
      const members = groups.get(g);
      const lead = members.find((a) => a.kind === "lead" && a.parent === "a0");
      const rest = members.filter((a) => a !== lead);
      if (lead) target(lead, sx, sy);
      const unit = Math.max(6.5, Math.min(17, cr / Math.sqrt(rest.length + 3)));
      rest.forEach((a, k) => {
        const b = k * 2.39996 + i, d = (lead ? 22 : 10) + Math.sqrt(k) * unit;
        target(a, sx + Math.cos(b) * d * 1.15, sy + Math.sin(b) * d * 0.9);
      });
    });
    const rev = groups.get("review") ?? [];
    rev.forEach((a, i) => {
      const ang = (i / Math.max(1, rev.length)) * Math.PI * 2 + 0.3;
      target(a, cx + Math.cos(ang) * (rx + W * 0.17), cy + Math.sin(ang) * (ry + H * 0.17));
    });
  }

  const css = (n, el) => getComputedStyle(el).getPropertyValue(n).trim();
  const radius = (a) => (a.kind === "conductor" ? 6 : a.kind === "lead" || a.kind === "critic" || a.kind === "finisher" ? 5 : 3.6);

  function frame() {
    raf = 0;
    if (!canvas || !showField) return;
    const t = performance.now();
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const line = css("--line", canvas), ok = css("--live", canvas) || "#3d9a6a", bad = css("--danger", canvas) || "#b4443a", ink = css("--ink", canvas), soft = css("--ink-soft", canvas);
    place();
    let moving = false;
    for (const r of nodes.values()) {
      const dx = r.tx - r.x, dy = r.ty - r.y;
      if (Math.abs(dx) + Math.abs(dy) > 0.2) { r.x += dx * 0.14; r.y += dy * 0.14; moving = true; } else { r.x = r.tx; r.y = r.ty; }
    }
    // hairlines from each agent to its parent
    ctx.lineWidth = 1;
    ctx.strokeStyle = line;
    for (const a of part.agents) {
      const r = nodes.get(a.id), p = nodes.get(a.parent);
      if (!r || !p) continue;
      const k = Math.max(0, Math.min(1, (t - r.born) / 450));
      ctx.globalAlpha = Math.min(1, k) * (a.kind === "verifier" || a.kind === "red_team" || a.kind === "critic" ? 0.5 : 1);
      ctx.beginPath();
      ctx.moveTo(p.x, p.y);
      ctx.lineTo(p.x + (r.x - p.x) * k, p.y + (r.y - p.y) * k);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
    // a light travelling between two agents that talk
    if (!reduce) {
      const live_ = [];
      for (const m of part.pulses ?? []) {
        const k = (t - m.t) / 850;
        if (k >= 1.2) continue;
        live_.push(m);
        const A = nodes.get(m.from), B = nodes.get(m.to);
        if (!A || !B) continue;
        const e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * Math.min(k, 1) + 2, 2) / 2;
        const x = A.x + (B.x - A.x) * e, y = A.y + (B.y - A.y) * e;
        ctx.strokeStyle = BLUE;
        ctx.globalAlpha = Math.max(0, 0.28 * (1 - Math.abs(Math.min(k, 1) - 0.5) * 1.2));
        ctx.beginPath(); ctx.moveTo(A.x, A.y); ctx.lineTo(B.x, B.y); ctx.stroke();
        const g = ctx.createRadialGradient(x, y, 0, x, y, 9);
        g.addColorStop(0, "rgba(59,130,246,.9)");
        g.addColorStop(1, "rgba(59,130,246,0)");
        ctx.globalAlpha = k < 1 ? 1 : Math.max(0, 1 - (k - 1) / 0.2);
        ctx.fillStyle = g;
        ctx.beginPath(); ctx.arc(x, y, 9, 0, 7); ctx.fill();
        if (k >= 1 && !m.hit) { m.hit = true; if (B) B.flash = t; }
      }
      ctx.globalAlpha = 1;
      if (live_.length) moving = true;
    }
    for (const a of part.agents) {
      const r = nodes.get(a.id);
      if (!r) continue;
      const e = 1 - Math.pow(1 - Math.max(0, Math.min(1, (t - r.born) / 450)), 3);
      if (e < 1) moving = true;
      const rad = radius(a) * e;
      const col = a.state === "done" ? ok : a.state === "failed" ? bad : a.state === "work" ? BLUE : soft;
      if (a.state === "work" && !reduce) {
        const ph = (Math.sin(t / 380 + a.id.length * 7 + (+a.id.slice(1) || 0)) + 1) / 2;
        ctx.fillStyle = BLUE; ctx.globalAlpha = 0.14 + 0.14 * ph;
        ctx.beginPath(); ctx.arc(r.x, r.y, rad + 3 + ph * 3, 0, 7); ctx.fill();
        ctx.globalAlpha = 1;
        moving = true;
      }
      if (r.flash && t - r.flash < 500 && !reduce) {
        ctx.strokeStyle = BLUE; ctx.globalAlpha = 1 - (t - r.flash) / 500;
        ctx.beginPath(); ctx.arc(r.x, r.y, rad + 2 + (t - r.flash) / 60, 0, 7); ctx.stroke();
        ctx.globalAlpha = 1;
        moving = true;
      }
      ctx.globalAlpha = a.state === "wait" ? 0.55 : 1;
      ctx.fillStyle = col;
      ctx.beginPath(); ctx.arc(r.x, r.y, rad, 0, 7); ctx.fill();
      ctx.globalAlpha = 1;
      if (a.state === "done" && rad > 4) {
        ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.4;
        ctx.beginPath(); ctx.moveTo(r.x - rad * 0.4, r.y); ctx.lineTo(r.x - rad * 0.08, r.y + rad * 0.34); ctx.lineTo(r.x + rad * 0.45, r.y - rad * 0.3); ctx.stroke();
        ctx.lineWidth = 1;
      }
      if (hover === a.id) { ctx.strokeStyle = ink; ctx.beginPath(); ctx.arc(r.x, r.y, rad + 4, 0, 7); ctx.stroke(); }
    }
    if (running || moving || hover) raf = requestAnimationFrame(frame);
  }
  const kick = () => { if (!raf && showField) raf = requestAnimationFrame(frame); };

  // redraw when the agents change (the loop sleeps once a finished card has settled)
  $effect(() => {
    part.agents.length; part.agents.map((a) => a.state); part.pulses?.length; part.phase; showField;
    kick();
  });

  onMount(() => {
    size();
    const ro = new ResizeObserver(() => { size(); kick(); });
    if (canvas) ro.observe(canvas);
    return () => { ro.disconnect(); cancelAnimationFrame(raf); };
  });
  $effect(() => { if (showField && canvas) { size(); kick(); } });

  function near(e) {
    const rect = canvas.getBoundingClientRect(), x = e.clientX - rect.left, y = e.clientY - rect.top;
    let best = 14, id = null;
    for (const a of part.agents) {
      const r = nodes.get(a.id);
      if (!r) continue;
      const d = Math.hypot(r.x - x, r.y - y);
      if (d < best) { best = d; id = a.id; }
    }
    return { id, x, y };
  }
  function move(e) {
    const { id, x, y } = near(e);
    hover = id;
    const a = part.agents.find((q) => q.id === id);
    tip = a ? { on: true, x, y, text: KIND[a.kind] + (a.squad && a.kind !== "conductor" ? ` · ${a.squad}` : ""), sub: a.state === "done" ? "Done" : a.state === "failed" ? "Failed" : a.activity || (a.state === "wait" ? "Waiting" : "Working") } : { ...tip, on: false };
    kick();
  }
  function leave() { hover = null; tip = { ...tip, on: false }; kick(); }
  async function pick(e) {
    const { id } = near(e);
    const a = part.agents.find((q) => q.id === id);
    if (!a) return;
    picked = a;
    detail = null;
    if (part.run) {
      try { detail = await api(`/api/swarm/${part.run}/agents/${a.id}`); } catch { detail = { agent: null, entries: [], messages: [] }; }
    }
  }
  const close = () => (picked = null);
  const keydown = (e) => { if (e.key === "Escape" && picked) close(); };
</script>

<svelte:window onkeydown={keydown} />

<div class="sw" class:fin={!running} bind:this={box}>
  {#if !running}
    <button type="button" class="sw-line" aria-expanded={expanded} onclick={() => (expanded = !expanded)}>
      <span>{summary}</span>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6" /></svg>
    </button>
  {/if}
  {#if showField}
    <div class="sw-card" class:flat={!running}>
      <div class="sw-head">
        <b>Agent Swarm</b>
        <span class="sw-meta">
          {#if running}
            <i style="--c: #3b82f6">{working} working</i>{#if waiting}<i style="--c: var(--ink-soft)">{waiting} waiting</i>{/if}
          {/if}
          <i style="--c: var(--live)">{done} done</i>
          <span title={part.stats?.cap ? `of a $${part.stats.cap.toFixed(2)} cap` : ""}>{clock(elapsed)} · ${spent.toFixed(2)}</span>
        </span>
      </div>
      <div class="sw-rail" aria-label="Phase">
        {#each PHASES as p, i}<span class:done={!running ? true : i < part.phase} class:now={running && i === part.phase}>{p}</span>{/each}
      </div>
      <div class="sw-fw">
        <canvas bind:this={canvas} class="sw-field" aria-label="{agents.length} agents: {working} working, {done} done" onpointermove={move} onpointerleave={leave} onclick={pick}></canvas>
        {#if tip.on}<div class="sw-tip" style="left:{tip.x}px;top:{tip.y}px">{tip.text}<s>{tip.sub}</s></div>{/if}
      </div>
      {#if part.shots?.length}
        <div class="sw-shots">{#each part.shots as s (s.file)}<a href="/api/files/{s.file}" target="_blank" rel="noopener"><img src="/api/files/{s.file}" alt="A builder's screenshot" loading="lazy" /></a>{/each}</div>
      {/if}
    </div>
  {/if}
</div>

{#if picked}
  <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
  <div class="sw-scrim" onclick={close}>
    <div class="sw-sheet" role="dialog" aria-label="Agent details" tabindex="-1" onclick={(e) => e.stopPropagation()}>
      <header>
        <b>{KIND[picked.kind] ?? picked.kind}</b>
        {#if picked.squad}<span class="sw-meta">{picked.squad}</span>{/if}
        <span class="sw-pill {picked.state}">{picked.state === "wait" ? "waiting" : picked.state === "work" ? "working" : picked.state}</span>
        <button type="button" class="sw-x" aria-label="Close" onclick={close}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17" /></svg></button>
      </header>
      <section><h4>Task</h4><p>{detail?.agent?.task ?? picked.task}</p></section>
      {#if picked.activity && (picked.state === "work" || picked.state === "wait")}<section><h4>Doing now</h4><p>{picked.activity}</p></section>{/if}
      {#if picked.error || detail?.agent?.error}<section><h4>Failed</h4><p class="bad">{picked.error || detail.agent.error}</p></section>{/if}
      {#if detail?.agent?.result}<section><h4>Result</h4><p class="pre">{detail.agent.result}</p></section>{/if}
      {#if detail?.entries?.length}
        <section>
          <h4>Findings</h4>
          <ul>
            {#each detail.entries as e (e.id)}
              <li><span class="sw-tag">{e.kind}{e.status !== "open" ? ` · ${e.status}` : ""}</span> {e.text}{#if e.data?.url} <a href={e.data.url} target="_blank" rel="noopener noreferrer">source</a>{/if}</li>
            {/each}
          </ul>
        </section>
      {/if}
      {#if detail?.messages?.length}
        <section>
          <h4>Messages</h4>
          <ul>{#each detail.messages as m}<li><span class="sw-tag">{m.frm} to {m.to}</span> {m.text}</li>{/each}</ul>
        </section>
      {/if}
    </div>
  </div>
{/if}

<style>
  .sw { margin: 6px 0; }
  .sw-line {
    display: inline-flex; align-items: center; gap: 4px; padding: 4px 2px; border: 0; background: none; cursor: pointer;
    color: var(--ink-soft); font-size: 13px; border-radius: 8px;
  }
  .sw-line:hover { color: var(--ink); }
  .sw-line svg { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; transition: transform .18s; }
  .sw-line[aria-expanded="true"] svg { transform: rotate(90deg); }
  .sw-card {
    position: relative; background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 14px 16px 10px;
  }
  .sw-card.flat { margin-top: 4px; }
  .sw-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; flex-wrap: wrap; }
  .sw-head b { font-weight: 500; }
  .sw-meta { color: var(--ink-soft); font-size: 12.5px; font-variant-numeric: tabular-nums; display: inline-flex; gap: 14px; flex-wrap: wrap; }
  .sw-meta i { font-style: normal; display: inline-flex; align-items: center; gap: 6px; }
  .sw-meta i::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: var(--c); }
  .sw-rail { display: flex; gap: 6px; margin: 12px 0 0; font-size: 12.5px; color: var(--ink-soft); }
  .sw-rail span { flex: 1; padding-top: 8px; position: relative; transition: color .3s; min-width: 0; }
  .sw-rail span::before { content: ""; position: absolute; left: 0; right: 3px; top: 0; height: 2px; border-radius: 1px; background: var(--line); }
  .sw-rail span::after { content: ""; position: absolute; left: 0; top: 0; height: 2px; border-radius: 1px; background: #3b82f6; width: 0; transition: width .5s; }
  .sw-rail .done::after { width: calc(100% - 3px); }
  .sw-rail .now::after { width: calc(50% - 3px); animation: pulse 1.6s ease-in-out infinite; }
  .sw-rail .now, .sw-rail .done { color: var(--ink); }
  @keyframes pulse { 50% { opacity: .45; } }
  .sw-fw { position: relative; }
  .sw-field { display: block; width: 100%; height: 300px; margin-top: 6px; cursor: default; touch-action: manipulation; }
  .sw-tip {
    position: absolute; pointer-events: none; background: var(--ink); color: var(--card); font-size: 12px; padding: 5px 9px; border-radius: 8px;
    white-space: nowrap; transform: translate(-50%, -135%); max-width: 260px; overflow: hidden; text-overflow: ellipsis;
  }
  .sw-tip s { text-decoration: none; opacity: .65; margin-left: 6px; }
  .sw-shots { display: flex; gap: 8px; overflow-x: auto; padding: 8px 0 4px; }
  .sw-shots img { height: 72px; border-radius: 8px; border: 1px solid var(--line); display: block; }
  .sw-scrim { position: fixed; inset: 0; z-index: 60; background: rgba(0, 0, 0, 0.32); display: flex; align-items: flex-end; justify-content: center; }
  .sw-sheet {
    background: var(--card); border: 1px solid var(--line); width: min(560px, 100%); max-height: 80dvh; overflow: auto; padding: 14px 18px 20px;
    border-radius: 18px 18px 0 0; box-shadow: 0 -8px 30px rgba(0, 0, 0, 0.18);
  }
  @media (min-width: 700px) { .sw-scrim { align-items: center; } .sw-sheet { border-radius: 18px; } }
  .sw-sheet header { display: flex; align-items: center; gap: 10px; }
  .sw-sheet header .sw-x { margin-left: auto; border: 0; background: none; padding: 6px; color: var(--ink-soft); cursor: pointer; border-radius: 8px; }
  .sw-sheet header .sw-x:hover { color: var(--ink); }
  .sw-x svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; display: block; }
  .sw-pill { font-size: 11.5px; padding: 2px 9px; border-radius: 999px; background: var(--glass); color: var(--ink-soft); }
  .sw-pill.work { color: #3b82f6; } .sw-pill.done { color: var(--live); } .sw-pill.failed { color: var(--danger); }
  .sw-sheet h4 { margin: 14px 0 4px; font-size: 12px; font-weight: 500; letter-spacing: .04em; text-transform: uppercase; color: var(--ink-soft); }
  .sw-sheet p { margin: 0; font-size: 14px; white-space: pre-wrap; overflow-wrap: anywhere; }
  .sw-sheet .bad { color: var(--danger); }
  .sw-sheet .pre { max-height: 220px; overflow: auto; }
  .sw-sheet ul { margin: 0; padding-left: 16px; display: grid; gap: 6px; font-size: 13.5px; overflow-wrap: anywhere; }
  .sw-tag { font-size: 11px; color: var(--ink-soft); background: var(--glass); padding: 1px 7px; border-radius: 6px; margin-right: 4px; }
  @media (max-width: 600px) { .sw-field { height: 230px; } .sw-card { padding: 12px 12px 8px; } .sw-rail { font-size: 11.5px; } }
  @media (prefers-reduced-motion: reduce) { .sw-rail .now::after { animation: none; } }
</style>
