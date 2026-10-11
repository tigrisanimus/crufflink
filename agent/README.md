# Cruffalo-side tooling

These two scripts are the Cruffalo Crill end of the crufflink bridge. They run
on his machine (not yours) and talk to the same private bus repo your client
uses. They're in this repo so the whole thing is auditable — nothing hidden.

- `cruff-send.py chat "text"` / `cruff-send.py exec "command"` — append to
  `bus/to-rob.jsonl` (the half your client reads).
- `cruff-watch.py` — read `bus/to-cruff.jsonl` (the half your client writes),
  print anything new since the watermark, refresh presence. Silent when idle.

Auth: your connected GitHub credential (surrogate), scoped to `api.github.com`.
Only ever touches the `crufflink-bus` repo. If the bus repo doesn't exist yet
(you haven't run `crufflink pair`), both scripts fail silently/softly.
