"""crufflink CLI."""

import argparse
import json
import sys
import time

from . import __version__
from . import bus as busmod
from . import chat as chatmod
from . import config as configmod


def _bus_from_config():
    cfg = configmod.load()
    if not cfg.get("token"):
        print("not paired yet — run: crufflink pair")
        sys.exit(1)
    return busmod.Bus(cfg["token"], cfg.get("owner", "tigrisanimus"),
                      cfg.get("bus_repo", "crufflink-bus"))


def _cmd_status(b):
    cfg = configmod.load()
    print("crufflink %s" % __version__)
    print("bus repo : https://github.com/%s/%s (private)"
          % (cfg.get("owner"), cfg.get("bus_repo")))
    print("exec mode: %s  (change with: crufflink mode ask|auto|never)"
          % cfg.get("shell_mode", "ask"))
    try:
        _, _, content = b.read("presence.json")
        p = json.loads(content) if content else {}
        age = time.time() - p.get("ts", 0)
        print("you      : mode=%s, seen %ds ago" % (p.get("mode"), int(age)))
    except Exception as e:
        print("you      : presence unreadable (%s)" % e)
    try:
        _, _, content = b.read("cruff-presence.json")
        p = json.loads(content) if content else {}
        age = time.time() - p.get("ts", 0)
        if age < 600:
            print("cruffalo : watching the bus (seen %ds ago)" % int(age))
        else:
            print("cruffalo : not watching right now")
    except Exception:
        print("cruffalo : not watching right now")


def main():
    ap = argparse.ArgumentParser(
        prog="crufflink",
        description="Shared terminal + chat bridge to Cruffalo Crill.")
    ap.add_argument("--version", action="store_true",
                    help="print version and exit")
    sub = ap.add_subparsers(dest="cmd")

    p_pair = sub.add_parser("pair", help="link this machine to Cruffalo Crill")
    p_pair.add_argument("--owner", help="GitHub username")
    p_pair.add_argument("--bus-repo", help="private bus repo name")

    sub.add_parser("chat", help="chat with Cruffalo Crill")
    sub.add_parser("shell", help="shared terminal with Cruffalo Crill")

    p_send = sub.add_parser("send", help="send a one-shot chat message")
    p_send.add_argument("text", nargs="*", help="message text")

    p_mode = sub.add_parser("mode",
                            help="get/set approval mode for his shell commands")
    p_mode.add_argument("value", nargs="?", choices=["ask", "auto", "never"],
                        help="ask (default): approve each command; "
                             "auto: run immediately; never: decline all")

    sub.add_parser("status", help="show link status")

    args = ap.parse_args()

    if args.version:
        print("crufflink %s" % __version__)
        return 0

    if args.cmd == "pair":
        from . import pair as pairmod
        return pairmod.run(args)

    if args.cmd == "mode" and args.value is None:
        print(configmod.load().get("shell_mode", "ask"))
        return 0
    if args.cmd == "mode":
        cfg = configmod.load()
        cfg["shell_mode"] = args.value
        configmod.save(cfg)
        print("shell approval mode: %s" % args.value)
        return 0

    b = _bus_from_config()

    if args.cmd == "chat":
        chatmod.run_chat(b)
        return 0
    if args.cmd == "shell":
        from . import shell as shellmod
        shellmod.run_shell(b)
        return 0
    if args.cmd == "send":
        text = " ".join(args.text) if args.text else sys.stdin.read()
        if not text.strip():
            print("nothing to send.")
            return 1
        b.append("to-cruff.jsonl", chatmod.make_msg("chat", text=text))
        print("sent.")
        return 0
    if args.cmd == "status":
        _cmd_status(b)
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
