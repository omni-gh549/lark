"""A fake OpenAI-compatible provider for tests."""
import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
GOOD = "sk-good-key-1234"


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


@app.post("/chat/completions")
async def chat(request: Request):
    if not authed(request):
        return JSONResponse({"error": {"message": "Invalid API key"}}, status_code=401)
    body = await request.json()
    last_msg = body["messages"][-1]
    last = last_msg["content"]

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
