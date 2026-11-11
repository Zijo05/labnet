# Troubleshooting

Start with `labnet install-check`. It finds most setup problems and prints
the fix.

Each entry below says where it comes from: **met** while building or using
labnet (including the earlier prototype), or **known** behaviour of WSL or
Linux that was not reproduced here.

## WSL

### My lab is stopped, or my settings are gone, after I closed the terminal

*Met during development.*

WSL shuts a distro down a few seconds after its last terminal window closes.
That stops Incus and every lab node. When you open a terminal again the
nodes are stopped (labnet turns Incus's autostart off on purpose), and
anything that was only configured in memory is gone, exactly as after a
power cut.

- Bring the lab back with `labnet start <lab>`.
- Keep a WSL terminal open while you work. A running `labnet web` counts.
- Put configuration in files, not just commands. See
  [the user guide](user-guide.md#configuring-a-node-so-that-it-survives-a-reboot).

### Containers started by themselves and used memory after a restart

*Met with the prototype.*

Incus restarts whatever was running when it was shut down. labnet nodes are
created with `boot.autostart=false`, which tells Incus to leave them stopped,
so labs stay off until you run `labnet start`. Containers made by other
tools are not affected by this setting; for those, run
`incus config set <name> boot.autostart false`.

### WSL does not give memory back to Windows

*Known WSL behaviour; not something labnet controls.*

After a large lab, Windows Task Manager may show "Vmmem" still holding
gigabytes even though the lab is stopped. Linux keeps freed memory as file
cache, and WSL returns it to Windows slowly.

- `labnet stop <lab>` (or `down`) frees the memory inside Linux.
- `wsl --shutdown` in PowerShell returns everything to Windows at once.
- Cap what WSL may use with `memory=6GB` in `%UserProfile%\.wslconfig`
  ([install.md](install.md#13-give-wsl-enough-memory)).

### "systemd is not enabled" / Incus is not running

*Known; `install-check` detects it.*

Incus is a systemd service. Add to `/etc/wsl.conf`:

```ini
[boot]
systemd=true
```

then `wsl --shutdown` in PowerShell and reopen the distro.

### The web UI does not open in my Windows browser

*The working setup was met; the failing one is known.*

`labnet web` listens on 127.0.0.1 inside WSL. On the development machine,
`http://localhost:8080` in a Windows browser reached it, with
`networkingMode=mirrored` in `.wslconfig`. In WSL's default NAT mode,
localhost forwarding normally works as well. If it does not:

- make sure `labnet web` is still running in its terminal;
- try `http://127.0.0.1:8080`;
- add `networkingMode=mirrored` under `[wsl2]` in `%UserProfile%\.wslconfig`
  and run `wsl --shutdown`.

Do not "fix" it with `labnet web --host 0.0.0.0`; that exposes root consoles
to your network.

### The topology picture in the web UI is blank

*Met during development.*

Browsers pause drawing for pages that are in the background or hidden. The
picture appears as soon as the tab is in front.

## Incus and permissions

### "permission denied" talking to Incus

*Known; `install-check` detects it.*

Your user is not in the `incus-admin` group, or was added after this login
session started.

```bash
sudo usermod -aG incus-admin "$USER"
```

then log out and in again (on WSL: `wsl --shutdown`, reopen). `newgrp
incus-admin` works for the current terminal only.

### "Incus has no storage pool"

*Known; `install-check` detects it.*

Incus was installed but never initialised: `sudo incus admin init --minimal`.

### `up` fails: an instance or network name "already exists and is not part of the lab"

*By design; covered by a test.*

Something in Incus that labnet did not create has the name labnet needs
(`<lab>-<node>`). labnet will not touch it. Change the lab or node name in
your topology file, or rename the other container.

## Building labs

### The first `up` is slow or fails while "installing packages"

*Slowness met; failure known.*

The first `up` downloads a Debian image and installs packages into three
node images. That took about two minutes here and needs working internet
access and DNS inside WSL. If it fails, check `ping deb.debian.org` on the
host, then run `labnet images build`. Later `up` runs do not need the
internet.

### `up` says nodes "did not finish booting"

*Not met; the usual cause is known.*

Usually the host is out of memory. Check `free -m`, stop other labs
(`labnet list`, `labnet stop <lab>`), or give WSL more memory. To look at one
node: `incus exec <lab>-<node> -- systemctl --failed`.

## Inside the lab

### I logged in over SSH, changed the network settings, and the session froze

*Met with the prototype.*

You cut the branch you were sitting on. Use the console instead, which does
not depend on the node's network: `labnet shell <node>`, or click the node
in the web UI. It always works, whatever state the node's networking is in.

### SSH asks for root's password and none works

*Met with the prototype.*

There is none. The `root` account has no password, so password logins as
root are impossible; you get root through the console. To use SSH between
nodes, create a user with a password or install a key. See
[How device access works](user-guide.md#how-device-access-works).

### "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED"

*Met with the prototype.*

You rebuilt a lab, the new node generated new SSH host keys, and your SSH
client remembers the old one for that address. Remove the stale entry on the
client and connect again:

```bash
ssh-keygen -R 192.168.10.2
```

### A PC does not get an address right after I start the DHCP server

*Met during development.*

A DHCP client that gets no answer waits longer between each attempt, up to
about a minute. The PC will ask again by itself; to ask now:

```bash
networkctl reconfigure eth0
```

The same applies after changing the server's options: clients keep their
old lease until it is renewed.

### dnsmasq will not start: "failed to create listening socket ... Address already in use"

*Met during development.*

By default dnsmasq claims port 53 on every address, but `systemd-resolved`
already listens on 127.0.0.53. Tell dnsmasq to listen only where you want
it:

```
interface=eth0
bind-dynamic
```

If you only want DHCP from dnsmasq, `port=0` switches its DNS part off.

### My address or route disappears after a while, or after a reboot

*Met during development (the reboot case).*

Settings made with `ip address add` or `ip route add` live in memory. On a
PC, `systemd-networkd` also manages the interface as a DHCP client and may
reset it. Write a `.network` file instead; see
[the user guide](user-guide.md#configuring-a-node-so-that-it-survives-a-reboot).

### `sysctl --system` prints "permission denied on key kernel...."

*Met during development.*

Harmless. A container may change its own network settings (such as
`net.ipv4.ip_forward`) but not kernel-wide ones, and `sysctl --system` tries
them all. The network settings are still applied. To set one key without the
noise: `sysctl -w net.ipv4.ip_forward=1`.

### A server or PC forwards packets although it is not a router

*Met during development; fixed in the images.*

A new container inherits the host's forwarding setting, and the host
forwards. labnet's images therefore set `net.ipv4.ip_forward` explicitly: 1
on routers, 0 elsewhere (`/etc/sysctl.d/90-labnet.conf`). Nodes that use a
custom `image:` do not get this file.

### Nodes on one switch cannot reach each other

*Known; could not be reproduced here because Docker does not run inside this
distro.*

If Docker Engine (or another firewall with a "drop" forwarding policy) runs
in the same Linux system as Incus, it can drop traffic crossing labnet's
bridges, because the kernel passes bridged packets through the firewall
(`net.bridge.bridge-nf-call-iptables = 1`). `install-check` warns when it
sees a Docker socket. The usual fixes are to run Docker elsewhere, or to
allow forwarding for the lab bridges, for example:

```bash
sudo nft insert rule ip filter FORWARD iifname "ln-*" oifname "ln-*" accept
```

(adjust to your firewall; this rule was not tested).

### `labnet capture ... -c N` never finishes

*Met during development.*

`-c N` waits for N matching packets. If fewer arrive, press Ctrl+C.

### Link delay or loss seems to affect only one direction

*By design.*

`labnet link set` shapes packets the node **sends** on that link. Set it on
the node at the other end as well to affect both directions.
