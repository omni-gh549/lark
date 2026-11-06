<script>
  import { api } from "./api.js";
  import { app } from "./store.svelte.js";

  const s = $derived(app.settings);
  const w = $derived(s?.swarm);
  let note = $state({ text: "", kind: "" });

  const GROUPS = [
    { title: "Scale and cost", help: "How big a run can get, and what it may spend. When the cap is near, squad leads stop spawning and the swarm wraps up with what it has.",
      keys: ["max_agents", "concurrency", "budget", "minutes", "max_depth"] },
    { title: "Workers", help: "How long each agent may work, and when a silent one is restarted.",
      keys: ["worker_rounds", "lead_rounds", "scouts", "stall"] },
    { title: "Checking", help: "Independent verifiers re-open sources, the red team attacks the draft, and the critic scores it against the plan. Gaps get targeted extra work.",
      keys: ["verifiers", "red_team", "revise_rounds"] },
    { title: "Messages", help: "Agents coordinate through the shared board first. These cap how much they may talk, because messages cost tokens.",
      keys: ["msg_agent", "msg_run"] },
    { title: "Sandbox", help: "Builders and analysts use the sandbox. Heavy builds are limited so the server stays responsive.", keys: ["builds"] },
    { title: "OpenCode Go", help: "OpenCode Go has usage caps that don't suit fan-out, so the swarm restricts itself there. OpenRouter is the better choice for it.",
      keys: ["go_concurrency", "go_agents"] },
  ];
  const TIERS = [["cheap", "Cheap"], ["balanced", "Balanced"], ["smart", "Smart"]];

  async function save(patch) {
    note = { text: "Saving…", kind: "" };
    try {
      app.settings = await api("/api/settings", { method: "PUT", body: { swarm: patch } });
      note = { text: "Saved.", kind: "good" };
    } catch (e) {
      note = { text: e.message, kind: "bad" };
    }
  }
  const num = (key, el) => {
    const v = Number(el.value);
    if (Number.isFinite(v) && v !== w[key]) save({ [key]: v });
    else el.value = w[key];
  };
  const saveRole = (id, el) => {
    const v = el.value.trim();
    if (v && v !== w.roles[id]) save({ roles: { [id]: v } });
    else el.value = w.roles[id];
  };
  function savePrice(tier, i, el) {
    const v = Number(el.value);
    if (!Number.isFinite(v) || v < 0) return void (el.value = w.prices[tier][i]);
    const next = [...w.prices[tier]];
    next[i] = v;
    save({ prices: { [tier]: next } });
  }
</script>

{#if w}
  <div class="group">
    <h2>About Agent Swarm</h2>
    <div class="rows">
      <div class="row">
        <p class="meta" style="margin:0">
          The fourth stop on the model slider. A conductor plans the job, squad leads split it into slices, many short-lived agents research in parallel,
          independent checkers verify the claims, and a red team and critic review the draft before the answer is written. It is slower and costs more than
          the other stops, so it is worth it for research, comparisons, decisions and designs. Everything below can be changed.
        </p>
      </div>
    </div>
  </div>

  {#each GROUPS as g}
    <div class="group">
      <h2>{g.title}</h2>
      <div class="rows">
        <div class="row">
          <p class="meta" style="margin:0">{g.help}</p>
          {#each g.keys as k}
            <div class="field">
              <label class="meta stop-label wide-label" for="sw-{k}">{w.meta.labels[k]}</label>
              <input id="sw-{k}" type="number" inputmode="decimal" step={k === "budget" ? "0.1" : "1"} min={w.meta.limits[k][0]} max={w.meta.limits[k][1]}
                value={w[k]} onchange={(e) => num(k, e.currentTarget)} />
            </div>
          {/each}
        </div>
      </div>
    </div>
  {/each}

  <div class="group">
    <h2>Models by role</h2>
    <div class="rows">
      <div class="row">
        <p class="meta" style="margin:0">
          Each role uses a stop from your model slider (cheap, balanced or smart), or any model name. Workers do most of the calls, so they default to Balanced;
          the conductor, critic, red team and the writer of the answer use Smart.
        </p>
        {#each w.meta.roles as r}
          <div class="field">
            <label class="meta stop-label wide-label" for="role-{r.id}">{r.label}</label>
            <input id="role-{r.id}" list="swarm-models" value={w.roles[r.id]} autocomplete="off" spellcheck="false" onchange={(e) => saveRole(r.id, e.currentTarget)} />
          </div>
        {/each}
        <datalist id="swarm-models">
          {#each TIERS as [id]}<option value={id}></option>{/each}
          {#each Object.values(s.tiers ?? {}) as m}<option value={m}></option>{/each}
        </datalist>
      </div>
    </div>
  </div>

  <div class="group">
    <h2>Price estimates</h2>
    <div class="rows">
      <div class="row">
        <p class="meta" style="margin:0">
          OpenRouter reports what each call cost, and the spend cap uses that. For other providers the swarm estimates from token counts using these figures
          (US dollars per million tokens), so set them to your models' prices.
        </p>
        {#each TIERS as [id, label]}
          <div class="field">
            <span class="meta stop-label wide-label">{label}</span>
            <input type="number" step="0.01" min="0" aria-label="{label} input price" value={w.prices[id][0]} onchange={(e) => savePrice(id, 0, e.currentTarget)} />
            <input type="number" step="0.01" min="0" aria-label="{label} output price" value={w.prices[id][1]} onchange={(e) => savePrice(id, 1, e.currentTarget)} />
          </div>
        {/each}
        <p class="meta" style="margin:0">Input price, then output price.</p>
      </div>
    </div>
  </div>

  <p class="status {note.kind}" role="status">{note.text}</p>
{/if}

<style>
  .wide-label { min-width: 220px; align-self: center; }
  .field input[type="number"] { max-width: 130px; }
  @media (max-width: 600px) { .wide-label { min-width: 0; flex: 1 1 100%; } .field input[type="number"] { max-width: none; } }
</style>
