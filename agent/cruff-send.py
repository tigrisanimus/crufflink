#!/usr/bin/env python3
"""Cruffalo-side sender for crufflink.

Appends a message to bus/to-rob.jsonl (the half of the bus Rob reads).
Runs on Cruffalo's machine, using the connected GitHub credential.

Usage:
    cruff-send.py chat "hello from the other side"
    cruff-send.py exec "ls ~"
"""
import base64
import json
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
from dynamic_credentials import add_surrogate_to_request  # noqa: E402

API = "https://api.github.com"
OWNER = "tigrisanimus"
REPO = "crufflink-bus"


def api(method, path, data=None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(data).encode() if data is not None else None,
        method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    add_surrogate_to_request(req, "custom.github", allowed_hosts=["api.github.com"])
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def read_file(path):
    code, body = api("GET", "/repos/%s/%s/contents/%s" % (OWNER, REPO, path))
    if code == 404:
        return None, ""
    if code != 200:
        raise RuntimeError("bus read failed: HTTP %s" % code)
    meta = json.loads(body)
    return meta["sha"], base64.b64decode(meta["content"]).decode()


def append_line(path, obj):
    line = json.dumps(obj, separators=(",", ":")) + "\n"
    for _ in range(3):
        sha, content = read_file(path)
        data = {"message": "crufflink: %s" % obj["kind"],
                "content": base64.b64encode((content + line).encode()).decode()}
        if sha:
            data["sha"] = sha
        code, body = api("PUT", "/repos/%s/%s/contents/%s" % (OWNER, REPO, path), data)
        if code in (200, 201):
            return
    raise RuntimeError("bus append failed: HTTP %s %s" % (code, body[:200]))


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ("chat", "exec"):
        sys.exit("usage: cruff-send.py chat|exec <text>")
    kind = sys.argv[1]
    payload = " ".join(sys.argv[2:])
    m = {"id": uuid.uuid4().hex,
         "ts": datetime.now(timezone.utc).isoformat(),
         "from": "cruff", "kind": kind}
    m["text" if kind == "chat" else "cmd"] = payload
    append_line("bus/to-rob.jsonl", m)
    print("sent %s %s" % (kind, m["id"][:8]))


if __name__ == "__main__":
    main()
