"""A fake OpenAI-compatible provider for tests."""
import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
GOOD = "sk-good-key-1234"
SEEN: dict = {}  # how many times each flaky prompt has been asked


def authed(request: Request) -> bool:
    return request.headers.get("authorization") == f"Bearer {GOOD}"


@app.get("/models")
async def models(request: Request):
    if not authed(request):
        return JSONResponse({"error": {"message": "Invalid API key"}}, status_code=401)
    return {"data": [{"id": "b/model"}, {"id": "a/model"}]}


@app.get("/key")
async def key(request: Request):
    if not authed(request):
        return JSONResponse({"error": {"message": "No auth credentials found", "code": 401}}, status_code=401)
    return {"data": {"label": "test"}}


@app.post("/embeddings")
async def embeddings(request: Request):
    if not authed(request):
        return JSONResponse({"error": {"message": "Invalid API key"}}, status_code=401)
    body = await request.json()
    groups = (("cat", "feline", "kitten"), ("sister", "sibling"))
    return {"data": [{"index": i, "embedding": [float(any(w in t.lower() for w in g)) for g in groups] + [0.1]}
                     for i, t in enumerate(body["input"])]}


@app.post("/chat/completions")
async def chat(request: Request):
    if not authed(request):
        return JSONResponse({"error": {"message": "Invalid API key"}}, status_code=401)
    body = await request.json()
    last_msg = body["messages"][-1]
    last = last_msg["content"]
    if isinstance(last, list):  # vision input: report how many images arrived
        n = sum(1 for p in last if p.get("type") == "image_url" and p["image_url"]["url"].startswith("data:image/"))
        last = f"vision {n} " + " ".join(p.get("text", "") for p in last if p.get("type") == "text")

    if last_msg["role"] == "user" and isinstance(last, str):
        SEEN[last] = SEEN.get(last, 0) + 1
        if last.startswith("flaky") and SEEN[last] <= 2:
            return JSONResponse({"error": {"message": "overloaded"}}, status_code=503)
        if last.startswith("down"):
            return JSONResponse({"error": {"message": "gone"}}, status_code=503)

    def call(name, args, i=0):
        chunks = [{"index": i, "id": f"call_{i + 1}", "type": "function", "function": {"name": name, "arguments": ""}},
                  {"index": i, "function": {"arguments": json.dumps(args)[:8]}},
                  {"index": i, "function": {"arguments": json.dumps(args)[8:]}}]
        return ["data: " + json.dumps({"choices": [{"delta": {"tool_calls": [c]}}]}) + "\n\n" for c in chunks]

    async def gen():
        yield ": PROCESSING\n\n"
        names = [t["function"]["name"] for t in body.get("tools", [])]
        if "subagent working for Lark" in body["messages"][0]["content"] and last_msg["role"] == "user":
            yield "data: " + json.dumps({"choices": [{"delta": {"content": "Sub result: " + last}}]}) + "\n\n"
            yield "data: [DONE]\n\n"
            return
        system = body["messages"][0]["content"]
        if "maintain long-term memory" in system:  # the background learner
            payload = json.loads(last)
            said = payload["latest_exchange"]["user"]
            ops = [{"op": "add", "text": "The user's cat is called Miso.", "kind": "person", "subject": "Miso", "importance": 4}] if "cat" in said else []
            for f in payload["related_memories"]:
                if "moved" in said and "Lisbon" in f["text"]:
                    ops.append({"op": "update", "id": f["id"], "text": "The user's sister Maya now lives in Porto."})
            out = json.dumps({"summary": "Talked about: " + said[:50], "ops": ops})
            yield "data: " + json.dumps({"choices": [{"delta": {"content": out}}]}) + "\n\n"
            yield "data: [DONE]\n\n"
            return
        if "You name chats" in system:  # chat titles
            users = [l.removeprefix("User: ") for l in last.split("\n") if l.startswith("User: ")]
            said = users[0] if not users[0].lower().startswith("hi") or len(users) == 1 else users[-1]
            out = "New chat" if said.lower().startswith("hi") else '"' + " ".join(said.split()[:3]).title() + '."'
            if "Current title:" in last and "shifted" in last:
                out = "Moved on entirely"
            yield "data: " + json.dumps({"choices": [{"delta": {"content": out}}]}) + "\n\n"
            yield "data: [DONE]\n\n"
            return
        if "decide whether" in system and "needs to hear about it" in system:  # contact triage
            payload = json.loads(last)
            said = payload["their_message"].lower()
            if "garbage" in said:
                out = "no idea"
            else:
                important = "lift" in said or any(n.lower().find("router") >= 0 and "router" in said for n in payload["owner_notes"])
                out = json.dumps({"notify": important, "summary": "Wants a lift or mentioned the router.", "needs_decision": False})
            yield "data: " + json.dumps({"choices": [{"delta": {"content": out}}]}) + "\n\n"
            yield "data: [DONE]\n\n"
            return
        if last.startswith("emptyreply") or (last.startswith("emptyonce") and SEEN.get(last, 0) == 1):
            yield "data: [DONE]\n\n"
            return
        if last.endswith("sysdump"):
            yield "data: " + json.dumps({"choices": [{"delta": {"content": system}}]}) + "\n\n"
            yield "data: [DONE]\n\n"
            return
        if last.startswith("remember ") and "remember" in names and last_msg["role"] == "user":
            for c in call("remember", {"text": last[9:]}):
                yield c
            yield "data: [DONE]\n\n"
            return
        if last.startswith("delegate ") and "subagent" in names and last_msg["role"] == "user":
            for i, t in enumerate(last[9:].split(",")):
                for c in call("subagent", {"task": t.strip()}, i):
                    yield c
            yield "data: [DONE]\n\n"
            return
        if last_msg["role"] == "tool":
            yield "data: " + json.dumps({"choices": [{"delta": {"content": "Tool said: " + last[:60].replace("\n", " ")}}]}) + "\n\n"
            yield "data: [DONE]\n\n"
            return
        if last.startswith("search ") and "web_search" in names:
            for c in call("web_search", {"query": last[7:]}):
                yield c
            yield "data: [DONE]\n\n"
            return
        if last == "slowbrowse" and "browser" in names:
            for c in call("browser", {"action": "goto", "url": "http://127.0.0.1:8899/index.html"}, 0):
                yield c
            for c in call("run_command", {"command": "sleep 5"}, 1):
                yield c
            yield "data: [DONE]\n\n"
            return
        if last.startswith("vision "):
            yield "data: " + json.dumps({"choices": [{"delta": {"content": "Saw: " + last}}]}) + "\n\n"
            yield "data: [DONE]\n\n"
            return
        if last.startswith("show ") and "show_image" in names:
            for c in call("show_image", {"path": last[5:]}):
                yield c
            yield "data: [DONE]\n\n"
            return
        if last.startswith("shot") and "browser" in names:
            for c in call("browser", {"action": "screenshot"}):
                yield c
            yield "data: [DONE]\n\n"
            return
        if last.startswith("run ") and "run_command" in names:
            for c in call("run_command", {"command": last[4:]}):
                yield c
            yield "data: [DONE]\n\n"
            return
        if last == "tools":
            yield "data: " + json.dumps({"choices": [{"delta": {"content": ",".join(names)}}]}) + "\n\n"
            yield "data: [DONE]\n\n"
            return
        parts = ["**Bold** and `code`\n\n- one\n- two\n\n[link](https://example.com)"] if last == "md" else ["Echo: ", last]
        for part in parts:
            yield "data: " + json.dumps({"choices": [{"delta": {"content": part}}]}) + "\n\n"
        if last == "boom":
            yield "data: " + json.dumps({"error": {"message": "upstream exploded"}}) + "\n\n"
            return
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/brave")
async def brave(request: Request, q: str = ""):
    if request.headers.get("x-subscription-token") != "brave-good-key":
        return JSONResponse({"error": {"detail": "bad token"}}, status_code=401)
    return {"web": {"results": [{"title": "<strong>Result</strong> for " + q, "url": "https://example.com/1", "description": "A &amp; B"}]}}
