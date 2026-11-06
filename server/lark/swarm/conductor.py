"""The Conductor: frames the job, fixes the squads, runs the phases, owns the final answer.

Phases (the card's rail): Plan (frame, scout, squads) -> Research (squad leads and their workers) -> Verify (independent checkers)
-> Review (draft, red team, critic, bounded revision) -> Finish. Each completed phase is checkpointed on the blackboard, so a
restart carries on from the last one."""
import asyncio
import math
import time
import uuid

from .. import agent as agent_mod, files, health, vault
from . import board as board_mod, config, events, prompts
from .hive import Agent, AgentFailure, Hive, as_text, extract_json

END = object()
CHUNK = 600

FRAME = """You are the Conductor of an agent swarm inside Lark, a personal assistant. A swarm is many agents that research and check things in parallel. It is slow and costs real money, so use it only where it buys something.

Read the owner's latest request (with the conversation and notes for context) and reply with JSON:
{"depth": "trivial" | "moderate" | "heavy",
 "kind": "research" | "design" | "other",
 "brief": "the job restated precisely: what the owner wants, for whom, any constraints, and what a great answer contains",
 "rubric": ["4 to 8 concrete, checkable things a great answer must do"],
 "scouts": ["2 to 4 short objectives for a quick breadth pass, each a different angle on what exists"]}

depth: trivial = one capable assistant answers it directly (conversation, a simple fact, a small edit, anything needing no outside research); moderate = needs several sources, or a small design, one workstream; heavy = broad, high-stakes or multi-part, with several independent workstreams. When unsure between two, pick the lighter one.
kind: design = the owner wants something built or designed (a page, an interface, a logo, a document layout) rather than researched; research = they want to know, compare or decide something."""

PLAN = """You are the Conductor of an agent swarm. Scouts have done a quick breadth pass; now fix the squads. Reply with JSON:
{"squads": [{"name": "short lowercase label", "objective": "what this squad must establish", "slices": ["independent slices of it"], "agents": 6}],
 "directions": [{"name": "short label", "idea": "one sentence"}]}

Rules:
- moderate job: exactly one squad. heavy job: between 3 and {max_squads} squads, each with a distinct objective that does not overlap the others.
- "agents" is how many workers that squad may use (it will usually use fewer). Total across squads at most {work}.
- Squads must split the work for real. Don't split a job that is really sequential, and don't create a squad that would repeat another.
- directions: only when the kind is design: {dirs} clearly different directions for the design (different concept, layout, mood), and leave squads empty. Otherwise leave directions empty."""

CRITIC = """You are the critic in an agent swarm. Score a draft answer against the rubric that was written before the work started, using the board. Reply with JSON:
{"scores": [{"check": "the rubric item", "score": 0-10, "note": "one short line"}],
 "gaps": [{"gap": "what is missing or wrong, specific enough to research", "squad": "which strand it belongs to"}],
 "material": true or false,
 "pass": true or false}
material: whether any gap would change what the owner decides or does. pass: true when every check scores 7 or more and there is nothing material. At most 6 gaps, most important first. Be strict but fair; don't invent problems."""


def request_text(history: list[dict]) -> str:
    for m in reversed(history):
        if m.get("role") == "user":
            t = as_text(m.get("content")).strip()
            if t.startswith("(Sent while you were working"):
                t = t.split("\n", 1)[-1]
            return t
    return ""


def transcript(history: list[dict], limit: int = 8) -> str:
    lines = []
    for m in history[-limit:]:
        t = as_text(m.get("content")).strip()
        if t:
            lines.append(f"{'Owner' if m['role'] == 'user' else 'Lark'}: {t[:2000]}")
    return "\n\n".join(lines)


def chunks(text: str):
    i = 0
    while i < len(text):
        j = min(len(text), i + CHUNK)
        if j < len(text):
            k = text.rfind("\n", i + CHUNK // 2, j)
            j = k + 1 if k > 0 else j
        yield text[i:j]
        i = j


def digest(hive: Hive, limit: int = 40000) -> str:
    """The board as text for the writer and the critic: squad reports, then claims by how far they were checked."""
    b = hive.board
    parts = []
    reports = b.find("report", limit=20)
    if reports:
        parts.append("SQUAD REPORTS\n" + "\n\n".join(f"[{e['squad'] or 'swarm'}]\n{e['text']}" for e in reports))
    for title, status in (("VERIFIED CLAIMS (independently checked)", "verified"), ("UNVERIFIED CLAIMS (not checked: say so)", "open"),
                          ("DISPUTED CLAIMS (sources disagree: say so, don't state as fact)", "disputed")):
        rows = b.find("claim", status, limit=150)
        if rows:
            parts.append(title + "\n" + "\n".join(b.line(e, 450) for e in rows))
    risks = b.find("risk", limit=30)
    if risks:
        parts.append("RISKS AND GAPS RAISED\n" + "\n".join(b.line(e, 450) for e in risks))
    files_ = b.find("file", limit=30)
    if files_:
        parts.append("FILES\n" + "\n".join(b.line(e) for e in files_))
    decisions = b.find("decision", limit=15)
    if decisions:
        parts.append("DECISIONS\n" + "\n".join(b.line(e) for e in decisions))
    text = "\n\n".join(parts) or "(the board is empty)"
    return text if len(text) <= limit else text[:limit] + "\n… [board cut for length]"


# ---- the run ---------------------------------------------------------------------------------------------------------

async def run(name: str, key: str, model: str, history: list[dict], memory_notes: str = "", steer=None, chat_id: str = "", resume: str | None = None):
    """Yields the same UI events as agent.run, plus the swarm_* and agent_* events for the card."""
    q: asyncio.Queue = asyncio.Queue()
    task = asyncio.ensure_future(_orchestrate(q.put_nowait, name, key, history, memory_notes, steer, chat_id, resume))
    task.add_done_callback(lambda _t: q.put_nowait(END))
    try:
        while True:
            ev = await q.get()
            if ev is END:
                break
            yield ev
        exc = task.exception() if not task.cancelled() else None
        if exc:
            health.record("swarm", f"run failed: {type(exc).__name__}")
            yield {"error": str(exc) if isinstance(exc, AgentFailure) else "The swarm hit a problem and stopped. Send that again, or try a lower setting on the slider."}
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def _orchestrate(emit, name, key, history, notes, steer, chat_id, resume):
    s = vault.load()
    cfg = config.load()
    run_id = resume if resume and board_mod.exists(resume) else uuid.uuid4().hex[:12]
    resumed = run_id == resume
    board_mod.prune()
    board = board_mod.Board(run_id)
    emit(events.start(run_id, time.time()))
    hive = Hive(run_id, chat_id, name, key, s, cfg, board, emit)
    hive.install()
    outcome: dict = {}
    try:
        await hive.load_tools()
        hive.helpers.append(asyncio.ensure_future(hive.watchdog()))
        if resumed:
            hive.adopt()
        await _phases(hive, history, notes, steer, outcome)
    finally:
        for h in hive.helpers:
            h.cancel()
        await asyncio.gather(*hive.helpers, return_exceptions=True)
        await hive.cancel_all()
        if not outcome.get("trivial"):
            c = hive.counts()
            emit(events.end({"elapsed": int(time.time() - hive.started), "spent": round(hive.budget.spent, 4), "cap": hive.budget.cap, "sources": board.sources(),
                             "agents": c["agents"], "failed": c["failed"], "capped": hive.capped, **outcome}))
        board.close()


async def _watch_steer(hive: Hive, steer):
    """A message the owner sends mid-run goes to the Conductor, which tells the squads."""
    if not steer:
        return
    while True:
        await asyncio.sleep(1.0)
        for m in steer():
            text = as_text(m.get("content")).replace("(Sent while you were working. Take it into account and carry on.)", "").strip()
            if not text:
                continue
            hive.updates.append(text)
            hive.board.add("decision", "owner", "The owner added while the swarm worked: " + text, "")
            hive.broadcast(hive.root, f"The owner added: {text}\nAdjust your work if this changes your task. If it is a new strand nobody covers, tell your lead.", "all")


async def _phases(hive: Hive, history: list[dict], notes: str, steer, outcome: dict):
    board, cfg, root = hive.board, hive.cfg, hive.root
    done = set(board.get_kv("done", []))

    async def phase(label, fn):
        if label in done:
            return
        await fn()
        done.add(label)
        board.set("done", sorted(done))

    ask = request_text(history)
    ctx = transcript(history)
    n_work = max(2, hive.max_agents - 4)
    n_scout = min(cfg["scouts"], hive.max_agents // 8)
    n_ver = min(cfg["verifiers"], max(0, hive.max_agents // 5))
    n_red = min(cfg["red_team"], hive.max_agents // 20)
    hive.set_phase(0)

    # ---- frame
    async def frame():
        user = f"Owner's latest request:\n{ask}\n\nConversation so far:\n{ctx}" + (f"\n\nWhat Lark remembers about the owner:\n{notes}" if notes else "")
        hive.set_state(root, "work", "Framing the job")
        got = await hive.ask_json(root, FRAME, user)  # a provider failure here ends the run with the provider's own words
        if not isinstance(got, dict):
            got = {"depth": "moderate", "kind": "research", "brief": ask, "rubric": ["Answers what was asked.", "Claims are backed by sources.", "Says what could not be established."], "scouts": []}
        board.set("frame", got)
        board.set("brief", got.get("brief") if isinstance(got.get("brief"), str) and got.get("brief").strip() else ask)

    await phase("frame", frame)
    fr = board.get_kv("frame", {})
    depth = fr.get("depth") if fr.get("depth") in ("trivial", "moderate", "heavy") else "moderate"
    if depth == "trivial":
        hive.set_state(root, "done", "")
        outcome["trivial"] = True
        hive.emit(events.end({"trivial": True}))
        smart = config.model_for("conductor", hive.settings, cfg)[0]
        board.set("done", ["frame", "trivial"])
        async for ev in agent_mod.run(hive.name, hive.key, smart, history, notes, steer=steer):
            hive.emit(ev)
        return
    hive.helpers.append(asyncio.ensure_future(_watch_steer(hive, steer)))
    rubric = [r for r in (fr.get("rubric") or []) if isinstance(r, str)][:8]
    board.set("brief", (board.get_kv("brief") or ask) + ("\n\nA great answer:\n" + "\n".join(f"- {r}" for r in rubric) if rubric else ""))
    design = fr.get("kind") == "design"

    # ---- scout
    scout_notes = ""

    async def scout():
        nonlocal scout_notes
        objs = [o for o in (fr.get("scouts") or []) if isinstance(o, str) and o.strip()][:n_scout]
        hive.set_state(root, "work", "Scouting")
        scouts = []
        for o in objs:
            try:
                scouts.append(await hive.spawn(root, "scout", f"Objective: {o}\nThis is a quick breadth pass for the job: {ask[:500]}", "scouts", rounds=6, check=False))
            except Exception:
                break
        await hive.wait(scouts)
        for sc in scouts:
            if sc.state == "done" and sc.result:
                scout_notes += f"- {sc.result[:700]}\n"
        board.set("scout_notes", scout_notes)

    await phase("scout", scout)
    scout_notes = board.get_kv("scout_notes", "")

    # ---- plan the squads (or the design directions)
    max_squads = 8 if depth == "heavy" else 1
    dirs = min(4, max(2, hive.max_agents // 12 + 2))

    async def plan():
        hive.set_state(root, "work", "Fixing the squads")
        system = PLAN.replace("{max_squads}", str(max_squads)).replace("{work}", str(max(2, n_work - n_ver - n_red - max_squads))).replace("{dirs}", str(dirs))
        user = f"The job:\n{board.get_kv('brief')}\n\nKind: {fr.get('kind')}  Depth: {depth}\n\nWhat the scouts found:\n{scout_notes or '(nothing)'}"
        got = None
        try:
            got = await hive.ask_json(root, system, user)
        except AgentFailure as e:
            hive.last_error = str(e)
        got = got if isinstance(got, dict) else {}
        squads = []
        for sq in (got.get("squads") or []):
            if isinstance(sq, dict) and isinstance(sq.get("objective"), str) and sq["objective"].strip():
                nm = "".join(c for c in str(sq.get("name") or f"squad{len(squads) + 1}").lower().replace(" ", "-") if c.isalnum() or c == "-")[:24] or f"squad{len(squads) + 1}"
                if any(x["name"] == nm for x in squads):
                    nm += str(len(squads) + 1)
                n = sq.get("agents")
                squads.append({"name": nm, "objective": sq["objective"].strip()[:600], "slices": [x for x in (sq.get("slices") or []) if isinstance(x, str)][:8],
                               "agents": n if isinstance(n, int) and n > 0 else 5})
        squads = squads[:max_squads]
        if not squads and not design:
            squads = [{"name": "research", "objective": ask[:600], "slices": [], "agents": 5}]
        total = sum(x["agents"] for x in squads)
        room = max(1, n_work - n_ver - n_red - len(squads) - n_scout) if not design else 0
        for x in squads:  # fit the squads inside the run's agent limit, keeping their proportions
            x["agents"] = max(2, int(x["agents"] * min(1, room / max(1, total))))
        directions = [d for d in (got.get("directions") or []) if isinstance(d, dict) and d.get("idea")][:dirs]
        if design and not directions:
            directions = [{"name": "direction " + str(i + 1), "idea": ask[:300]} for i in range(min(3, dirs))]
        board.set("plan", {"squads": squads, "directions": directions})

    await phase("plan", plan)
    pl = board.get_kv("plan", {"squads": [], "directions": []})
    hive.reserve = n_ver + n_red + 3

    # ---- research (or the design studio)
    hive.set_phase(1)
    if design:
        await phase("research", lambda: _studio(hive, pl["directions"], ask))
    else:
        await phase("research", lambda: _research(hive, pl["squads"], scout_notes))
    hive.reserve = 0
    hive.set_state(root, "work", "Checking the work")

    # ---- verify
    hive.set_phase(2)
    if not design:
        await phase("verify", lambda: _verify(hive, n_ver, 0))

    # ---- review: draft, attack, score, revise
    hive.set_phase(3)
    draft = board.get_kv("draft", "")
    if "review" not in done:
        draft = await _review(hive, ask, rubric, n_red, design)
        board.set("draft", draft)
        done.add("review")
        board.set("done", sorted(done))

    # ---- finish
    hive.set_phase(4)
    hive.set_state(root, "work", "Writing the answer")
    final = draft.strip() or ("The swarm couldn't put an answer together" + (f": {hive.last_error}" if hive.last_error else " from what it found. Try again, or ask with a lower setting on the slider."))
    if hive.capped:
        final += "\n\n(The swarm stopped at its spend or time limit, so this is what it had by then.)"
    if design:
        for ev in hive.design_images():
            hive.emit(ev)
    for part in chunks(final):
        hive.emit({"text": part})
    hive.set_state(root, "done", "")
    hive.emit({"done": True})
    outcome["answered"] = True


# ---- research --------------------------------------------------------------------------------------------------------

async def _research(hive: Hive, squads: list[dict], scout_notes: str):
    board = hive.board
    leads = []
    for sq in squads:
        have = board.find(squad=sq["name"], limit=1)
        if have and board.find("report", squad=sq["name"], limit=1):
            continue  # finished before a restart
        slices = "\n".join(f"- {x}" for x in sq["slices"]) or "(work out the slices yourself)"
        carry = ""
        if have:
            carry = f"\nThe board already holds {len(board.find(squad=sq['name'], limit=500))} entries from this squad (the server restarted). Read them first and carry on; don't repeat them."
        task = (f"Squad: {sq['name']}\nObjective: {sq['objective']}\nSuggested slices:\n{slices}\n"
                f"You may use up to {sq['agents']} agents in total, counting any sub-squads.{carry}\n"
                + (f"\nWhat the scouts found (a rough landscape, may be wrong):\n{scout_notes[:2500]}" if scout_notes else ""))
        hive.quota[sq["name"]] = sq["agents"]
        try:
            leads.append(await hive.spawn(hive.root, "lead", task, sq["name"], check=False))
        except Exception as e:
            health.record("swarm", f"could not start a squad: {type(e).__name__}")
    hive.set_state(hive.root, "work", "Squads at work")
    await hive.wait(leads)
    for ld in leads:
        text = ld.result.strip()
        if ld.state != "done" or not text:
            rows = board.find("claim", squad=ld.squad, limit=30)
            text = ("The squad lead did not report" + (f" ({ld.error})" if ld.error else "") + ". Its workers' claims:\n" + "\n".join(board.line(e) for e in rows)) if rows else ""
        if text:
            board.add("report", ld.id, text, ld.squad)


# ---- verification ----------------------------------------------------------------------------------------------------

async def _verify(hive: Hive, n_ver: int, since: int) -> None:
    board = hive.board
    claims = board.find("claim", "open", since=since, limit=400)
    if not claims or n_ver <= 0 or hive.level() != "ok":
        return
    conf = board_mod.CONF
    claims.sort(key=lambda e: (bool(e["data"].get("url")), conf.get(e["data"].get("confidence", "med"), 1)))  # unsourced and low-confidence first
    per = 3
    batch = claims[: n_ver * per]
    groups = [batch[i:i + per] for i in range(0, len(batch), per)]
    vs = []
    for g in groups:
        task = "Check these claims (open each source yourself):\n" + "\n".join(board.line(e, 600) for e in g)
        try:
            vs.append(await hive.spawn(hive.root, "verifier", task, "verification", rounds=8, check=False))
        except Exception:
            break
    hive.set_state(hive.root, "work", "Checking the work")
    await hive.wait(vs)


# ---- review ----------------------------------------------------------------------------------------------------------

DRAFT_SYSTEM = prompts.FINISHER + (" This is the draft that will be reviewed and then sent, so write the real answer. "
                                   "If the owner asked for a file or a design, say where it is and what it is.")


async def _review(hive: Hive, ask: str, rubric: list[str], n_red: int, design: bool) -> str:
    board, cfg = hive.board, hive.cfg
    updates = ("\n\nThe owner added while the swarm worked (it overrides the original request where they differ):\n" + "\n".join(hive.updates)) if hive.updates else ""
    brief = board.get_kv("brief", ask)

    async def write(feedback: str = "", previous: str = "") -> str:
        user = (f"The owner's request:\n{ask}{updates}\n\nThe brief the swarm worked to:\n{brief}\n\nTHE BOARD\n{digest(hive)}"
                + (f"\n\nYour previous draft:\n{previous}\n\nReviewers found these problems. Fix what is real, using the board; "
                   f"don't add claims the board doesn't support:\n{feedback}" if previous else ""))

        async def runner(a: Agent) -> str:
            hive.set_state(a, "work", "Drafting the answer")
            return (await hive.complete(a, DRAFT_SYSTEM, user)).strip()
        a = await hive.spawn(hive.root, "finisher", "Write the answer from the board", "finish", role="finisher", runner=runner, check=False)
        await a.done.wait()
        if a.state != "done" or not a.result:
            raise AgentFailure(a.error or "The writer failed.")
        return a.result

    try:
        draft = await write()
    except AgentFailure as e:
        health.record("swarm", "could not write the draft")
        return ""
    for rnd in range(cfg["revise_rounds"] + 1):
        findings = ""
        if rnd == 0 and n_red > 0 and hive.level() == "ok":
            angles = ["factual accuracy: does each claim in the draft follow from its source, and do the numbers add up?",
                      "what is missing: options, counter-arguments, caveats or recent developments that would change the reader's decision",
                      "logic and recommendations: does the conclusion follow, and is anything overconfident?",
                      "sources: are the sources good and current, and is anything cited that the board does not support?",
                      "the owner's request: does the draft actually answer what was asked, in the form asked?",
                      "the other side: argue against the draft's main conclusion as hard as honestly possible"]
            reds = []
            for i in range(n_red):
                try:
                    reds.append(await hive.spawn(hive.root, "red_team", f"Attack this draft. Your angle: {angles[i % len(angles)]}\n\nThe owner's request: {ask}\n\nDRAFT:\n{draft}",
                                                 "review", rounds=8, check=False))
                except Exception:
                    break
            hive.set_state(hive.root, "work", "Reviewers at work")
            await hive.wait(reds)
        risks = board.find("risk", limit=30)
        findings = "\n".join(board.line(e, 500) for e in risks)
        verdict = await _critique(hive, ask, rubric, draft, findings)
        board.add("decision", "critic", "Critic: " + ("pass" if verdict.get("pass") else "gaps: " + "; ".join(g.get("gap", "")[:120] for g in verdict.get("gaps", []))), "review")
        gaps = [g for g in (verdict.get("gaps") or []) if isinstance(g, dict) and g.get("gap")]
        if verdict.get("pass") or not (verdict.get("material") and gaps) or rnd >= cfg["revise_rounds"] or hive.level() != "ok":
            if gaps and not verdict.get("pass") and findings:
                try:
                    draft = await write("\n".join(f"- {g['gap']}" for g in gaps) + "\n" + findings, draft)
                except AgentFailure:
                    pass
            break
        # revise: targeted work on the gaps, check the new claims, rewrite
        mark = (board.db.execute("select coalesce(max(id),0) from entries").fetchone()[0])
        hive.set_state(hive.root, "work", "Closing the gaps")
        workers = []
        for g in gaps[: min(6, max(1, hive.max_agents - len(hive.agents)))]:
            try:
                workers.append(await hive.spawn(hive.root, "researcher", f"Objective: close this gap in the answer: {g['gap']}\nYour slice: only this gap.\nOutput: claims on the board with sources, and a short result.",
                                                str(g.get("squad") or "revision")[:24], check=False))
            except Exception:
                break
        await hive.wait(workers)
        await _verify(hive, max(1, min(cfg["verifiers"], 4)), mark)
        fb = "\n".join(f"- {g['gap']}" for g in gaps) + ("\n" + findings if findings else "")
        try:
            draft = await write(fb, draft)
        except AgentFailure:
            break
    return draft


async def _critique(hive: Hive, ask: str, rubric: list[str], draft: str, findings: str) -> dict:
    out: dict = {}

    async def runner(a: Agent) -> str:
        hive.set_state(a, "work", "Scoring the draft")
        user = (f"The owner's request:\n{ask}\n\nRubric:\n" + "\n".join(f"- {r}" for r in rubric) + f"\n\nDRAFT:\n{draft}\n\nRisks the red team and others raised:\n{findings or '(none)'}\n\n"
                f"Claim counts: {hive.board.count('claim', 'verified')} verified, {hive.board.count('claim', 'open')} unverified, {hive.board.count('claim', 'disputed')} disputed.")
        got = await hive.ask_json(a, CRITIC, user)
        out.update(got if isinstance(got, dict) else {})
        return "scored"
    try:
        a = await hive.spawn(hive.root, "critic", "Score the draft against the rubric", "review", role="critic", runner=runner, check=False)
        await a.done.wait()
    except Exception:
        pass
    return out or {"pass": True}


# ---- design studio ---------------------------------------------------------------------------------------------------

STUDIO_TASK = """Objective: design and build this, as a real working result, not a sketch: {ask}
Your direction (stay with it; other builders are trying other directions): {name} - {idea}
Steps: write the files in your folder (start with index.html; one self-contained page unless the job needs more). Serve your folder in the background with
`cd <your folder> && nohup python3 -m http.server {port} >/dev/null 2>&1 &`, open http://127.0.0.1:{port}/ with the browser tool and take a screenshot. Look at it.
Fix what is weak, take a final screenshot. Then stop the server (kill the python3 http.server you started). Record the main file as a file entry on the board.
Output: the paths of what you made and one line on what is good and what is weak about it."""


async def _studio(hive: Hive, directions: list[dict], ask: str):
    """Several builders try different directions in parallel, a vision critic compares them, the best is refined by a second wave."""
    board = hive.board
    hive.set_state(hive.root, "work", "Builders at work")
    builders = []
    for i, d in enumerate(directions):
        task = STUDIO_TASK.format(ask=ask[:800], name=d.get("name", f"direction {i + 1}"), idea=d.get("idea", "")[:400], port=8800 + i)
        try:
            builders.append(await hive.spawn(hive.root, "builder", task, "studio", rounds=24, check=False))
        except Exception:
            break
    await hive.wait(builders)
    shots = [(b, b.images[-1]) for b in builders if b.images and b.state == "done"]
    if not shots:
        return
    winner = shots[0][0]
    notes = ""

    async def runner(a: Agent) -> str:
        nonlocal winner, notes
        hive.set_state(a, "work", "Comparing the designs")
        content = [{"type": "text", "text": f"The brief: {ask}\n\nBelow are the builders' final screenshots, in order. Compare them with the brief and with each other.\n" +
                    "\n".join(f"{i + 1}. agent {b.id}: {b.result[:400]}" for i, (b, _) in enumerate(shots)) +
                    '\n\nReply with JSON: {"winner": 1-based number, "why": "...", "refine": "specific changes that would make the winner better"}'}]
        for _, f in shots:
            u = files.data_url(f)
            if u:
                content.append({"type": "image_url", "image_url": {"url": u}})
        got = await hive.ask_json(a, "You are a design critic with an exacting eye. Judge layout, hierarchy, typography, colour, polish and fit to the brief.", content)
        if isinstance(got, dict):
            n = got.get("winner")
            if isinstance(n, int) and 1 <= n <= len(shots):
                winner = shots[n - 1][0]
            notes = str(got.get("refine") or "")[:1500]
            board.add("decision", a.id, f"Design critic picked {winner.id}: {got.get('why', '')}", "studio")
        return "compared"
    try:
        c = await hive.spawn(hive.root, "critic", "Compare the designs", "studio", role="critic", runner=runner, check=False)
        await c.done.wait()
    except Exception:
        pass
    if notes and hive.level() == "ok":
        task = (f"Objective: refine the winning design. Copy /home/lark/swarm/{hive.run_id}/{winner.id}/ into your own folder first, then make these changes: {notes}\n"
                f"Serve, screenshot and check as the other builders did (port 8810). Output: paths and what changed.")
        try:
            r = await hive.spawn(hive.root, "builder", task, "studio", rounds=18, check=False)
            await r.done.wait()
            if r.state == "done" and r.images:
                winner = r
        except Exception:
            pass
    board.set("winner", winner.id)
    board.add("decision", "swarm", f"Winning design: agent {winner.id}; files in /home/lark/swarm/{hive.run_id}/{winner.id}/", "studio")
