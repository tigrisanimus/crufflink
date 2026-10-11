# crufflink

A shared terminal + chat bridge between your computer and **Cruffalo Crill** (Muse, your personal AI).

Two things in one tiny program:

1. **Talk to him from your terminal** — `crufflink chat` opens a chat line straight to Cruffalo. He answers within a couple of minutes while your client is open.
2. **A terminal you share** — `crufflink shell` runs your interactive shell with him riding along: everything printed is streamed to him, and when he wants to run a command, it appears in *your* terminal and asks *you* first. Nothing runs behind your back.

Pure Python standard library. Zero dependencies. Works on macOS and Linux (the shared shell needs a Unix pty; chat works anywhere Python runs, including Windows).

## How it works

```
your machine                          GitHub (private bus repo)            Cruffalo
─────────────                         ─────────────────────────            ────────
crufflink chat ──poll──▶ bus/to-rob.jsonl ◀──append── cruff-send.py
crufflink chat ──append─▶ bus/to-cruff.jsonl ──poll──▶ cruff-watch.py (cron, ~2 min)
```

The "wire" is a **private** GitHub repo (`crufflink-bus`) holding two append-only
message logs. Your client polls one half; Cruffalo's watcher polls the other.
No servers to run, no ports to open, no accounts beyond GitHub.

## Quickstart

```bash
git clone https://github.com/tigrisanimus/crufflink.git
cd crufflink
./install.sh
crufflink pair
```

`crufflink pair` asks for a GitHub personal access token (classic, `repo` scope —
[create one here](https://github.com/settings/tokens); the `repo` scope is enough).
It creates the private `crufflink-bus` repo if needed. The token lives only in
`~/.config/crufflink/config.json` (mode 0600).

Then tell Cruffalo you're paired so he switches on his listener, and:

```bash
crufflink chat    # talk to him
crufflink shell   # shared terminal
```

## Commands

| command | what it does |
|---|---|
| `crufflink pair` | link this machine (GitHub token, creates private bus repo) |
| `crufflink chat` | chat with Cruffalo; his shell commands ask before running |
| `crufflink shell` | shared pty terminal — he sees your output, his commands are typed visibly |
| `crufflink send "hi"` | one-shot chat message (good for scripts) |
| `crufflink mode ask\|auto\|never` | approval mode for his shell commands (default: `ask`) |
| `crufflink status` | link status, who's watching |
| `crufflink daemon install` | install background agent (autostart at login) |
| `crufflink daemon status\|logs\|stop\|uninstall` | manage the background agent |

Approval modes: **ask** (default) prompts you for every command he sends;
**auto** runs them immediately; **never** declines them all. In `ask`/`auto`
shell mode, his commands are typed into *your* visible terminal — you can
always Ctrl-C.

## Background agent

`crufflink daemon install` sets up a user systemd service that starts at login
and runs headless:

- holds presence so he knows you're around,
- shows a desktop notification for his chat messages,
- runs his commands per your approval mode (`ask` uses a GUI prompt via
  zenity; without zenity it declines safely).

If `crufflink chat` or `crufflink shell` is open, the daemon stands down and
lets it handle commands, so nothing runs twice. Manage it with
`crufflink daemon status|logs|stop|uninstall`. One-shot messages anytime:
`crufflink send "hi"`.

## Token-saving layer

Long command output is capped on the wire (default 32 KB per command; your
terminal still sees everything). Tune it with `max_output_kb` in
`~/.config/crufflink/config.json` — the exit message notes when output was
truncated.

**Health checks** run locally on a schedule and only message him when
something needs attention — no news is good news:

```bash
crufflink check add disk 3600 -- bash -c 'test $(df / --output=pcent | tail -1 | tr -dc 0-9) -lt 90'
crufflink check list
crufflink check remove disk
```

A check is a shell command that exits 0 when all is well and nonzero when it
needs attention. Notifications fire on the ok→failing transition, then at
most every 4h while still failing.

**Retry policies** keep flaky commands local: his `exec` requests can carry
`retry_tries`/`retry_delay`, and early attempts run quietly — only the final
attempt streams output back.

## Jobs

A job is a whole script his machine runs autonomously — the token-saving
primitive for multi-step work. One job in, one result out; no round-trips
in between:

```bash
# from his side (a worker or script):
cruff-send.py job "nightly backup" --tries 2 --timeout 900 --file ./backup.sh
echo 'echo hello' | cruff-send.py job "quick test"
```

The daemon writes the script to a temp file, runs it with `bash` (under
`timeout`), retries quietly on failure, and streams only the final attempt
over the bus (capped like any command). `crufflink chat` can run jobs too.
Approval works exactly like exec: your mode applies, `ask` pops a GUI
prompt showing the script.

## Security model (read this)

- The bus repo is **private to your GitHub account**. Anyone holding your token
  (or your GitHub login) can read and write the bus — treat the token like a password.
- His commands **ask first by default**. `auto` mode is your explicit choice.
- Don't paste passwords or secrets into the chat — the bus keeps history.
- Cruffalo reaches the bus through *your* connected GitHub credential and only
  touches the `crufflink-bus` repo (plus this public repo).
- v1 has no payload encryption; the transport is TLS to GitHub's API. If you
  want stronger guarantees later, self-hosting the bus is on the roadmap.

## Protocol

One JSON object per line. Fields: `id`, `ts`, `from` (`rob`|`cruff`), `kind`.

| kind | direction | fields | meaning |
|---|---|---|---|
| `chat` | both | `text` | chat message |
| `exec` | cruff → rob | `cmd` | "please run this" |
| `ack` | rob → cruff | `ref`, `note` | `running` / `declined` |
| `out` | rob → cruff | `ref`, `stream`, `data` (base64), `code` | command output chunk / exit |
| `out` | rob → cruff | `stream: "pty"`, `data` | shared-shell output broadcast |

`agent/` holds Cruffalo's side of the bridge (`cruff-send.py`, `cruff-watch.py`) —
same protocol, nothing hidden.

## Roadmap ideas

- file transfer over the bus (chunked `file` messages)
- optional passphrase-based payload encryption
- `crufflink exec --auto` one-shot remote runs for scripts
- Windows shared shell (ConPTY)

## License

MIT — see LICENSE.
