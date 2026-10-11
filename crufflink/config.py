"""Local config: ~/.config/crufflink/config.json (mode 0600 — holds your GitHub token)."""

import json
import os

DIR = os.path.expanduser("~/.config/crufflink")
PATH = os.path.join(DIR, "config.json")


def load():
    try:
        with open(PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(cfg):
    os.makedirs(DIR, exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, PATH)
