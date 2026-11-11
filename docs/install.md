# Installing labnet

labnet runs on Linux and needs [Incus](https://linuxcontainers.org/incus/),
the container manager it builds labs with. On Windows it runs inside WSL2.

| Host | Status |
|------|--------|
| Debian 13 under WSL2 (Windows 11) | **Developed and fully tested here.** |
| Ubuntu 26.04 under WSL2 | Unit tests and `install-check` pass (Python 3.14). Labs were not run on it. |
| Ubuntu 24.04, Debian 12, native (non-WSL) Debian or Ubuntu | Expected to work; not tested. |

If you already have Linux with systemd, skip to [Install labnet](#2-install-labnet).

## 1. Windows 11: set up WSL2 from zero

### 1.1 Install WSL and a distro

Open **PowerShell as Administrator** and run:

```powershell
wsl --install -d Debian
```

Restart if asked, then start "Debian" from the Start menu and create your
Linux user. (Ubuntu works too: `wsl --install -d Ubuntu-24.04`. This guide
uses Debian because that is where labnet was tested.)

Check that the distro uses WSL version 2:

```powershell
wsl -l -v
```

### 1.2 Enable systemd

Incus runs as a systemd service. Recent WSL enables systemd by default; check
inside the distro:

```bash
ps -p 1 -o comm=
```

If this prints `systemd`, you are done. If it prints `init`, create or edit
`/etc/wsl.conf` (`sudo nano /etc/wsl.conf`) so that it contains:

```ini
[boot]
systemd=true
```

Then, in PowerShell, run `wsl --shutdown` and open the distro again.

### 1.3 Give WSL enough memory

WSL limits how much memory Linux may use. A lab of 10 small nodes needs
about 1 GB; the OSPF scenario about 0.5 GB. Set the limit in the file
`%UserProfile%\.wslconfig` on the **Windows** side (create it with Notepad
if it does not exist):

```ini
[wsl2]
memory=6GB
processors=4
```

Apply it with `wsl --shutdown` in PowerShell. This file affects every WSL
distro, so pick a value that leaves Windows enough memory for itself.

labnet was developed with `networkingMode=mirrored` also set in that file.
That is what makes `http://localhost:8080` in a Windows browser reach the
web UI inside WSL. With the default NAT mode, WSL normally forwards
`localhost` as well; if the page does not open, see
[troubleshooting](troubleshooting.md#the-web-ui-does-not-open-in-my-windows-browser).

## 2. Install labnet

Inside Linux (the WSL distro, on Windows):

```bash
sudo apt update && sudo apt install -y git
git clone <repository-url> labnet
cd labnet
./install.sh
```

`install.sh` does five things and skips any that are already done:

1. installs Incus and pipx with apt (asks for your sudo password);
2. initialises Incus with default settings (`incus admin init --minimal`);
3. adds your user to the `incus-admin` group, so Incus works without sudo;
4. installs labnet and its web UI with pipx, into its own virtual
   environment, and puts the `labnet` command in `~/.local/bin`;
5. runs `labnet install-check`.

Every command it runs with sudo is printed first. It does not touch existing
Incus containers or networks.

If step 3 added you to the group, log out and in again (on WSL: close the
terminal, `wsl --shutdown` in PowerShell, reopen), then run
`labnet install-check` yourself.

> What was tested: on the development machine Incus was already installed and
> initialised, so steps 1 to 3 were skipped there. Steps 4 and 5 were run
> for real. The apt and group steps are standard commands but were not
> exercised on a blank system.

### Checking the installation

```bash
labnet install-check
```

prints one line per requirement, and for anything that is wrong, the exact
command that fixes it:

```
[ ok ] Operating system: Debian GNU/Linux 13 (trixie) on WSL2
[ ok ] WSL: systemd is running (Incus needs it)
[ ok ] Incus is installed
[ ok ] Incus daemon is reachable (version 6.0.4)
[ ok ] Incus storage pool 'default' is available
[info] Role images not built yet: pc, router, server
       -> Nothing to do: 'labnet up' builds them when first needed ...
[ ok ] Memory: 5928 MiB available to Linux
[ ok ] Kernel supports link delay and loss (netem)
[ ok ] Web UI dependencies are installed
[ ok ] Ansible is installed (for 'labnet export ansible')
```

### First lab

```bash
labnet up examples/office.yaml     # first run builds the node images: about 2 minutes
labnet web                         # then open http://localhost:8080
```

The first `up` downloads a Debian image and builds labnet's three node
images (PC, router, server), which needs internet access. After that, labs
start in seconds and work offline (except scenarios that use the internet
on purpose).

## Installing by hand

If you would rather not run the script:

```bash
sudo apt update
sudo apt install -y incus pipx
sudo incus admin init --minimal
sudo usermod -aG incus-admin "$USER"      # then log out and in again
pipx install '.[web]'                     # from the repository directory
pipx ensurepath                           # puts ~/.local/bin on your PATH
labnet install-check
```

**Debian 12** does not have Incus in its main archive. Enable
`bookworm-backports` and install it from there
(`sudo apt install -t bookworm-backports incus`). Not tested.

**Optional:** `sudo apt install -y ansible` if you want to use
`labnet export ansible`.

## For development

```bash
./dev.sh setup          # virtualenv in ~/.venvs/labnet-dev, editable install
./dev.sh test           # unit tests, no Incus needed (about 3 seconds)
./dev.sh integration    # integration tests, real containers (about 20 minutes)
./dev.sh lint           # ruff format check and linter
```

## Updating and removing

```bash
git pull && pipx install --force '.[web]'      # update
labnet images build --rebuild                  # if the changelog says the images changed
```

To remove everything labnet created:

```bash
labnet list                       # see which labs exist
labnet down <lab> --yes           # for each lab
labnet images remove --yes        # the three node images
pipx uninstall labnet
```

Incus itself and anything else in it are left alone.
