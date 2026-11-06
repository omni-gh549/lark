"""The swarm's additive stream events, and how a saved reply keeps a compact copy of them for the card."""
PHASES = ("Plan", "Research", "Verify", "Review", "Finish")


def start(run: str, t: float) -> dict:
    return {"swarm_start": {"run": run, "t": t}}


def phase(i: int) -> dict:
    return {"swarm_phase": {"i": i, "name": PHASES[i]}}


def spawn(a) -> dict:
    return {"agent_spawn": {"id": a.id, "parent": a.parent, "squad": a.squad, "kind": a.kind, "task": a.task[:160], "depth": a.depth}}


def state(a, error: str = "") -> dict:
    ev = {"id": a.id, "state": a.state, "activity": a.activity[:120]}
    if error:
        ev["error"] = error[:300]
    return {"agent_state": ev}


def msg(frm: str, to: str, kind: str) -> dict:
    return {"agent_msg": {"from": frm, "to": to, "kind": kind}}


def stats(d: dict) -> dict:
    return {"swarm_stats": d}


def end(d: dict) -> dict:
    return {"swarm_end": d}


def shot(agent: str, file: str) -> dict:
    return {"agent_shot": {"id": agent, "file": file}}


def fold(events: list[dict]) -> dict | None:
    """The swarm card as it stood at the end of these events, for the saved reply. None if the reply wasn't a swarm run."""
    agents: dict[str, dict] = {}
    info = None
    st, ph, shots, ended, msgs, over = {}, 0, [], None, 0, False
    for ev in events:
        if "swarm_start" in ev:
            info = ev["swarm_start"]
        elif "swarm_phase" in ev:
            ph = ev["swarm_phase"]["i"]
        elif "agent_spawn" in ev:
            a = ev["agent_spawn"]
            agents[a["id"]] = {**a, "state": "wait", "activity": ""}
        elif "agent_state" in ev:
            a = agents.get(ev["agent_state"]["id"])
            if a:
                a["state"], a["activity"] = ev["agent_state"]["state"], ev["agent_state"].get("activity", "")
                if ev["agent_state"].get("error"):
                    a["error"] = ev["agent_state"]["error"]
        elif "agent_msg" in ev:
            msgs += 1
        elif "agent_shot" in ev:
            shots.append(ev["agent_shot"])
        elif "swarm_stats" in ev:
            st = ev["swarm_stats"]
        elif "swarm_end" in ev:
            ended = ev["swarm_end"]
        elif "stopped" in ev or "error" in ev:
            over = True  # the reply was stopped or failed: nothing is still working
    if info is None:
        return None
    if ended and ended.get("trivial"):
        return None  # answered directly: nothing to show
    for a in agents.values():
        if (ended or over) and a["state"] in ("wait", "work"):
            a["state"] = "failed"  # the run is over: nothing is still working
            a["activity"] = ""
    return {"type": "swarm", "run": info["run"], "phase": 4 if ended else ph, "done": bool(ended or over), "agents": list(agents.values()),
            "stats": {**st, **(ended or {})}, "msgs": msgs, "shots": shots[-12:]}
