"""crufflink chat — terminal chat with Cruffalo Crill.

Also handles his exec requests: with your approval, runs the command
locally and streams the output back to him.
"""

import base64
import json
import os
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


def poll_thread(b, seen, exec_q, stop, prompt="> ", job_q=None):
    """Background: print his chat messages live, queue his exec/job requests."""
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
            elif kind == "job" and job_q is not None:
                job_q.put(m)
                print("\n[cruff] sent a job '%s' — I'll ask before running it."
                      % m.get("name", "job"))
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


def _max_output_bytes():
    try:
        return int(configmod.load().get("max_output_kb", 32)) * 1024
    except Exception:
        return 32768


def run_and_stream(b, m, cmd, max_bytes=None):
    """Run cmd locally, streaming stdout back to the bus in chunks.

    max_bytes caps what goes over the bus (your local terminal still sees
    everything). Beyond the cap, output is dropped and the exit message notes
    the truncation. None = config max_output_kb (default 32).
    """
    if max_bytes is None:
        max_bytes = _max_output_bytes()
    print("--- running: %s" % cmd)
    sent = 0
    dropped = 0
    try:
        p = subprocess.Popen(cmd, shell=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        buf = b""

        def flush(force=False):
            nonlocal sent, dropped, buf
            while buf and (len(buf) >= 3072 or force):
                if sent >= max_bytes:
                    dropped += len(buf)
                    buf = b""
                    return
                send, buf = buf[:3072], buf[3072:]
                room = max_bytes - sent
                if len(send) > room:
                    dropped += len(send) - room
                    send = send[:room]
                if not send:
                    return
                b.append("to-cruff.jsonl", make_msg(
                    "out", ref=m.get("id"), stream="stdout",
                    data=base64.b64encode(send).decode()))
                sent += len(send)

        while True:
            chunk = p.stdout.read(2048)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
            buf += chunk
            flush()
        flush(force=True)
        code = p.wait()
        note = None
        if dropped:
            note = "output truncated: sent %d KB of %d KB total" % (
                sent // 1024, (sent + dropped) // 1024)
            print("--- %s" % note)
        b.append("to-cruff.jsonl", make_msg("out", ref=m.get("id"),
                                          stream="exit", code=code, note=note))
        print("--- done (exit %d)" % code)
    except Exception as e:
        try:
            b.append("to-cruff.jsonl", make_msg("out", ref=m.get("id"),
                                              stream="exit", code=-1,
                                              note=str(e)[:200]))
        except Exception:
            pass
        print("--- failed: %s" % e)


def run_with_retry(b, m, cmd, tries=1, delay=10, max_bytes=None):
    """Run cmd up to `tries` times until exit 0.

    Early attempts run quietly (output stays local); only the final attempt
    streams over the bus. Saves tokens on flaky commands.
    """
    try:
        tries = max(1, int(tries or 1))
    except Exception:
        tries = 1
    try:
        delay = max(0, int(delay or 0))
    except Exception:
        delay = 0
    for attempt in range(1, tries + 1):
        if attempt > 1:
            print("--- attempt %d/%d (waiting %ds)" % (attempt, tries, delay))
            time.sleep(delay)
        if attempt < tries:
            try:
                r = subprocess.run(cmd, shell=True, capture_output=True,
                                   text=True, timeout=600)
            except Exception as e:
                print("--- attempt %d errored: %s" % (attempt, e))
                continue
            sys.stdout.write(r.stdout)
            sys.stdout.flush()
            if r.returncode == 0:
                b.append("to-cruff.jsonl", make_msg(
                    "out", ref=m.get("id"), stream="exit", code=0,
                    note="succeeded on attempt %d/%d (output kept local)"
                         % (attempt, tries)))
                print("--- done (exit 0 on attempt %d)" % attempt)
                return 0
            print("--- attempt %d failed (exit %d)" % (attempt, r.returncode))
        else:
            if tries > 1:
                print("--- final attempt %d/%d" % (attempt, tries))
            run_and_stream(b, m, cmd, max_bytes=max_bytes)
            return 0
    return 0


def run_job(b, m, name, script, tries=1, delay=10, timeout=600, max_bytes=None):
    """Run a multi-step job script autonomously on this machine.

    The whole script runs start to finish per attempt; early attempts are
    quiet (output stays local); only the final attempt streams over the bus.
    One job in, one result out — no token-burning round-trips in between.
    """
    import tempfile
    try:
        tries = max(1, int(tries or 1))
    except Exception:
        tries = 1
    try:
        delay = max(0, int(delay or 0))
    except Exception:
        delay = 0
    try:
        timeout = max(30, int(timeout or 600))
    except Exception:
        timeout = 600
    if max_bytes is None:
        max_bytes = _max_output_bytes()
    path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False,
                                         prefix="crufflink-job-") as f:
            f.write("#!/bin/bash\n" + script + "\n")
            path = f.name
        cmd = "timeout %d bash %s" % (timeout, path)
        for attempt in range(1, tries + 1):
            if attempt > 1:
                print("--- job attempt %d/%d (waiting %ds)"
                      % (attempt, tries, delay))
                time.sleep(delay)
            if attempt < tries:
                try:
                    r = subprocess.run(cmd, shell=True, capture_output=True,
                                       text=True, timeout=timeout + 60)
                except Exception as e:
                    print("--- job attempt %d errored: %s" % (attempt, e))
                    continue
                sys.stdout.write(r.stdout)
                sys.stdout.flush()
                if r.returncode == 0:
                    b.append("to-cruff.jsonl", make_msg(
                        "out", ref=m.get("id"), stream="exit", code=0,
                        note="job '%s' succeeded on attempt %d/%d "
                             "(output kept local)" % (name, attempt, tries)))
                    print("--- job '%s' done (exit 0, attempt %d)"
                          % (name, attempt))
                    return 0
                print("--- job attempt %d failed (exit %d)"
                      % (attempt, r.returncode))
            else:
                if tries > 1:
                    print("--- job final attempt %d/%d" % (attempt, tries))
                print("--- job '%s' running" % name)
                run_and_stream(b, m, cmd, max_bytes=max_bytes)
                return 0
    finally:
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
    return 0


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


def run_job_interactive(b, m):
    """Run one of his job scripts in chat mode, with approval."""
    name = m.get("name", "job")
    script = m.get("script", "")
    print("\n[cruff] sent a job '%s':" % name)
    print("  --- script ---")
    print(script[:1500])
    print("  --------------")
    ok = approve_interactive("$ job: %s" % name)
    try:
        b.append("to-cruff.jsonl", make_msg("ack", ref=m.get("id"),
                                           note="running" if ok else "declined"))
    except Exception as e:
        print("[ack failed: %s]" % e)
    if not ok:
        return
    run_job(b, m, name, script,
            tries=m.get("retry_tries", 1),
            delay=m.get("retry_delay", 10),
            timeout=m.get("timeout", 600))


def run_chat(b):
    seen = seed_seen(b)
    exec_q = queue.Queue()
    job_q = queue.Queue()
    stop = threading.Event()
    threading.Thread(target=presence_loop, args=(b, "chat", stop),
                     daemon=True).start()
    threading.Thread(target=poll_thread, args=(b, seen, exec_q, stop),
                     kwargs={"job_q": job_q}, daemon=True).start()
    print("crufflink chat — talking to Cruffalo Crill. Ctrl-D to quit.")
    try:
        while True:
            while not exec_q.empty():
                run_exec_interactive(b, exec_q.get())
            while not job_q.empty():
                run_job_interactive(b, job_q.get())
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
