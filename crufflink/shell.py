"""crufflink shell — a genuinely shared terminal.

Your interactive shell runs here. Everything printed in it is streamed to
Cruffalo so he can follow along. When he sends a command, it appears here
and — with your approval — is typed into your shell visibly, so you see
exactly what runs. Type 'exit' to leave.
"""

import base64
import fcntl
import os
import pty
import queue
import select
import shutil
import signal
import struct
import termios
import threading
import time
import tty

from . import chat as chatmod
from . import config as configmod


def _fit_winsize(fd):
    try:
        cols, rows = shutil.get_terminal_size()
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except Exception:
        pass


def _approve_cooked(saved):
    cfg = configmod.load()
    smode = cfg.get("shell_mode", "ask")
    if smode == "auto":
        return True
    if smode == "never":
        return False
    try:
        termios.tcsetattr(0, termios.TCSADRAIN, saved)
        try:
            ans = input("run it? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            ans = ""
        return ans.strip().lower() in ("y", "yes")
    finally:
        tty.setraw(0)


def _handle_exec(b, fd, m, saved):
    cmd = m.get("cmd", "")
    os.write(1, ("\r\n[cruff] wants to run:\r\n  $ %s\r\n" % cmd).encode())
    ok = _approve_cooked(saved)
    if not ok:
        os.write(1, b"(declined)\r\n")
    try:
        b.append("to-cruff.jsonl", chatmod.make_msg(
            "ack", ref=m.get("id"), note="running" if ok else "declined"))
    except Exception:
        pass
    if ok:
        os.write(fd, cmd.encode() + b"\n")  # typed visibly into your shell


def run_shell(b):
    shell = os.environ.get("SHELL", "/bin/bash")
    pid, fd = pty.fork()
    if pid == 0:
        os.environ.setdefault("TERM", "xterm-256color")
        try:
            os.execvp(shell, [shell, "-i"])
        except Exception:
            os.execvp("/bin/sh", ["/bin/sh", "-i"])

    saved = termios.tcgetattr(0)
    stop = threading.Event()
    try:
        _fit_winsize(fd)
        signal.signal(signal.SIGWINCH, lambda s, f: _fit_winsize(fd))
        seen = chatmod.seed_seen(b)
        exec_q = queue.Queue()
        threading.Thread(target=chatmod.presence_loop, args=(b, "shell", stop),
                         daemon=True).start()
        threading.Thread(target=chatmod.poll_thread,
                         args=(b, seen, exec_q, stop, ""),
                         daemon=True).start()
        tty.setraw(0)
        os.write(1, ("\r\ncrufflink shared shell - live and shared with Cruffalo Crill.\r\n"
                     "Type 'exit' to leave. His commands appear here and ask first.\r\n").encode())
        out_buf = bytearray()
        last_flush = time.time()
        while True:
            r, _, _ = select.select([0, fd], [], [], 0.5)
            if 0 in r:
                try:
                    data = os.read(0, 4096)
                except OSError:
                    data = b""
                if data:
                    os.write(fd, data)
            if fd in r:
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    data = b""
                if not data:
                    break  # shell exited
                os.write(1, data)
                out_buf += data
            now = time.time()
            if out_buf and (len(out_buf) >= 3072 or now - last_flush > 2):
                chunk = bytes(out_buf[:3072])
                del out_buf[:3072]
                try:
                    b.append("to-cruff.jsonl", chatmod.make_msg(
                        "out", stream="pty",
                        data=base64.b64encode(chunk).decode()))
                except Exception:
                    pass
                last_flush = now
            while not exec_q.empty():
                _handle_exec(b, fd, exec_q.get(), saved)
        if out_buf:
            try:
                b.append("to-cruff.jsonl", chatmod.make_msg(
                    "out", stream="pty",
                    data=base64.b64encode(bytes(out_buf)).decode()))
            except Exception:
                pass
    finally:
        stop.set()
        termios.tcsetattr(0, termios.TCSADRAIN, saved)
        print("\nshell closed. bye.")
        try:
            os.waitpid(pid, 0)
        except Exception:
            pass
