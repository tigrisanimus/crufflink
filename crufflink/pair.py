"""crufflink pair — link this machine to Cruffalo Crill."""

import getpass

from . import bus as busmod
from . import config as configmod


def run(args):
    print("crufflink pair — link this machine to Cruffalo Crill")
    print()
    print("You need a GitHub personal access token with the 'repo' scope.")
    print("  Classic PAT: https://github.com/settings/tokens")
    print("  (If you made the 'ddburn push' token on this machine, you can reuse it.)")
    print("The token is stored only here, in ~/.config/crufflink/config.json (0600).")
    print()
    token = getpass.getpass("GitHub token (hidden input): ").strip()
    if not token:
        print("no token — aborting.")
        return 1
    default_owner = configmod.load().get("owner") or "tigrisanimus"
    owner = (args.owner or
             input("GitHub username [%s]: " % default_owner).strip() or default_owner)
    repo = args.bus_repo or "crufflink-bus"
    b = busmod.Bus(token, owner, repo)
    print("Checking token and bus repo...", flush=True)
    try:
        b.ensure()
    except busmod.BusError as e:
        print("pairing failed: %s" % e)
        return 1
    cfg = configmod.load()
    cfg.update({"token": token, "owner": owner, "bus_repo": repo,
                "shell_mode": cfg.get("shell_mode", "ask")})
    configmod.save(cfg)
    print()
    print("Paired! Private bus repo: https://github.com/%s/%s" % (owner, repo))
    print("Now tell Cruffalo you're paired so he switches on his listener, then run:")
    print("    crufflink chat     # chat with him")
    print("    crufflink shell    # shared terminal")
    return 0
