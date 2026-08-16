#!/bin/bash
# Install the agent CLI into the home directory as ~/opencode_cli.py
set -e

cd "$(dirname "$0")"

# copy to the home directory
cp ./pickel.py ~/opencode_cli.py

# also install the proxy verifier tool
cp ./check_proxies.py ~/check_proxies.py

# The dev copy saves sessions in a relative "session/" folder, but the
# installed copy must keep using the absolute ~/UserDirs/session path
# (independent of the working directory, survives /cd). update.sh used to
# clobber this customization — restore it after every update.
python3 - "$HOME" <<'EOF'
import sys
from pathlib import Path

target = Path(sys.argv[1]) / "opencode_cli.py"
src = target.read_text(encoding="utf-8")

old = '''# Sessions are saved as JSON files inside this folder — one file per
# conversation, named after that session's UUID (session/<uuid>.json).
SESSION_DIR        = "session"'''
new = '''# Absolute path under ~/UserDirs/session (independent of the working directory).
SESSION_DIR        = str(Path.home() / "UserDirs" / "session")'''

if old in src:
    target.write_text(src.replace(old, new), encoding="utf-8")
    echo_msg = "session dir customization preserved"
else:
    echo_msg = "note: SESSION_DIR block not found — left as-is"
print(echo_msg)
EOF

# optionally install the dependencies
if [ -f requirements.txt ]; then
    echo "dependencies: pip install -r requirements.txt  (httpx, rich)"
fi
