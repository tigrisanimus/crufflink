#!/usr/bin/env python3
"""Cruffalo-side bus watcher for crufflink.

Reads bus/to-cruff.jsonl (the half Rob writes), prints anything new since
the watermark, updates the watermark, and refreshes Cruffalo's presence.
Silent (no output) when there's nothing new — the cron agent ends quietly.

Runs on Cruffalo's machine, using the connected GitHub credential.
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
from dynamic_credentials import add_surrogate_to_request  # noqa: E402

API = "https://api.github.com"
OWNER = "tigrisanimus"
REPO = "crufflink-bus"
STATE_DIR = os.path.expanduser("~/workspace/crufflink-agent")
WM = os.path.join(STATE_DIR, "watermark.json")


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


def get_file(path):
    code, body = api("GET", "/repos/%s/%s/contents/%s" % (OWNER, REPO, path))
    if code == 404:
        return None, None
    if code != 200:
        raise RuntimeError("bus read failed: HTTP %s" % code)
    meta = json.loads(body)
    return meta["sha"], base64.b64decode(meta["content"]).decode()


def put_file(path, content, sha, message):
    data = {"message": message,
            "content": base64.b64encode(content.encode()).decode()}
    if sha:
        data["sha"] = sha
    code, body = api("PUT", "/repos/%s/%s/contents/%s" % (OWNER, REPO, path), data)
    if code not in (200, 201):
        raise RuntimeError("bus write failed: HTTP %s %s" % (code, body[:200]))


def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    seen = set()
    if os.path.exists(WM):
        try:
            seen = set(json.load(open(WM)))
        except Exception:
            pass

    sha, content = get_file("bus/to-cruff.jsonl")
    if content is None:
        return  # bus repo not set up yet — silent

    new = []
    for ln in content.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            m = json.loads(ln)
        except Exception:
            continue
        if m.get("id") not in seen and m.get("from") == "rob":
            new.append(m)
            seen.add(m["id"])

    # Is his client currently open?
    active = False
    try:
        _, pcontent = get_file("bus/presence.json")
        if pcontent:
            p = json.loads(pcontent)
            active = (time.time() - p.get("ts", 0)) < 600
    except Exception:
        pass

    # refresh my presence (best effort, at most every 5 min to avoid commit spam)
    try:
        csha, cpcontent = get_file("bus/cruff-presence.json")
        last = 0
        if cpcontent:
            try:
                last = json.loads(cpcontent).get("ts", 0)
            except Exception:
                pass
        if time.time() - last > 300:
            put_file("bus/cruff-presence.json",
                     json.dumps({"who": "cruff", "ts": time.time()}),
                     csha, "crufflink: cruff presence")
    except Exception:
        pass

    with open(WM, "w") as f:
        json.dump(sorted(seen)[-500:], f)

    if not new:
        return
    print("ACTIVE=%s" % active)
    for m in new:
        kind = m.get("kind")
        if kind == "chat":
            print("CHAT @%s:\n%s\n---" % (m.get("ts"), m.get("text", "")))
        elif kind == "out":
            txt = ""
            d = m.get("data", "")
            if d:
                try:
                    txt = base64.b64decode(d).decode(errors="replace")
                except Exception:
                    txt = "<undecodable %d chars>" % len(d)
            if len(txt) > 2000:
                txt = txt[:2000] + "\n…(truncated)"
            print("OUT stream=%s ref=%s code=%s:\n%s\n---"
                  % (m.get("stream"), str(m.get("ref", ""))[:8],
                     m.get("code", ""), txt))
        elif kind == "ack":
            print("ACK ref=%s: %s\n---"
                  % (str(m.get("ref", ""))[:8], m.get("note", "")))
        else:
            print("%s: %s\n---" % (str(kind).upper(), json.dumps(m)[:500]))


if __name__ == "__main__":
    main()
