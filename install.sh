#!/usr/bin/env bash
# labnet installer. Install straight from GitHub with one line:
#
#     wget -qO- https://raw.githubusercontent.com/Zijo05/labnet/main/install.sh | bash
#
# or, from a clone of the repository:
#
#     ./install.sh
#
# What it does, skipping every step that is already done:
#   1. Installs Incus, pipx and python3-venv with apt   (asks for your sudo password)
#   2. Initialises Incus with default settings           (sudo)
#   3. Adds you to the 'incus-admin' group               (sudo)
#   4. Installs labnet, with the web UI, using pipx      (no sudo)
#   5. Runs 'labnet install-check'
#
# It never touches existing Incus containers or networks. Every command run
# with sudo is printed first. Supported: Debian 12/13 and Ubuntu 24.04 or
# newer, natively or under WSL2 with systemd. See docs/install.md.
#
# Settings (environment variables):
#   LABNET_REF   which branch or tag to install from GitHub (default: main)
#   LABNET_REPO  which GitHub repository (default: Zijo05/labnet)
#
# Everything is inside main(), which is called on the last line. When the
# script arrives through a pipe, bash therefore reads all of it before
# running anything, so a download cut short cannot run half a script.

set -euo pipefail

LABNET_REPO="${LABNET_REPO:-Zijo05/labnet}"
LABNET_REF="${LABNET_REF:-main}"

say()  { printf '\n==> %s\n' "$*"; }
fail() { printf '\nError: %s\n' "$*" >&2; exit 1; }
run_sudo() { printf '    sudo %s\n' "$*"; sudo "$@"; }
# No "cmd | grep -q" pipelines in this script: with pipefail, grep -q exits
# at the first match, the writer gets SIGPIPE, and the pipeline "fails".
in_group_now() { [[ " $(id -nG) " == *" incus-admin "* ]]; }
in_group_file() { [[ " $(id -nG "$USER") " == *" incus-admin "* ]]; }

# Print the repository directory if this script is running from a clone.
local_checkout() {
    local source="${BASH_SOURCE[0]:-}"
    [ -n "$source" ] && [ -f "$source" ] || return 1
    local dir
    dir="$(cd "$(dirname "$source")" && pwd)"
    grep -qs '^name = "labnet"' "$dir/pyproject.toml" || return 1
    echo "$dir"
}

check_system() {
    [ "$(uname -s)" = "Linux" ] || fail "labnet runs on Linux. On Windows, use WSL2: see docs/install.md."
    [ "$(id -u)" -ne 0 ] || fail "Run this as your normal user, not as root. It asks for sudo when needed."
    command -v apt-get >/dev/null || fail "This installer supports Debian and Ubuntu (apt). See docs/install.md."
    command -v sudo >/dev/null || fail "sudo is not installed. As root: apt install sudo && usermod -aG sudo $USER"
    if grep -qi microsoft /proc/version && [ "$(ps -p 1 -o comm=)" != "systemd" ]; then
        fail "WSL is running without systemd, which Incus needs.
Add these two lines to /etc/wsl.conf (sudo nano /etc/wsl.conf):
    [boot]
    systemd=true
Then run 'wsl --shutdown' in Windows PowerShell, open this distro again and run the installer again."
    fi
}

install_packages() {
    local missing=()
    command -v incus >/dev/null || missing+=(incus)
    command -v pipx >/dev/null || missing+=(pipx)
    python3 -c 'import ensurepip' 2>/dev/null || missing+=(python3-venv)
    if [ "${#missing[@]}" -gt 0 ]; then
        say "Installing system packages: ${missing[*]}"
        run_sudo apt-get update
        if [[ " ${missing[*]} " == *" incus "* ]] \
            && [[ "$(apt-cache policy incus 2>/dev/null)" != *"Candidate: "[0-9]* ]]; then
            fail "Incus is not available from your package sources.
On Debian 12 it is in bookworm-backports: enable that repository, run
'sudo apt install -t bookworm-backports incus', then run this installer again."
        fi
        run_sudo apt-get install -y "${missing[@]}"
    else
        say "Incus, pipx and python3-venv are already installed"
    fi
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
        || fail "labnet needs Python 3.11 or newer; this system has $(python3 --version 2>&1)."
}

init_incus() {
    local pools
    if in_group_now; then
        pools="$(incus storage list --format csv 2>/dev/null || true)"
    else
        pools="$(sudo incus storage list --format csv 2>/dev/null || true)"
    fi
    if [ -z "$pools" ]; then
        say "Initialising Incus with default settings"
        run_sudo incus admin init --minimal
    else
        say "Incus is already initialised"
    fi
}

join_group() {
    if in_group_file; then
        say "You are in the incus-admin group"
    else
        say "Adding $USER to the incus-admin group (lets you use Incus without sudo)"
        run_sudo usermod -aG incus-admin "$USER"
    fi
}

install_labnet() {
    local checkout spec
    if checkout="$(local_checkout)"; then
        spec="$checkout[web]"
        say "Installing labnet from this folder ($checkout)"
    else
        # A tarball of the branch or tag, so git does not need to be installed.
        spec="labnet[web] @ https://github.com/$LABNET_REPO/archive/$LABNET_REF.tar.gz"
        say "Installing labnet from github.com/$LABNET_REPO ($LABNET_REF)"
    fi
    pipx install --force "$spec"
    pipx ensurepath >/dev/null 2>&1 || true
}

final_check() {
    local labnet="$HOME/.local/bin/labnet"
    say "Checking the installation"
    if in_group_now; then
        "$labnet" install-check || true
    else
        # This terminal started before the group change. 'sg' runs one
        # command with the new group so the check can talk to Incus now.
        sg incus-admin -c "'$labnet' install-check" || true
    fi

    say "Done"
    if ! in_group_now; then
        echo "Open a NEW terminal before using labnet, so that the incus-admin group applies."
        echo "(On WSL, if Incus still says 'permission denied', run 'wsl --shutdown' in"
        echo "PowerShell and open the distro again.)"
    elif ! command -v labnet >/dev/null; then
        echo "Open a new terminal so that 'labnet' is on your PATH, or run:"
        echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
    fi
    echo
    echo "Then try:   labnet up office     (the first run builds the node images, ~2 minutes)"
    echo "            labnet web           and open http://localhost:8080"
}

main() {
    check_system
    install_packages
    init_incus
    join_group
    install_labnet
    final_check
}

main "$@"
