"""The message bus: a private GitHub repo holding append-only JSONL files.

Files under bus/ in the repo:
  to-rob.jsonl    — written by Cruffalo, read by you   (chat replies, exec requests)
  to-cruff.jsonl  — written by you, read by Cruffalo   (chat messages, shell output)
  presence.json   — your client's heartbeat (overwritten)
  cruff-presence.json — Cruffalo's heartbeat (overwritten)

Single-writer per file, so appends are a safe read-modify-write.
Pure stdlib (urllib) — no dependencies.
"""

import base64
import json
import time
import urllib.error
import urllib.request

API = "https://api.github.com"
BUS_DIR = "bus"
MAX_BYTES = 256 * 1024  # rotate a file past this size


class BusError(Exception):
    pass


class Bus:
    def __init__(self, token, owner, repo):
        self.token = token
        self.owner = owner
        self.repo = repo

    # -- low-level -----------------------------------------------------
    def _req(self, method, path, data=None, etag=None):
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(API + path, data=body, method=method)
        req.add_header("Authorization", "Bearer " + self.token)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if body:
            req.add_header("Content-Type", "application/json")
        if etag:
            req.add_header("If-None-Match", etag)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def _path(self, name):
        return "/repos/%s/%s/contents/%s/%s" % (self.owner, self.repo, BUS_DIR, name)

    def _put(self, name, content, sha, message):
        data = {"message": message,
                "content": base64.b64encode(content.encode()).decode()}
        if sha:
            data["sha"] = sha
        code, _, body = self._req("PUT", self._path(name), data)
        if code not in (200, 201):
            raise BusError("PUT %s: HTTP %s %s" % (name, code, body[:200]))

    # -- setup ---------------------------------------------------------
    def ensure(self):
        """Create the private bus repo + files if missing. Idempotent."""
        code, _, body = self._req("GET", "/repos/%s/%s" % (self.owner, self.repo))
        if code == 404:
            code, _, body = self._req("POST", "/user/repos", {
                "name": self.repo,
                "private": True,
                "auto_init": True,
                "description": "Private message bus for crufflink (Cruffalo Crill connector).",
            })
            if code != 201:
                raise BusError("create repo failed: HTTP %s %s" % (code, body[:200]))
            time.sleep(2)
        elif code != 200:
            raise BusError("repo check failed: HTTP %s" % code)
        seeds = {
            "to-rob.jsonl": "",
            "to-cruff.jsonl": "",
            "presence.json": json.dumps({"who": "rob", "mode": "none", "ts": 0}),
        }
        for name, init in seeds.items():
            code, _, _ = self._req("GET", self._path(name))
            if code == 404:
                self._put(name, init, None, "crufflink: init " + name)

    # -- reads / writes -------------------------------------------------
    def read(self, name):
        """Return (sha, etag, text) or (None, None, None) if missing."""
        code, headers, body = self._req("GET", self._path(name))
        if code == 404:
            return None, None, None
        if code != 200:
            raise BusError("GET %s: HTTP %s" % (name, code))
        meta = json.loads(body)
        return meta["sha"], headers.get("ETag"), \
            base64.b64decode(meta["content"]).decode()

    def append(self, name, obj):
        """Append one JSON object as a line. Retries on sha conflict."""
        line = json.dumps(obj, separators=(",", ":")) + "\n"
        for _ in range(3):
            sha, _, content = self.read(name)
            if content is None:
                content, sha = "", None
            new = content + line
            if len(new.encode()) > MAX_BYTES:
                lines = new.splitlines(keepends=True)
                keep, size = [], 0
                for ln in reversed(lines):
                    size += len(ln.encode())
                    if size > MAX_BYTES // 4:
                        break
                    keep.append(ln)
                new = "".join(reversed(keep))
            try:
                self._put(name, new, sha, "crufflink: append " + name)
                return
            except BusError:
                time.sleep(1)
        raise BusError("append %s: too many conflicts" % name)

    def write_json(self, name, obj):
        for _ in range(3):
            sha, _, _ = self.read(name)
            try:
                self._put(name, json.dumps(obj), sha, "crufflink: update " + name)
                return
            except BusError:
                time.sleep(1)
        raise BusError("write %s: too many conflicts" % name)

    def poll_lines(self, name, seen_ids, timeout=30):
        """Return new message dicts (ids not in seen_ids), waiting up to timeout."""
        deadline = time.time() + timeout
        etag = None
        while True:
            code, headers, body = self._req("GET", self._path(name), etag=etag)
            if code == 200:
                etag = headers.get("ETag")
                meta = json.loads(body)
                content = base64.b64decode(meta["content"]).decode()
                out = []
                for ln in content.splitlines():
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        m = json.loads(ln)
                    except ValueError:
                        continue
                    if m.get("id") not in seen_ids:
                        out.append(m)
                if out:
                    return out
            elif code not in (304, 404):
                raise BusError("poll %s: HTTP %s" % (name, code))
            if time.time() >= deadline:
                return []
            time.sleep(3)
