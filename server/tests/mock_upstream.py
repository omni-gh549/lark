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
    last = body["messages"][-1]["content"]

    async def gen():
        yield ": PROCESSING\n\n"
        parts = ["**Bold** and `code`\n\n- one\n- two\n\n[link](https://example.com)"] if last == "md" else ["Echo: ", last]
        for part in parts:
            yield "data: " + json.dumps({"choices": [{"delta": {"content": part}}]}) + "\n\n"
        if last == "boom":
            yield "data: " + json.dumps({"error": {"message": "upstream exploded"}}) + "\n\n"
            return
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
