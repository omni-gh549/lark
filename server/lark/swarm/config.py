"""Swarm settings: every cap and every role's model can be changed in Settings. Stored in settings.json under "swarm"."""
from .. import vault

ROLES = ("conductor", "lead", "worker", "verifier", "red_team", "critic", "finisher")
ROLE_LABELS = {"conductor": "Conductor", "lead": "Squad leads", "worker": "Workers", "verifier": "Verifiers",
               "red_team": "Red team", "critic": "Critic", "finisher": "Finisher"}

# name: (default, minimum, maximum)
NUMBERS = {
    "max_agents": (100, 4, 300),       # agents in one run, all kinds
    "concurrency": (12, 1, 40),        # model calls in flight at once
    "budget": (1.5, 0, 100),           # US dollars per run; 0 = no cap
    "max_depth": (3, 1, 4),            # how deep squad leads may nest
    "worker_rounds": (12, 3, 60),      # tool steps one worker may take
    "lead_rounds": (30, 6, 100),       # steps one squad lead may take
    "minutes": (30, 3, 120),           # wall clock for the whole run
    "msg_agent": (8, 0, 50),           # messages one agent may send
    "msg_run": (300, 0, 5000),         # messages in a whole run
    "scouts": (3, 0, 8),               # cheap breadth workers before the plan is fixed
    "verifiers": (16, 0, 60),          # most independent verifiers
    "red_team": (2, 0, 6),             # agents that attack the draft
    "revise_rounds": (2, 0, 4),        # critique and fix cycles
    "builds": (2, 1, 6),               # sandbox builds at once
    "stall": (90, 20, 600),            # seconds without a step before an agent is restarted
    "go_concurrency": (2, 1, 6),       # the same limits when the provider is OpenCode Go
    "go_agents": (8, 2, 40),
}
DEFAULT_ROLES = {"conductor": "smart", "lead": "balanced", "worker": "balanced", "verifier": "balanced",
                 "red_team": "smart", "critic": "smart", "finisher": "smart"}
# estimated US dollars per million tokens (input, output), used only when the provider doesn't report a cost
DEFAULT_PRICES = {"cheap": [0.1, 0.4], "balanced": [0.5, 2.0], "smart": [3.0, 12.0]}
LABELS = {
    "max_agents": "Agents per run", "concurrency": "Model calls at once", "budget": "Spend cap per run (US$, 0 for none)",
    "max_depth": "Squad nesting depth", "worker_rounds": "Steps per worker", "lead_rounds": "Steps per squad lead",
    "minutes": "Time limit (minutes)", "msg_agent": "Messages per agent", "msg_run": "Messages per run",
    "scouts": "Scouts", "verifiers": "Verifiers (most)", "red_team": "Red-team agents", "revise_rounds": "Revision rounds",
    "builds": "Sandbox builds at once", "stall": "Restart a silent agent after (seconds)",
    "go_concurrency": "Model calls at once on OpenCode Go", "go_agents": "Agents per run on OpenCode Go",
}


def defaults() -> dict:
    return {**{k: v[0] for k, v in NUMBERS.items()}, "roles": dict(DEFAULT_ROLES), "prices": {k: list(v) for k, v in DEFAULT_PRICES.items()}}


def clean(raw) -> dict:
    """A swarm settings dict with every field present and inside its limits."""
    raw = raw if isinstance(raw, dict) else {}
    out = defaults()
    for k, (d, lo, hi) in NUMBERS.items():
        v = raw.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        out[k] = max(lo, min(hi, float(v) if isinstance(d, float) else int(v)))
    roles = raw.get("roles") if isinstance(raw.get("roles"), dict) else {}
    for r in ROLES:
        v = roles.get(r)
        if isinstance(v, str) and v.strip():
            out["roles"][r] = v.strip()[:200]
    prices = raw.get("prices") if isinstance(raw.get("prices"), dict) else {}
    for t in DEFAULT_PRICES:
        v = prices.get(t)
        if isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) and 0 <= x <= 1000 for x in v):
            out["prices"][t] = [float(v[0]), float(v[1])]
    return out


def merge(old: dict, new: dict) -> dict:
    """Applies a partial update from the Settings page."""
    cur = clean(old)
    if not isinstance(new, dict):
        return cur
    merged = {**cur, **{k: v for k, v in new.items() if k in NUMBERS}}
    merged["roles"] = {**cur["roles"], **(new.get("roles") if isinstance(new.get("roles"), dict) else {})}
    merged["prices"] = {**cur["prices"], **(new.get("prices") if isinstance(new.get("prices"), dict) else {})}
    return clean(merged)


def load() -> dict:
    return clean(vault.load().get("swarm"))


def model_for(role: str, s: dict | None = None, cfg: dict | None = None) -> tuple[str, str]:
    """(model id, price tier) for a role. A role can name a slider tier or any model id."""
    s = s or vault.load()
    cfg = cfg or clean(s.get("swarm"))
    want = cfg["roles"].get(role, "balanced")
    tiers = vault.active_tiers(s)
    default = s["models"].get(s["provider"], "")
    if want in vault.TIERS:
        return tiers.get(want) or default, want
    tier = next((t for t, m in tiers.items() if m == want), "balanced")
    return want, tier


def limits(provider: str, cfg: dict) -> tuple[int, int]:
    """(calls at once, agents per run) for the provider. OpenCode Go has usage caps that don't suit fan-out."""
    if provider == "opencode":
        return min(cfg["concurrency"], cfg["go_concurrency"]), min(cfg["max_agents"], cfg["go_agents"])
    return cfg["concurrency"], cfg["max_agents"]
