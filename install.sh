#!/usr/bin/env bash
# One-command installer for labnet. Run it from a clone of the repository:
#
#     ./install.sh
#
# What it does, skipping every step that is already done:
#   1. Installs Incus and pipx with apt            (asks for your sudo password)
#   2. Initialises Incus with default settings     (sudo)
#   3. Adds you to the 'incus-admin' group         (sudo)
#   4. Installs labnet, with the web UI, using pipx (no sudo)
#   5. Runs 'labnet install-check'
#
# It never touches existing Incus containers or networks. Every command run
# with sudo is printed first. Supported: Debian 13, Ubuntu 24.04 or newer
# (natively or under WSL2 with systemd enabled). See docs/install.md.
set -euo pipefail
cd "$(dirname "$0")"

say()  { printf '\n==> %s\n' "$*"; }
fail() { printf '\nError: %s\n' "$*" >&2; exit 1; }
run_sudo() { printf '    sudo %s\n' "$*"; sudo "$@"; }

[ "$(uname -s)" = "Linux" ] || fail "labnet runs on Linux. On Windows, use WSL2: see docs/install.md."
[ "$(id -u)" -ne 0 ] || fail "Run this as your normal user, not as root. It asks for sudo when needed."
command -v apt-get >/dev/null || fail "This installer supports Debian and Ubuntu (apt). See docs/install.md for other systems."

if grep -qi microsoft /proc/version && [ "$(ps -p 1 -o comm=)" != "systemd" ]; then
    fail "WSL is running without systemd, which Incus needs.
Add these two lines to /etc/wsl.conf (sudo nano /etc/wsl.conf):
    [boot]
    systemd=true
Then run 'wsl --shutdown' in Windows PowerShell, open this distro again and re-run ./install.sh"
fi

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || fail "labnet needs Python 3.11 or newer; this system has $(python3 --version 2>&1)."

# --- 1. system packages -----------------------------------------------------
missing=()
command -v incus >/dev/null || missing+=(incus)
command -v pipx  >/dev/null || missing+=(pipx)
if [ "${#missing[@]}" -gt 0 ]; then
    say "Installing system packages: ${missing[*]}"
    run_sudo apt-get update
    run_sudo apt-get install -y "${missing[@]}"
else
    say "Incus and pipx are already installed"
fi

# --- 2. initialise Incus ----------------------------------------------------
# 'sg' runs a command with the incus-admin group even if this login session
# does not have it yet (group changes normally need a new login).
incus_cmd() {
    if id -nG | tr ' ' '\n' | grep -qx incus-admin; then incus "$@"; else sudo incus "$@"; fi
}
if [ -z "$(incus_cmd storage list --format csv 2>/dev/null)" ]; then
    say "Initialising Incus with default settings"
    run_sudo incus admin init --minimal
else
    say "Incus is already initialised"
fi

# --- 3. group membership ----------------------------------------------------
need_relogin=no
if ! id -nG "$USER" | tr ' ' '\n' | grep -qx incus-admin; then
    say "Adding $USER to the incus-admin group (lets you use Incus without sudo)"
    run_sudo usermod -aG incus-admin "$USER"
    need_relogin=yes
elif ! id -nG | tr ' ' '\n' | grep -qx incus-admin; then
    need_relogin=yes   # in the group, but this session started before that
else
    say "You are already in the incus-admin group"
fi

# --- 4. labnet itself -------------------------------------------------------
say "Installing labnet with pipx"
pipx install --force ".[web]"
pipx ensurepath >/dev/null 2>&1 || true
LABNET="$HOME/.local/bin/labnet"

# --- 5. check ---------------------------------------------------------------
if [ "$need_relogin" = yes ]; then
    say "Almost done"
    echo "Your group membership changes at the next login. Log out and back in"
    echo "(on WSL: close the terminal, run 'wsl --shutdown' in PowerShell, open it again),"
    echo "then run:   labnet install-check"
else
    say "Checking the installation"
    "$LABNET" install-check || true
    echo
    echo "If 'labnet' is not found in a new terminal, run 'pipx ensurepath' and open a new one."
    echo "Next:   labnet up examples/office.yaml     then     labnet web"
fi
