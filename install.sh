#!/bin/bash
# crufflink installer — pure Python stdlib, zero dependencies.
set -e
cd "$(dirname "$0")"

echo "== crufflink installer =="
python3 --version

installed=""
if python3 -m pip install --user . >/dev/null 2>&1; then
    installed="pip"
else
    # fallback: plain copy + launcher, no pip needed
    mkdir -p ~/.local/lib ~/.local/bin
    rm -rf ~/.local/lib/crufflink
    cp -r crufflink ~/.local/lib/crufflink
    cat > ~/.local/bin/crufflink <<'LAUNCHER'
#!/bin/sh
# crufflink launcher (pip-less install).
# Uses python3 -c (not a stdin heredoc) so stdin stays on your terminal —
# otherwise input() prompts hit EOF.
exec python3 -c "import os, sys; sys.path.insert(0, os.path.expanduser('~/.local/lib')); from crufflink.__main__ import main; sys.exit(main())" "$@"
LAUNCHER
    chmod +x ~/.local/bin/crufflink
    installed="copy"
fi

case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) echo "NOTE: ~/.local/bin is not on your PATH — add it, then re-run." ;;
esac

if command -v crufflink >/dev/null 2>&1; then
    crufflink --version
    echo "installed via $installed. next step:"
    echo "    crufflink pair"
else
    echo "installed to ~/.local/bin/crufflink but it's not on PATH yet."
    echo "add ~/.local/bin to PATH, then run:  crufflink pair"
fi
