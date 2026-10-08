"""Names chats. After a reply, a small cheap model reads the opening exchange and writes a title that is useful
when you look back through your history. Runs in the background and never raises: the first words of your
message stay as the title when it can't."""
import asyncio
import re

from . import chats, memory, providers, vault

SYSTEM = """You name chats in a personal assistant's history, so the owner can find them again at a glance.

Write one title for the conversation below.
- 2 to 6 words, sentence case, no quotes, no full stop, no emoji.
- Name the specific thing: people, places, products, dates, what was decided or done. "Rosa's dinner with Sam, Friday" beats "Dinner plans".
- Say what the chat is about, not that it is a chat: never start with "Chat about", "Question about", "Help with", "Request for", "Discussion of".
- If the assistant did something (booked, sent, compared, fixed), prefer the outcome over the ask.
- If a current title is given and still describes the whole conversation, repeat it exactly. Otherwise write the new one.
- If there is no real topic yet (a greeting, thanks), reply with exactly: New chat
Reply with the title only."""

FALLBACK = "google/gemini-2.5-flash-lite"  # tried when the title model fails (OpenRouter)
RETITLE_AT = 4  # your messages in a chat before the title is checked once more, in case the topic moved on
_lock = asyncio.Lock()


def clean(text: str) -> str:
    """The model's reply as a title, or '' when it has none (or says there's no topic yet)."""
    line = next((l.strip() for l in text.strip().splitlines() if l.strip()), "")
    line = re.sub(r"^(title|chat title)\s*:\s*", "", line, flags=re.I)
    line = line.strip(" \t\"'`“”‘’*#").rstrip(".。")
    if not line or line.lower().rstrip(".!") == "new chat":
        return ""
    words = line.split()
    if len(words) > 6:
        line = " ".join(words[:6])
    if len(line) > 60:
        line = line[:60].rsplit(" ", 1)[0] if " " in line[:60] else line[:60]
    return line.strip(" ,;:-") or ""


def _exchange(msgs: list[dict]) -> tuple[list[dict], int]:
    """The user/assistant messages with text, and how many of them are yours."""
    keep = [(m["role"], memory._message_text(m)) for m in msgs if m.get("role") in ("user", "assistant")]
    keep = [{"role": r, "text": t} for r, t in keep if t]
    return keep, sum(1 for m in keep if m["role"] == "user")


def _prompt(keep: list[dict], current: str | None) -> str:
    first_user = next((m["text"] for m in keep if m["role"] == "user"), "")
    first_reply = next((m["text"] for m in keep if m["role"] == "assistant"), "")
    out = f"User: {first_user[:600]}\nAssistant: {first_reply[:600]}"
    if len(keep) > 2:
        last_user = next((m["text"] for m in reversed(keep) if m["role"] == "user"), "")
        last_reply = next((m["text"] for m in reversed(keep) if m["role"] == "assistant"), "")
        if last_user != first_user:
            out += f"\n\nLater in the chat:\nUser: {last_user[:600]}\nAssistant: {last_reply[:400]}"
    if current:
        out += f"\n\nCurrent title: {current}"
    return out


async def _ask(name: str, key: str, model: str, prompt: str) -> str:
    # cheap models differ on reasoning: ask for none, and fall back to plain if the model refuses the field
    plain = {"temperature": 0.2, "max_tokens": 24}
    tries = [(model, {**plain, "reasoning": {"enabled": False}} if name == "openrouter" else plain), (model, plain)]
    if name == "openrouter" and model != FALLBACK:
        tries.append((FALLBACK, {**plain, "reasoning": {"enabled": False}}))
    for m, extra in tries:
        out, failed = "", False
        async for ev in providers.stream_round(name, key, m, [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}], None, extra):
            if "text" in ev:
                out += ev["text"]
            elif "error" in ev:
                failed = True
        if not failed and out.strip():
            return out
    return ""


async def name_chat(chat_id: str, name: str, key: str, model: str) -> None:
    """Titles the chat when it's due: after the first full exchange (until a topic shows up), and once more after a few."""
    try:
        if memory.is_contact_chat(chat_id):
            return
        doc = chats.load(chat_id)
        if not doc:
            return
        keep, users = _exchange(doc["messages"])
        if not users or not any(m["role"] == "assistant" for m in keep):
            return
        state = doc.get("titled")
        if state and (state.get("again") or users < RETITLE_AT):
            return
        current = doc["title"] if state else None
        s = vault.load()
        use = s["title_model"].strip() or model
        async with _lock:
            title = clean(await _ask(name, key, use, _prompt(keep, current)))
        if not title:
            return
        fresh = chats.load(chat_id)
        if not fresh or fresh.get("titled") != state:
            return  # something else titled it meanwhile
        chats.set_title(chat_id, title, users, again=bool(state))
    except Exception:
        pass


async def backfill(name: str, key: str, model: str) -> int:
    """Titles older chats that only have the first words of a message. Newest first."""
    done = 0
    for row in chats.listing():
        cid = row["id"]
        if memory.is_contact_chat(cid):
            continue
        doc = chats.load(cid)
        if not doc or doc.get("titled"):
            continue
        keep, users = _exchange(doc["messages"])
        if not users or not any(m["role"] == "assistant" for m in keep):
            continue
        await name_chat(cid, name, key, model)
        after = chats.load(cid)
        done += bool(after and after.get("titled"))
    return done
