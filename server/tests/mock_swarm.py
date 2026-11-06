"""A scripted OpenAI-compatible model for the swarm tests. It plays every role (conductor, scouts, squad leads, workers, verifiers,
red team, critic, writer) by reading the system prompt, and decides each agent's next step from the conversation so far."""
import asyncio
import json
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
GOOD = "sk-good-key-1234"
LOG: list = []          # (role, task) for every call
TOOL_RESULTS: list = []  # (role, tool result text) so tests can see what agents were told
CFG = {"slow": 0.0, "cost": None, "fail_role": None, "frame": None, "fan": 0, "squads": 2}


def role_of(system: str) -> str:
    for marker, role in (("Conductor of an agent swarm inside Lark", "frame"), ("Scouts have done a quick breadth pass", "plan"),
                         ("critic in an agent swarm", "critic"), ("design critic", "design"), ("You lead a squad", "lead"),
                         ("independent verifier", "verifier"), ("You are the red team", "red"), ("You are a scout", "scout"),
                         ("You build things in the sandbox", "builder"), ("You research.", "researcher"), ("You write the final answer", "draft"),
                         ("You maintain long-term memory", "memory"), ("You name chats", "title")):
        if marker in system:
            return role
    return "other"


def sse(obj):
    return "data: " + json.dumps(obj) + "\n\n"


def text(s):
    return [sse({"choices": [{"delta": {"content": s}}]})]


def calls(*cs):
    out = []
    for i, (name, args) in enumerate(cs):
        a = json.dumps(args)
        out += [sse({"choices": [{"delta": {"tool_calls": [{"index": i, "id": f"c{i}-{abs(hash(a)) % 10**6}", "type": "function", "function": {"name": name, "arguments": ""}}]}}]}),
                sse({"choices": [{"delta": {"tool_calls": [{"index": i, "function": {"arguments": a}}]}}]})]
    return out


def steps_done(messages) -> int:
    return sum(1 for m in messages if m["role"] == "assistant" and m.get("tool_calls"))


def last_tool_text(messages) -> str:
    return "\n".join(m["content"] for m in messages if m["role"] == "tool")


def user_task(messages) -> str:
    return next((m["content"] for m in messages if m["role"] == "user"), "")


def claim_ids(task: str):
    return [int(x) for x in re.findall(r"#(\d+) claim", task)]


SLICES = ["pricing tiers", "coverage maps", "customer reviews", "contract terms", "install times", "router hardware", "outage history", "support quality"]
SQUADS = ["pricing", "coverage", "reviews", "contracts", "support"]


def script(role: str, body: dict):
    msgs = body["messages"]
    system = msgs[0]["content"]
    task = user_task(msgs)
    n = steps_done(msgs)
    tool_names = [t["function"]["name"] for t in body.get("tools") or []]
    LOG.append((role, task[:120]))
    for m in msgs:
        if m["role"] == "tool":
            TOOL_RESULTS.append((role, m["content"]))
    if CFG["fail_role"] == role:
        return JSONResponse({"error": {"message": "role is broken"}}, status_code=400)

    if role == "frame":
        want = CFG["frame"] or ("trivial" if "hello" in task.lower() else "heavy" if "big" in task.lower() else "design" if "design" in task.lower() else "moderate")
        kind = "design" if want == "design" else "research"
        depth = "moderate" if want == "design" else want
        return text(json.dumps({"depth": depth, "kind": kind, "brief": "Brief: " + task[:80], "rubric": ["Covers pricing", "Cites sources", "States gaps"],
                                "scouts": ["landscape of providers", "recent changes"]}))
    if role == "plan":
        heavy = "Depth: heavy" in task
        design = "Kind: design" in task
        if design:
            return text(json.dumps({"squads": [], "directions": [{"name": "calm", "idea": "quiet and airy"}, {"name": "bold", "idea": "loud and high contrast"}]}))
        sq = [{"name": "pricing", "objective": "Establish prices", "slices": SLICES[:2], "agents": 4}]
        if heavy:
            sq = [{"name": SQUADS[i], "objective": f"Establish {SQUADS[i]}", "slices": SLICES[:2], "agents": max(4, CFG["fan"])} for i in range(CFG["squads"])]
        return text(json.dumps({"squads": sq}))
    if role == "scout":
        if n == 0:
            return calls(("board_write", {"kind": "claim", "text": "Scout saw providers exist", "url": "http://example.com/s", "quote": "providers"}))
        return text("Landscape: providers, prices, coverage.")
    if role == "lead":
        if n == 0:
            fan = CFG["fan"] or (3 if "Squad: pricing" in task else 2)
            sq = re.search(r"Squad: (\w+)", task)
            sq = sq.group(1) if sq else "x"
            return calls(*[("spawn", {"kind": "researcher", "objective": f"Find out about {sq} {s}", "slice": f"{sq} {s}"}) for s in SLICES[:fan]])
        if n == 1:
            sq = re.search(r"Squad: (\w+)", task)
            first = f"{sq.group(1) if sq else 'x'} {SLICES[0]}"
            return calls(("spawn", {"kind": "researcher", "objective": f"Find out about {first}", "slice": first}), ("collect", {"wait_seconds": 60}))
        if n == 2:
            return calls(("collect", {"wait_seconds": 60}))
        return text(f"Squad report for {task.split(chr(10))[0]}: findings in claims; gaps: none.")
    if role == "researcher":
        s = re.search(r"Your slice \(stay inside it\): ([^\n]+)", task)
        sl = s.group(1) if s else "x"
        if "Find out" not in task and "close this gap" not in task:
            return text("nothing")
        if n == 0:
            return calls(("board_write", {"kind": "claim", "text": f"Fact about {sl}", "url": f"http://example.com/{sl.replace(' ', '-')}", "quote": "q", "confidence": "low"}),
                         ("send", {"to": "lead", "text": f"working on {sl}", "kind": "info"}),
                         ("send", {"to": "lead", "text": "bad directive", "kind": "directive"}))
        return text(f"Found facts about {sl}.")
    if role == "verifier":
        ids = claim_ids(task)
        if n == 0 and ids:
            return calls(*[("board_mark", {"id": i, "status": "verified", "note": "source says so"}) for i in ids])
        return text("Checked.")
    if role == "red":
        if n == 0:
            return calls(("board_write", {"kind": "risk", "text": "The draft lacks refund terms", "confidence": "high"}))
        return text("One problem: refunds.")
    if role == "critic":
        draft = task.split("DRAFT:\n", 1)[-1]
        if "REVISED" in draft:
            return text(json.dumps({"scores": [], "gaps": [], "material": False, "pass": True}))
        return text(json.dumps({"scores": [{"check": "Covers pricing", "score": 5, "note": "thin"}], "gaps": [{"gap": "Cover refund terms", "squad": "pricing"}], "material": True, "pass": False}))
    if role == "draft":
        return text("REVISED answer from the board." if "Your previous draft" in task else "Draft answer from the board.")
    if role == "builder":
        if n == 0:
            return calls(("run_command", {"command": "echo hi"}), ("browser", {"action": "screenshot"}))
        return text("Built /home/lark/swarm/x/index.html. Good: clean. Weak: plain.")
    if role == "design":
        return text(json.dumps({"winner": 2, "why": "bolder", "refine": "bigger headline"}))
    if role == "title":
        return text("Swarm test")
    if role == "memory":
        return text(json.dumps({"summary": "x", "ops": []}))
    # anything else (the plain assistant, background jobs)
    last = msgs[-1]["content"] if isinstance(msgs[-1]["content"], str) else ""
    return text("Echo: " + last[:60])


@app.get("/models")
async def models():
    return {"data": [{"id": "a/model"}]}


@app.post("/chat/completions")
async def chat(request: Request):
    if request.headers.get("authorization") != f"Bearer {GOOD}":
        return JSONResponse({"error": {"message": "Invalid API key"}}, status_code=401)
    body = await request.json()
    role = role_of(body["messages"][0]["content"] if body["messages"][0]["role"] == "system" else "")
    out = script(role, body)
    if isinstance(out, JSONResponse):
        return out

    async def gen():
        if CFG["slow"] and role in ("researcher", "lead", "verifier"):
            await asyncio.sleep(CFG["slow"])
        for line in out:
            yield line
        if CFG["cost"] is not None:
            yield sse({"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": CFG["cost"]}})
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/page")
async def page():
    return JSONResponse({"x": 1})
