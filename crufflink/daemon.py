"""crufflink daemon — background agent on your machine.

Runs headless (usually under systemd --user, started at login):
  - holds presence so Cruffalo knows you're around,
  - shows a desktop notification for his chat messages,
  - runs his exec requests per your approval mode.

If an interactive client (crufflink chat/shell) is active, the daemon stands
down and lets it handle execs, so nothing runs twice.

CLI: crufflink daemon {install,uninstall,start,stop,restart,status,logs,run}
"""

import json
import os
import signal
import subprocess
import threading
import time

from . import chat as chatmod
from . import config as configmod

SERVICE_NAME = "crufflink.service"


def log(msg):
    print("[daemon] %s" % msg, flush=True)


def ensure_desktop_env():
    """Best-effort session bus so notify-send/zenity can reach the desktop."""
    uid = os.getuid()
    os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS",
                          "unix:path=/run/user/%d/bus" % uid)


def notify(title, text):
    try:
        subprocess.run(["notify-send", title, text[:200]],
                       timeout=5, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def approve_daemon(cmd):
    """Approval without a terminal: GUI prompt if available, else config mode."""
    cfg = configmod.load()
    smode = cfg.get("shell_mode", "ask")
    if smode == "auto":
        return True
    if smode == "never":
        return False
    try:
        r = subprocess.run(
            ["zenity", "--question", "--title=crufflink",
             "--text=Cruffalo wants to run:\n$ %s" % cmd,
             "--ok-label=Run", "--cancel-label=Decline",
             "--timeout=120"],
            timeout=130, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return r.returncode == 0
    except Exception:
        return False  # no way to ask: safe default is decline


def interactive_active(b):
    """True if a chat/shell client has checked in recently."""
    try:
        _, _, content = b.read("presence.json")
        if not content:
            return False
        p = json.loads(content)
        return (time.time() - p.get("ts", 0) < 90) and p.get("mode") in ("chat", "shell")
    except Exception:
        return False


def already_acked(b, ref):
    """True if someone already acked this exec (avoids double execution)."""
    try:
        _, _, content = b.read("to-cruff.jsonl")
    except Exception:
        return False
    if not content:
        return False
    for ln in content.splitlines():
        try:
            m = json.loads(ln)
        except Exception:
            continue
        if m.get("kind") == "ack" and m.get("ref") == ref:
            return True
    return False


def daemon_presence_loop(b, stop):
    while not stop.wait(30):
        try:
            b.write_json("daemon-presence.json",
                         {"who": "rob", "mode": "daemon", "ts": time.time()})
        except Exception as e:
            log("presence failed: %s" % e)


def handle_exec_daemon(b, m):
    cmd = m.get("cmd", "")
    if interactive_active(b):
        log("exec skipped (interactive client active)")
        return
    if already_acked(b, m.get("id")):
        log("exec skipped (already handled)")
        return
    ok = approve_daemon(cmd)
    try:
        b.append("to-cruff.jsonl", chatmod.make_msg(
            "ack", ref=m.get("id"), note="running" if ok else "declined"))
    except Exception as e:
        log("ack failed: %s" % e)
    if not ok:
        log("exec declined: %s" % cmd[:80])
        notify("crufflink", "Declined command: %s" % cmd[:80])
        return
    log("exec running: %s" % cmd[:80])
    chatmod.run_and_stream(b, m, cmd)


def run_daemon(b):
    ensure_desktop_env()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda s, f: stop.set())
    signal.signal(signal.SIGINT, lambda s, f: stop.set())
    seen = chatmod.seed_seen(b)
    threading.Thread(target=daemon_presence_loop, args=(b, stop), daemon=True).start()
    log("started (approval mode: %s)" % configmod.load().get("shell_mode", "ask"))
    while not stop.is_set():
        try:
            msgs = b.poll_lines("to-rob.jsonl", seen, timeout=25)
        except Exception as e:
            log("link hiccup: %s" % e)
            time.sleep(5)
            continue
        for m in msgs:
            seen.add(m.get("id"))
            if m.get("from") != "cruff":
                continue
            if m.get("kind") == "chat":
                text = m.get("text", "")
                log("chat: %s" % text[:80])
                notify("Cruffalo Crill", text)
            elif m.get("kind") == "exec":
                handle_exec_daemon(b, m)
    log("stopped")
    return 0


UNIT_TEMPLATE = """[Unit]
Description=crufflink background agent
After=network-online.target
Wants=network-online.target

[Service]
ExecStart={home}/.local/bin/crufflink daemon run
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""


def _systemctl(*args):
    r = subprocess.run(["systemctl", "--user"] + list(args),
                       capture_output=True, text=True, timeout=30)
    return r.returncode, (r.stdout + r.stderr).strip()


def _unit_path():
    return os.path.expanduser("~/.config/systemd/user/%s" % SERVICE_NAME)


def cmd_install():
    path = _unit_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(UNIT_TEMPLATE.format(home=os.path.expanduser("~")))
    _systemctl("daemon-reload")
    code, out = _systemctl("enable", "--now", SERVICE_NAME)
    print(out or ("background agent %s" % ("installed and started"
                                           if code == 0 else "install FAILED")))
    print("It now starts automatically at login. Manage it with:")
    print("  crufflink daemon status|logs|stop|uninstall")
    return 0 if code == 0 else 1


def cmd_uninstall():
    _systemctl("disable", "--now", SERVICE_NAME)
    try:
        os.remove(_unit_path())
    except OSError:
        pass
    _systemctl("daemon-reload")
    print("background agent removed.")
    return 0


def cmd_status():
    code, out = _systemctl("is-active", SERVICE_NAME)
    state = out.strip() or "unknown"
    cfg = configmod.load()
    print("daemon: %s" % state)
    print("approval mode: %s" % cfg.get("shell_mode", "ask"))
    return 0


def cmd_logs():
    r = subprocess.run(["journalctl", "--user", "-u", SERVICE_NAME,
                        "-n", "40", "--no-pager"], timeout=15)
    return r.returncode


def cli(action):
    if action == "install":
        return cmd_install()
    if action == "uninstall":
        return cmd_uninstall()
    if action == "start":
        code, out = _systemctl("start", SERVICE_NAME)
        print(out or "started")
        return code
    if action == "stop":
        code, out = _systemctl("stop", SERVICE_NAME)
        print(out or "stopped")
        return code
    if action == "restart":
        code, out = _systemctl("restart", SERVICE_NAME)
        print(out or "restarted")
        return code
    if action == "logs":
        return cmd_logs()
    return cmd_status()
