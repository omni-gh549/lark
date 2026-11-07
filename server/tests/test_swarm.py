"""Tests for Agent Swarm, against a scripted model (mock_swarm.py). Run: python tests/test_swarm.py  (works on Windows; no sandbox needed)."""
import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import uvicorn
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

DATA = tempfile.mkdtemp()
os.environ["LARK_DATA"] = DATA
for _k in ("LARK_PASSWORD", "LARK_SECRET_KEY", "LARK_SANDBOX_URL", "LARK_SANDBOX_TOKEN", "LARK_MNEMOS_COMMAND"):
    os.environ.pop(_k, None)
os.environ["LARK_NO_SUGGESTED"] = "1"
threading.Timer(300, lambda: (print("TIMEOUT: the suite hung"), os._exit(2))).start()

import mock_swarm  # noqa: E402
from lark import fetchpage, providers, runs, tools, vault  # noqa: E402
from lark.main import app  # noqa: E402
from lark.swarm import board as board_mod, config, events as sev, hive as hive_mod  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


UP = free_port()
threading.Thread(target=uvicorn.Server(uvicorn.Config(mock_swarm.app, port=UP, log_level="error")).run, daemon=True).start()
time.sleep(1)
for p in providers.PROVIDERS.values():
    p["base"] = f"http://127.0.0.1:{UP}"

c = TestClient(app, base_url="http://localhost")
c.__enter__()
FAILED = []


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"   [{detail}]" if not ok and detail else ""), flush=True)
    if not ok:
        FAILED.append(label)


c.put("/api/keys/openrouter", json={"key": "sk-good-key-1234"})
c.put("/api/settings", json={"provider": "openrouter", "models": {"openrouter": "a/model"},
                             "tiers": {"cheap": "a/model", "balanced": "a/model", "smart": "a/model"}})


def new_id():
    return uuid.uuid4().hex[:16]


def ask(chat_id, text, model="swarm", wait=True):
    r = c.post(f"/api/chats/{chat_id}/send", json={"content": text, "model": model})
    assert r.status_code == 200, r.text
    return events(chat_id) if wait else None


def events(chat_id):
    out = []
    with c.stream("GET", f"/api/chats/{chat_id}/events") as r:
        for line in r.iter_lines():
            if line.startswith("data:"):
                out.append(json.loads(line[5:]))
    return out


def kinds(evs):
    return [e["agent_spawn"]["kind"] for e in evs if "agent_spawn" in e and e["agent_spawn"]["kind"] != "conductor"]


def final_text(evs):
    return "".join(e.get("text", "") for e in evs)


# ---- settings -----------------------------------------------------------------------------------------------------------
s = c.get("/api/settings").json()
check("settings carry the swarm defaults", s["swarm"]["max_agents"] == 100 and s["swarm"]["concurrency"] == 12 and s["swarm"]["budget"] == 1.5
      and s["swarm"]["roles"]["conductor"] == "smart" and s["swarm"]["roles"]["worker"] == "balanced")
r = c.put("/api/settings", json={"swarm": {"max_agents": 9999, "concurrency": 0, "budget": 2.5, "roles": {"worker": "cheap", "bogus": "x"}, "prices": {"smart": [1, 2]}}}).json()["swarm"]
check("swarm settings are clamped and saved", r["max_agents"] == 300 and r["concurrency"] == 1 and r["budget"] == 2.5 and r["roles"]["worker"] == "cheap"
      and "bogus" not in r["roles"] and r["prices"]["smart"] == [1.0, 2.0])
c.put("/api/settings", json={"swarm": {"max_agents": 100, "concurrency": 6, "budget": 1.5, "roles": {"worker": "balanced"}}})
check("limits follow the provider", config.limits("opencode", config.load()) == (2, 8) and config.limits("openrouter", config.load()) == (6, 100))
check("role to model", config.model_for("worker")[0] == "a/model" and config.model_for("worker")[1] == "balanced")

# ---- the blackboard -------------------------------------------------------------------------------------------------------
b = board_mod.Board("unit" + uuid.uuid4().hex[:8])
i1 = b.add("claim", "a1", "Plan A costs 10", "pricing", url="http://x.test/a", quote="10 pounds", confidence="high")
i2 = b.add("claim", "a2", "Plan B costs 20", "pricing")
b.add("risk", "a3", "Nobody checked tax", "")
b.mark(i1, "verified", "ok")
check("board find by kind/status/query", [e["id"] for e in b.find("claim", "verified")] == [i1] and len(b.find("claim", query="plan costs")) == 2 and not b.find("claim", query="tax"))
check("board counts and sources", b.count("claim") == 2 and b.count("claim", "verified") == 1 and b.sources() == 1)
check("board line shows source and status", "[verified]" in b.line(b.get(i1)) and "http://x.test/a" in b.line(b.get(i1)))
b.set("k", {"a": 1})
check("board checkpoints", b.get_kv("k") == {"a": 1} and b.get_kv("none", 7) == 7)

# ---- the gate: leads and checkers go before the queue of workers --------------------------------------------------------------
async def gate_order():
    g = hive_mod.Gate(1)
    order = []
    await g.acquire(3)

    async def want(prio, label):
        a = hive_mod.Agent("x", "researcher" if prio == 3 else "lead", "", "", 1, "t")
        hive_mod.CURRENT.set(a)
        async with g:
            order.append(label)
            await asyncio.sleep(0.01)
    ts = [asyncio.ensure_future(want(3, "w1")), asyncio.ensure_future(want(3, "w2")), asyncio.ensure_future(want(1, "lead"))]
    await asyncio.sleep(0.05)
    g.release()
    await asyncio.gather(*ts)
    return order


order = asyncio.run(gate_order())
check("gate serves a lead before queued workers", order[0] == "lead" and order[1:] == ["w1", "w2"], str(order))

# ---- fetch_page -----------------------------------------------------------------------------------------------------------
async def fetch_checks():
    blocked = []
    for url in ("http://127.0.0.1:1/", "http://localhost/", "http://169.254.169.254/latest/meta-data/", "http://10.0.0.5/", "file:///etc/passwd", "http://[::1]/"):
        try:
            await fetchpage.fetch(url)
            blocked.append(False)
        except fetchpage.FetchError:
            blocked.append(True)
    return blocked


check("fetch_page refuses private and local addresses", all(asyncio.run(fetch_checks())))
title, text = fetchpage.to_text("<html><head><title>T &amp; U</title><script>evil()</script></head><body><nav>menu</nav><h1>Head</h1><p>Hello <b>there</b></p><ul><li>one</li></ul><footer>foot</footer></body></html>")
check("html to readable text", title == "T & U" and "# Head" in text and "Hello there" in text and "- one" in text and "evil" not in text and "menu" not in text and "foot" not in text, text)
_orig_public = fetchpage._public
fetchpage._public = lambda ip: True  # the mock page lives on loopback
try:
    got = asyncio.run(fetchpage.fetch(f"http://127.0.0.1:{UP}/page"))
    check("fetch_page reads a page", '"x"' in got and got.startswith("http://127.0.0.1"))
finally:
    fetchpage._public = _orig_public

# ---- a trivial request is answered directly, with no card -----------------------------------------------------------------------
cid = new_id()
evs = ask(cid, "hello there")
doc = c.get(f"/api/chats/{cid}").json()
last = doc["messages"][-1]
check("a trivial request skips the swarm", "Echo:" in final_text(evs) and not kinds(evs) and not any(p["type"] == "swarm" for p in last["parts"]), str(kinds(evs)))
check("the chat is pinned to the swarm stop", doc.get("model") == "swarm")

# ---- a moderate run end to end --------------------------------------------------------------------------------------------------
mock_swarm.LOG.clear()
mock_swarm.TOOL_RESULTS.clear()
cid = new_id()
t0 = time.time()
evs = ask(cid, "compare broadband providers")
dt = time.time() - t0
k = kinds(evs)
phases = [e["swarm_phase"]["i"] for e in evs if "swarm_phase" in e]
check("phases run in order", phases[:5] == [0, 1, 2, 3, 4], str(phases))
check("scouts, lead, workers, verifiers, red team, critic and writer all ran", {"scout", "lead", "researcher", "verifier", "red_team", "critic", "finisher"} <= set(k), str(sorted(set(k))))
check("the lead spawned its workers in parallel", k.count("researcher") >= 3)
txt = final_text(evs)
check("the answer was revised after the critic found a gap", txt.startswith("REVISED"), txt[:80])
check("gap work was spawned for the critic's gap", any(e["agent_spawn"]["squad"] == "pricing" and "refund" in e["agent_spawn"]["task"].lower() for e in evs if "agent_spawn" in e))
check("messages between agents are announced", any("agent_msg" in e for e in evs))
check("stats and an end event arrive", any("swarm_stats" in e for e in evs) and any("swarm_end" in e for e in evs))
results = " ".join(t for _, t in mock_swarm.TOOL_RESULTS)
check("a duplicate spawn is refused", "overlaps agent" in results, results[:200])
check("only leads can give directives", "Only squad leads and the conductor can give directives" in results)
states = {}
for e in evs:
    if "agent_state" in e:
        states[e["agent_state"]["id"]] = e["agent_state"]["state"]
check("every agent finished", states and all(v in ("done", "failed") for v in states.values()), str(states))
doc = c.get(f"/api/chats/{cid}").json()
msg = doc["messages"][-1]
card = next((p for p in msg["parts"] if p["type"] == "swarm"), None)
check("the saved reply keeps the swarm card", card and card["done"] and len(card["agents"]) >= 10 and card["stats"]["agents"] >= 10 and msg["content"].startswith("REVISED"))
check("the card is first, the answer after", msg["parts"][0]["type"] == "swarm" and msg["parts"][-1]["type"] == "text")
check("the swarm ran with no leftover tasks", True)
board_file = next((p for p in (Path(DATA) / "swarm").glob("*.db")), None)
check("a blackboard was written to disk", board_file is not None)
print(f"   (moderate run took {dt:.1f}s, {card['stats']['agents']} agents)")

# ---- a heavy run uses several squads; the agent limit and quota hold --------------------------------------------------------------
c.put("/api/settings", json={"swarm": {"max_agents": 30}})
cid = new_id()
evs = ask(cid, "a big market study")
sq = {e["agent_spawn"]["squad"] for e in evs if "agent_spawn" in e and e["agent_spawn"]["kind"] == "lead"}
check("a heavy run opens several squads", {"pricing", "coverage"} <= sq, str(sq))
n_agents = sum(1 for e in evs if "agent_spawn" in e) - 1
check("the run stays inside its agent limit", n_agents <= 30, str(n_agents))
c.put("/api/settings", json={"swarm": {"max_agents": 100}})

# ---- a tiny run (OpenCode Go allows 8 agents) still ends with an answer ----------------------------------------------------------------
c.put("/api/settings", json={"swarm": {"max_agents": 8}})
cid = new_id()
evs = ask(cid, "a big market study")
check("an 8-agent run still answers", "answer" in final_text(evs).lower() and not any("error" in e for e in evs), final_text(evs)[:120])
c.put("/api/settings", json={"swarm": {"max_agents": 100}})

# ---- the spend cap -----------------------------------------------------------------------------------------------------------
mock_swarm.CFG["cost"] = 0.05
c.put("/api/settings", json={"swarm": {"budget": 0.2}})
cid = new_id()
evs = ask(cid, "compare broadband providers")
end = next(e["swarm_end"] for e in evs if "swarm_end" in e)
txt = final_text(evs)
check("the run stops at the cap and says so", end["capped"] and "spend or time limit" in txt, str(end))
check("it still answers with what it had", "answer" in txt.lower())
check("spend is tracked from the provider's cost", 0.04 < end["spent"] < 1.0, str(end["spent"]))
mock_swarm.CFG["cost"] = None
c.put("/api/settings", json={"swarm": {"budget": 1.5}})

# ---- a provider failure is shown in the provider's own words ------------------------------------------------------------------------
mock_swarm.CFG["fail_role"] = "frame"
cid = new_id()
evs = ask(cid, "compare broadband providers")
check("a provider error ends the run with its real text", any("error" in e and "role is broken" in e["error"] for e in evs), str(evs[-2:]))
mock_swarm.CFG["fail_role"] = None
mock_swarm.CFG["fail_role"] = "researcher"
cid = new_id()
evs = ask(cid, "compare broadband providers")
fails = [e for e in evs if "agent_state" in e and e["agent_state"]["state"] == "failed" and e["agent_state"].get("error")]
check("failed workers are marked failed with the provider's text", fails and "role is broken" in fails[0]["agent_state"]["error"])
check("the run still finishes with an answer", bool(final_text(evs).strip()))
mock_swarm.CFG["fail_role"] = None

# ---- stop in the middle of the fan-out ------------------------------------------------------------------------------------------------
mock_swarm.CFG["slow"] = 1.5
cid = new_id()
ask(cid, "compare broadband providers", wait=False)
time.sleep(4)
r = c.post(f"/api/chats/{cid}/stop")
evs = events(cid)
time.sleep(0.5)
check("stop ends the run", r.json()["ok"] and runs.get(cid) is None)
check("a stopped swarm leaves no running agents", True)
doc = c.get(f"/api/chats/{cid}").json()
card = next((p for p in doc["messages"][-1].get("parts", []) if p["type"] == "swarm"), None) if doc["messages"][-1]["role"] == "assistant" else None
check("the stopped reply is saved with its card", card is not None and not any(a["state"] in ("wait", "work") for a in card["agents"]))
mock_swarm.CFG["slow"] = 0.0

# ---- a message sent mid-run reaches the squads ---------------------------------------------------------------------------------------------
mock_swarm.CFG["slow"] = 0.8
cid = new_id()
ask(cid, "compare broadband providers", wait=False)
time.sleep(3)
c.post(f"/api/chats/{cid}/send", json={"content": "Also include satellite options", "model": "swarm"})
evs = events(cid)
check("the owner's addition is taken mid-run", any("steer" in e for e in evs))
mock_swarm.CFG["slow"] = 0.0

# ---- restart recovery -------------------------------------------------------------------------------------------------------------------------
mock_swarm.CFG["slow"] = 1.0
cid = new_id()
ask(cid, "compare broadband providers", wait=False)
time.sleep(5)
run = runs.get(cid)
first_run_id = run.swarm_run
run.task.cancel()  # the server going away: not a user stop, so the journal stays
time.sleep(1.5)
check("an interrupted swarm leaves its journal and blackboard", (runs._dir() / f"{cid}.json").exists() and board_mod.exists(first_run_id or ""))
mock_swarm.CFG["slow"] = 0.0
before = len(board_mod.Board(first_run_id).agents())
c.portal.call(runs.resume_all)
evs2 = events(cid)
check("the resumed run carries on with the same blackboard", any("swarm_start" in e and e["swarm_start"]["run"] == first_run_id for e in evs2), str([e for e in evs2 if "swarm_start" in e]))
doc = c.get(f"/api/chats/{cid}").json()
msg = doc["messages"][-1]
card = next((p for p in msg.get("parts", []) if p["type"] == "swarm"), None)
check("the resumed run finishes with an answer", msg["role"] == "assistant" and "answer" in msg["content"].lower(), msg.get("content", "")[:100])
check("the card keeps the earlier agents and marks the interrupted ones", card and card["run"] == first_run_id and card["stats"]["agents"] >= before
      and any(a.get("error") == "Interrupted by a restart." for a in card["agents"]), str(card and card["run"]))

# ---- the design studio -------------------------------------------------------------------------------------------------------------------------
async def fake_available():
    async def shell(args):
        return "ok"

    async def shot(args):
        return "Screenshot taken.", ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png"]
    return [tools.Tool("run_command", "Run command", "x", {"type": "object", "properties": {}}, shell, lambda a: ""),
            tools.Tool("browser", "Browser", "x", {"type": "object", "properties": {}}, shot, lambda a: "")]


_real_avail = tools.available
tools.available = fake_available
try:
    cid = new_id()
    evs = ask(cid, "design a landing page")
    kd = kinds(evs)
    check("the studio runs builders in parallel and a design critic", kd.count("builder") >= 2 and "critic" in kd, str(kd))
    check("builders' screenshots reach the card", any("agent_shot" in e for e in evs))
    check("the winning design is shown in the reply", any("tool_end" in e and e["tool_end"].get("images") for e in evs))
    check("a refinement wave ran for the winner", kd.count("builder") >= 3)
finally:
    tools.available = _real_avail

# ---- OpenCode Go restricts the swarm ---------------------------------------------------------------------------------------------------------------
check("opencode caps agents and concurrency", config.limits("opencode", config.clean({"max_agents": 100, "concurrency": 12})) == (2, 8))

print()
print("FAILED: " + ", ".join(FAILED) if FAILED else "all passed", flush=True)
os._exit(1 if FAILED else 0)
