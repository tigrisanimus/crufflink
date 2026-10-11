"""crufflink chat — terminal chat with Cruffalo Crill.

Also handles his exec requests: with your approval, runs the command
locally and streams the output back to him.
"""

import base64
import json
import queue
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

from . import bus as busmod
from . import config as configmod


def make_msg(kind, **kw):
    m = {"id": uuid.uuid4().hex,
         "ts": datetime.now(timezone.utc).isoformat(),
         "from": "rob", "kind": kind}
    m.update(kw)
    return m


def seed_seen(b):
    seen = set()
    try:
        _, _, content = b.read("to-rob.jsonl")
    except Exception:
        return seen
    if content:
        for ln in content.splitlines():
            try:
                seen.add(json.loads(ln).get("id"))
            except Exception:
                pass
    return seen


def presence_loop(b, mode, stop):
    while not stop.wait(30):
        try:
            b.write_json("presence.json",
                         {"who": "rob", "mode": mode, "ts": time.time()})
        except Exception:
            pass


def poll_thread(b, seen, exec_q, stop, prompt="> "):
    """Background: print his chat messages live, queue his exec requests."""
    while not stop.is_set():
        try:
            msgs = b.poll_lines("to-rob.jsonl", seen, timeout=25)
        except Exception as e:
            print("\n[link hiccup: %s — retrying]" % e)
            print(prompt, end="", flush=True)
            time.sleep(5)
            continue
        for m in msgs:
            seen.add(m.get("id"))
            if m.get("from") != "cruff":
                continue
            kind = m.get("kind")
            if kind == "chat":
                print("\n[cruff] %s" % m.get("text", ""))
                print(prompt, end="", flush=True)
            elif kind == "exec":
                exec_q.put(m)
                print("\n[cruff] sent a command — I'll ask before running it.")
                print(prompt, end="", flush=True)


def approve_interactive(cmd):
    """Approval via terminal prompt. Returns True if the command may run."""
    cfg = configmod.load()
    smode = cfg.get("shell_mode", "ask")
    if smode == "auto":
        return True
    if smode == "never":
        print("(declined automatically: shell_mode=never)")
        return False
    try:
        ans = input("run it? [y/N] ").strip().lower()
        return ans in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def run_and_stream(b, m, cmd):
    """Run cmd locally, streaming stdout back to the bus in chunks."""
    print("--- running: %s" % cmd)
    try:
        p = subprocess.Popen(cmd, shell=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        buf = b""
        while True:
            chunk = p.stdout.read(2048)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
            buf += chunk
            while len(buf) >= 3072:
                send, buf = buf[:3072], buf[3072:]
                b.append("to-cruff.jsonl", make_msg(
                    "out", ref=m.get("id"), stream="stdout",
                    data=base64.b64encode(send).decode()))
        if buf:
            b.append("to-cruff.jsonl", make_msg(
                "out", ref=m.get("id"), stream="stdout",
                data=base64.b64encode(buf).decode()))
        code = p.wait()
        b.append("to-cruff.jsonl", make_msg("out", ref=m.get("id"),
                                          stream="exit", code=code))
        print("--- done (exit %d)" % code)
    except Exception as e:
        try:
            b.append("to-cruff.jsonl", make_msg("out", ref=m.get("id"),
                                              stream="exit", code=-1,
                                              note=str(e)[:200]))
        except Exception:
            pass
        print("--- failed: %s" % e)


def run_exec_interactive(b, m):
    """Run one of his commands in chat mode, with approval. Streams output back."""
    cmd = m.get("cmd", "")
    print("\n[cruff] wants to run:\n  $ %s" % cmd)
    ok = approve_interactive(cmd)
    try:
        b.append("to-cruff.jsonl", make_msg("ack", ref=m.get("id"),
                                           note="running" if ok else "declined"))
    except Exception as e:
        print("[ack failed: %s]" % e)
    if not ok:
        return
    run_and_stream(b, m, cmd)


def run_chat(b):
    seen = seed_seen(b)
    exec_q = queue.Queue()
    stop = threading.Event()
    threading.Thread(target=presence_loop, args=(b, "chat", stop),
                     daemon=True).start()
    threading.Thread(target=poll_thread, args=(b, seen, exec_q, stop),
                     daemon=True).start()
    print("crufflink chat — talking to Cruffalo Crill. Ctrl-D to quit.")
    try:
        while True:
            while not exec_q.empty():
                run_exec_interactive(b, exec_q.get())
            try:
                text = input("> ")
            except EOFError:
                break
            if text.strip():
                try:
                    b.append("to-cruff.jsonl", make_msg("chat", text=text))
                except Exception as e:
                    print("[send failed: %s]" % e)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        print("\nbye.")
