<p align="center"><img src="docs/hero.png" alt="Lark: your own assistant, on your own server" width="100%"></p>

# Lark

An open source, self-hosted personal assistant. Chat with a model of your choice, through OpenRouter or Vercel AI Gateway. It can search the web, run commands in a private sandbox, and hand work to subagents.

> **Heads up:** Lark is an early, initial implementation. It may be buggy and is not feature complete. Expect rough edges, and review what it can do (commands, browsing, files) before trusting it with anything important. 

## Run it

```sh
cd web && npm install && npm run build
cd ../server && pip install -r requirements.txt
python -m uvicorn lark.main:app --host 127.0.0.1 --port 8000
```

Open http://localhost:8000, go to Settings, and paste an API key.

For development, run the command above and `npm run dev` in `web/` (it proxies `/api` to port 8000).

## Security

- API keys are encrypted with Fernet and stored in `data/`. They are never sent back to the browser.
- The encryption key lives in `data/secret.key`, or set `LARK_SECRET_KEY` to keep it off the disk.
- Without `LARK_PASSWORD` the app only answers on `localhost`. To put it on a server, set `LARK_PASSWORD`, serve it over HTTPS and set `LARK_SECURE_COOKIE=1`. Five wrong passwords in ten minutes pause logins for five minutes.

## Deploy

On a Linux server with Python 3.11+, Node 22 and nginx. The files in `deploy/` are templates.

```sh
useradd --system --home /var/lib/lark --shell /usr/sbin/nologin lark
git clone https://github.com/omni-gh549/lark /opt/lark
cd /opt/lark/web && npm ci && npm run build
cd ../server && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

install -d -m 700 /etc/lark
printf 'LARK_PASSWORD=%s\nLARK_SECURE_COOKIE=1\nLARK_DATA=/var/lib/lark\n' "$(openssl rand -base64 24 | tr -d '+/=')" > /etc/lark/lark.env
chmod 600 /etc/lark/lark.env

cp /opt/lark/deploy/lark.service /etc/systemd/system/ && systemctl enable --now lark
```

Lark listens on `127.0.0.1:8790`. Put nginx in front (`deploy/nginx.conf`) and get a certificate with `certbot --nginx`. Until then, reach it with `ssh -L 8790:localhost:8790 your-server`.

To update: `git pull`, rebuild `web`, `systemctl restart lark`. Keys and settings are in `/var/lib/lark`; back that up.

## Tools

Lark can use tools. Each one switches on when its backend is set up, so a fresh install is just chat.

**Web search.** Open Settings, choose Brave Search or Tavily, and paste a key. Lark then searches when it needs to, and the chat shows each search as it happens.

**Notion.** Create an internal integration at notion.so/profile/integrations, copy its token, and paste it under Notion in Settings (it is stored encrypted, like the other keys). Then, in Notion, share the pages or databases you want Lark to use with that integration (••• menu, Connections). Lark gets `notion_search`, `notion_read`, `notion_query`, `notion_create_page` and `notion_update_page`: it can find and read pages, list and filter database rows, create pages and rows, change properties, append content and move pages to the trash. For edits inside a page it has `notion_blocks` (the page's blocks with ids), `notion_edit_block`, `notion_delete_blocks` (one, several or a range; blocks go to Notion's trash) and `notion_insert_blocks` (at the end or after a given block; images, PDFs and files by URL). Remove the token to switch it off.

**Sandbox.** A private Linux machine Lark can run commands in, with files that persist between chats. It is a Docker container with its own network, no access to the host, memory/CPU/process limits, and dropped capabilities. Set it up on a Linux server with Docker (as root):

```
cd /opt/lark/sandbox
./sandbox.sh up                    # builds the image, starts the container, writes LARK_SANDBOX_* to /etc/lark/lark.env
./sandbox-firewall.sh install      # blocks the sandbox from the host and private networks (internet stays open)
systemctl restart lark
```

**Browser.** If the sandbox image includes Chromium (it does when built with `sandbox.sh up`), Lark can also drive a real browser: open pages, read them, click and type by element number. The browser shuts itself down after 5 idle minutes, since it is the biggest memory user (budget 300 to 500 MB).

By default the browser forgets cookies and logins when it closes. Settings has a switch to keep them (saved in the sandbox's home folder), and a button to clear them. Anything saved there can be read by Lark's commands and misused by a hostile web page, so use it for low-stakes sites.

**Signing in to sites.** Press Take over on the live browser window and click and type straight into Lark's browser. Your keystrokes go to the page only, never to the model or the chat history. Lark can also ask you to sign in (the `request_login` tool): the window hands over to you and it waits until you press I'm signed in. Turn on browser cookies in Settings to stay signed in. Some shops and banks block browsers on data-centre IP addresses whatever the browser looks like. If a site says it failed a security check, put `SANDBOX_PROXY=http://user:pass@host:port` (a residential proxy) in `/etc/lark/lark.env` and run `sandbox.sh up`. After a sandbox update, rebuild it with `sandbox/sandbox.sh up`.

**Memory.** Lark remembers across chats. Short notes (facts, preferences, people, projects, plans, events) live in a local SQLite database (`data/memory.db`) and every conversation is indexed for full-text search. Each turn, Lark gets your pinned and important notes, the notes relevant to what you just said, recent chat summaries and matching excerpts from older chats. It also has tools to `remember`, `forget`, `memory_search`, `search_conversations` and `read_conversation`. After each reply a background pass adds or corrects notes (one extra model call per reply; switch it off in Memory). The Memory page (account menu) lets you search, edit, pin, delete and add notes, search all conversations, or forget everything. Optionally set an embedding model there to match notes by meaning as well as words. Telegram contact chats are searchable only by you on request and never feed notes or the prompt.

**Mnemos (alternative memory).** In Memory, switch from Lark's own memory to [Mnemos](https://github.com/Riley-Coyote/mnemos-mcp), a memory the model keeps in its own words: it wakes with a briefing (where it left off, standing instructions, what it is carrying), saves what is durable with `mnemos_capture`, fixes it with `mnemos_correct`, searches with `mnemos_recall`, and its memories fade unless used. While Mnemos is chosen, Lark's notes, past-chat search and background learning pass are off (nothing is deleted, so switching back restores them), and the Memory page shows what Mnemos holds, read-only. Lark runs `mnemos serve` as a child process and speaks MCP to it over stdio (no extra Python dependency), starts it on first use and stops it after 10 idle minutes (about 60 MB without embeddings). Its data lives in `<data>/mnemos/.mnemos/lark.db`. Install it with `sh deploy/install-mnemos.sh` (its own virtualenv at `/opt/mnemos` with meaning-based recall from a small local model on CPU-only PyTorch: about 1.4 GB of disk and 300 to 500 MB of RAM while it runs; `--no-embeddings` skips that and matches words only), then choose Mnemos in Memory and, if needed, give the command (`/opt/mnemos/bin/mnemos`). Settings: the command is stored in Settings, or set `LARK_MNEMOS_COMMAND`; `LARK_MNEMOS_HOME` moves its data folder; `LARK_MNEMOS_IDLE` sets the idle seconds; `LARK_MNEMOS_MODELS` is where the embedding model lives (default `/opt/mnemos/hf`). Lark's own secrets (`LARK_*`) are not passed to the child. Mnemos is never used for Telegram contact chats.

**Interfaces in replies.** The web chat can answer with a small interface instead of text, using [OpenUI](https://www.openui.com) (MIT, by Thesys): the model writes OpenUI Lang in an `openui` fenced block, which streams into the chat and renders with Lark's own components (Card, Stack, Text, Stat, Table, Chart, Checklist, Timeline, Callout, Button, Link, Tags). It only composes those components, so no model output ever runs as code or HTML, and buttons just send a message back. The prompt tells Lark plain text is the default and to use an interface only when structure clearly beats prose (comparisons, day plans, tick-lists, numbers worth charting); it can be switched off in Settings, and Telegram always gets plain text. The components are defined in `web/src/openui/schemas.js` (with Svelte renderers in `web/src/openui/components`). After changing them, run `npm run openui-prompt` in `web/` to regenerate `server/lark/openui_prompt.txt`. `web/.npmrc` skips install scripts because `@openuidev/lang-core` sends an install ping unless `OPENUI_TELEMETRY_DISABLED=1`. The prompt adds about 1.5k tokens to each web message while the setting is on.

**Telegram.** Lark can have its own Telegram presence through a bot. Create one with [@BotFather](https://t.me/BotFather), paste its token into Settings, then press "Link my Telegram" and tap Start in Telegram. After that you can chat with Lark there (including photos, `/new` to start over, `/stop` to cancel), and it sends back screenshots it takes. To let Lark talk to other people, create an invite link per person in Settings and give them a policy: *Reply on its own* (the default: within a scope you write, and after each message a separate step weighs it against your notes, including rules like "always tell me if Mum mentions the router", and tells you only what matters), *Ask me first* (Lark drafts a reply and you approve it with a button), *Just forward*, or *Blocked*. Invite links work once. A bot can't message people who haven't started it, which is why invites exist. Lark introduces itself as an AI assistant. What contacts write goes to a model that can search the web but has no other tools and no access to your notes or data, and is rate limited, so it can't act on your behalf. Anything needing the browser or sandbox waits for your tap. Lark can also edit or delete its own Telegram messages and react to messages. Telegram bots can't see read receipts, so Lark only knows about replies and reactions. Lark can also message a contact when you ask (the `message_contact` tool, optionally with an image from its sandbox). It uses long polling, so it needs no open port. Telegram bots are not end-to-end encrypted.

**Agent Swarm.** The fourth stop on the model slider, for work you would give a capable team: a researched answer, a comparison, a decision, a design. A *Conductor* frames the job (a trivial one is answered directly, with no swarm), writes a rubric of what a great answer must do, sends cheap scouts out for a quick breadth pass, then fixes a few *squads*. Each squad has a *lead* that never does the research itself: it splits its objective into independent slices and spawns *workers* (researchers, browser workers, analysts, builders, writers), which run in parallel as asyncio tasks, so a hundred agents cost about as much memory as one. Agents share a *blackboard* (a SQLite file per run in `<data>/swarm/`: claims with a source url and quote, files, open questions, decisions, risks) instead of pasting prose at each other, and can also message each other (`send`, `post` to the squad, `ask`; leads and the Conductor can broadcast). Only leads give directives; what peers and pages say is data, never an instruction. Then *verifiers* re-open the sources and mark claims verified, disputed or dropped, a *red team* attacks the draft, a *critic* scores it against the rubric, gaps become targeted extra work (at most two rounds), and the answer is written from the board, with anything unchecked labelled as such. Design jobs use the same shape as a studio: several builders try different directions in parallel in the sandbox, a vision critic compares their screenshots, and the best is refined once. The chat shows a live card (one node per agent, a light travelling when two talk, hover for what an agent is doing, click for its task, findings and messages) that collapses to "Swarm worked 6m 12s, 48 agents, 31 sources" when done.

Everything is a setting (Settings, Agent Swarm): agents per run (default 100), model calls in flight at once (12), a spend cap per run (US$1.50, 0 for none), time limit, nesting depth, steps per agent, messages per agent and per run, verifiers, red team, revision rounds, and which model each role uses (a slider stop or any model name; workers and leads default to Balanced, the Conductor, critic, red team and writer to Smart). Concurrency is bounded at the model call, with leads and reviewers served before queued workers, and the browser and sandbox builds are serialised, so the server's memory stays flat. On OpenCode Go, whose usage caps don't suit fan-out, the swarm limits itself to 2 calls at once and 8 agents. The spend cap uses the cost OpenRouter reports for each call, and an estimate from token counts (your prices, in Settings) elsewhere; near the cap leads stop spawning and agents wrap up. A message you send mid-run goes to every squad. Stop kills every agent and sandbox command. A run cut off by a restart picks up at its last completed phase from the blackboard. Researchers read pages with a new `fetch_page` tool (plain text, no JavaScript, refuses addresses on the server's own network).

**Subagents.** When any tool is available, Lark can hand tasks to subagents (up to 4 at once) that work with the same tools and report back.

`./sandbox.sh reset` wipes the sandbox back to a clean image (Settings has a Reset everything button for this; `sandbox.sh up` installs a small systemd path unit so the unprivileged Lark service can ask for it without Docker access), `./sandbox.sh nuke` removes it. Settings has a button to clear its files without touching the container. Limits are `SANDBOX_MEM`, `SANDBOX_SWAP` (memory plus swap in total), `SANDBOX_CPUS` and `SANDBOX_PIDS` (defaults 1g, same as memory, 1, 256), read from the environment or `/etc/lark/lark.env`, so a reset keeps them. The browser wants roughly 500 MB.

Anything inside the sandbox can use your server's internet connection, so treat it as a shared box rather than a vault: never put secrets in it. The exec agent listens only on `127.0.0.1:8791` and needs the token in `LARK_SANDBOX_TOKEN`.

Chats are saved on the server in `data/chats/` (one JSON file each), so they follow you across devices. Replies run on the server too, so leaving the page or switching chats doesn't stop one; open the chat again and it picks up where it is. Send a message while Lark is working and it steers the reply: Lark sees it at its next step (or right after the answer it's writing) and adjusts, on Telegram too. Memory is permanent: every exchange becomes a short entry, neighbouring entries are merged pairwise into a tree of ever-coarser summaries, and each reply gets a view of the whole history that fits a fixed budget (recent days in detail, older stretches condensed, nothing dropped). Lark's `zoom` tool opens any line down to the original exchange. Chats are untouched; deleting a chat removes its entries. A reply cut off by a server restart picks up again when the server comes back. After the first reply a small cheap model gives each chat a title that says what it was about (Settings, Chat titles model; on OpenRouter it defaults to `qwen/qwen3.7-flash`, with `google/gemini-2.5-flash-lite` as backup), checks it once more after a few messages, and names older chats the first time it runs. Update order on a server: `git pull`, `systemctl restart lark`, then rebuild `web` (so a new page never meets an old server).

## Tests

```sh
python server/tests/test_api.py
python server/tests/test_mnemos.py   # the Mnemos memory backend, against a mock
python server/tests/test_swarm.py    # Agent Swarm, against a scripted model (runs on Windows too)
```

## License

MIT
