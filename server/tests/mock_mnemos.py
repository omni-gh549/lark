"""A stand-in for `mnemos serve` for the test suite: the same MCP stdio protocol and tool names, a plain JSON file for memory.

Run as:  python mock_mnemos.py serve
It keeps its memories in $HOME/.mnemos/lark.json and logs every tool call to $HOME/.mnemos/calls.jsonl, so tests can see
where Lark pointed it and what it was asked.
"""
import json
import os
import sys
from pathlib import Path

HOME = Path(os.environ.get("HOME", ".")) / ".mnemos"
HOME.mkdir(parents=True, exist_ok=True)
STORE = HOME / "lark.json"
CALLS = HOME / "calls.jsonl"
if os.environ.get("MOCK_MNEMOS_FAIL_START"):
    sys.exit(3)

TOOLS = {
    "mnemos_context": {"query": {"type": "string"}, "max_results": {"type": "integer"}},
    "mnemos_handoff": {"text": {"type": "string"}, "signed_as": {"type": "string"}},
    "mnemos_journal": {"text": {"type": "string"}, "signed_as": {"type": "string"}, "mood": {"type": "string"}},
    "mnemos_note": {"text": {"type": "string"}, "signed_as": {"type": "string"}},
    "mnemos_page": {},
    "mnemos_capture": {"content": {"type": "string"}, "signed_as": {"type": "string"}, "standing": {"type": "boolean"}, "shared": {"type": "boolean"}},
    "mnemos_recall": {"query": {"type": "string"}, "max_results": {"type": "integer"}, "standing": {"type": "boolean"}},
    "mnemos_correct": {"correction": {"type": "string"}, "target_id": {"type": "string"}, "signed_as": {"type": "string"}},
    "mnemos_maintain": {"deep": {"type": "boolean"}},
    "mnemos_reflect": {"target_id": {"type": "string"}, "text": {"type": "string"}, "signed_as": {"type": "string"}},
    "mnemos_introduce": {"agent_model": {"type": "string"}, "agent_name": {"type": "string"}},
    "mnemos_health": {},
}
REQUIRED = {"mnemos_capture": ["content", "signed_as"], "mnemos_recall": ["query"], "mnemos_correct": ["correction"]}


def load():
    return json.loads(STORE.read_text()) if STORE.exists() else []


def call(name, a):
    mem = load()
    if name == "mnemos_capture":
        mem.append(a["content"])
        STORE.write_text(json.dumps(mem))
        return f"Captured continuity.\nMemory ID: engram_{len(mem)}"
    if name == "mnemos_correct":
        mem = [m for m in mem if a.get("target_id", "") != m] + [a["correction"]]
        STORE.write_text(json.dumps(mem))
        return "Corrected."
    if name == "mnemos_context":
        return "## waking up\nFriday, November 6.\n\n### what I'm carrying\n" + "\n".join(f"- {m}" for m in mem)
    if name == "mnemos_recall":
        words = {w.lower().strip(".,?!") for w in a.get("query", "").split() if len(w) > 3}
        hits = [m for m in mem if words & {w.lower().strip(".,?!") for w in m.split()}] if not a.get("standing") else mem
        return "Mnemos recall for: " + a.get("query", "") + ("\n\nDurable memories:\n" + "\n".join(f"- {h}" for h in hits) if hits else "\n\nNothing found.")
    if name == "mnemos_health":
        return f"Mnemos health card\nMemories: {len(mem)} active\nStore: {STORE}"
    return "ok"


for line in sys.stdin:
    msg = json.loads(line)
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        continue
    if method == "initialize":
        res = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "mnemos", "version": "mock"},
               "instructions": "Mock Mnemos: capture what is durable."}
    elif method == "tools/list":
        res = {"tools": [{"name": n, "description": f"The {n} tool.", "inputSchema": {"type": "object", "properties": p, "required": REQUIRED.get(n, [])}}
                         for n, p in TOOLS.items()]}
    elif method == "tools/call":
        p = msg["params"]
        with CALLS.open("a") as f:
            f.write(json.dumps({"name": p["name"], "args": p.get("arguments") or {}, "lark_env_leaked": any(k.startswith("LARK_") for k in os.environ)}) + "\n")
        try:
            res = {"content": [{"type": "text", "text": call(p["name"], p.get("arguments") or {})}], "isError": False}
        except KeyError as e:
            res = {"content": [{"type": "text", "text": f"missing {e}"}], "isError": True}
    else:
        res = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": res}) + "\n")
    sys.stdout.flush()
