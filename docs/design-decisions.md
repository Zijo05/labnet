# Design decisions

This file records what was checked on the real machine before building labnet,
and each significant choice with its reason and the alternatives that were
rejected. Where the original plan (see `HANDOFF` notes in `PROJECT_LOG.md`)
turned out to be wrong, the machine won and the change is written down here.

## The machine the project was built and tested on

| Item | Value |
|---|---|
| Windows | Windows 11 Home, build 26200, WSL 3.0.3 |
| WSL kernel | 6.18.54.1-microsoft-standard-WSL2, x86_64 |
| Host distro used | **Debian 13 (trixie)** under WSL2, systemd enabled |
| Incus | 6.0.4 from the Debian archive, storage backend `dir`, firewall driver `nftables` |
| Python | 3.13.5 |
| WSL settings | 6 GB memory, 4 processors, `networkingMode=mirrored` |

The plan said the Ubuntu distro held Incus and the prototype. In fact the
**Debian** distro did (Incus, 20 stopped `pc01`..`pc20` containers, network
`ured`, profile `lab`), and the Ubuntu distro was a bare Ubuntu 26.04 without
Incus. Development therefore happened on Debian 13, the owner's preferred
distro. The prototype lab was not touched at any point.

## Part 1: questions answered by experiment

The scripts are in [`spikes/`](../spikes). Each used throwaway objects named
`lnspike-*` and was cleaned up afterwards.

### 1. How to drive Incus from Python

**Decision: run the `incus` command-line tool and read its JSON output.**

`incus list --format json` returns everything labnet needs in one call:
instance config, devices, state, and per-interface addresses. `incus query
/1.0` gives the server version. Creating a container with all its config and
devices is one command (`incus init IMAGE NAME --no-profiles`, with a YAML
definition on standard input).

Rejected alternatives:

- *REST API over the unix socket.* Works, but creating things is asynchronous
  (you get an operation and must wait for it), so the code needs an HTTP
  client for unix sockets plus operation polling. More code, no benefit here.
- *A client library (pylxd).* It is written for LXD, not Incus, and the two
  have been drifting apart since the fork. Not worth the risk.

Shelling out has a cost of roughly 30–60 ms per call, which does not matter
for a tool that a person drives by hand. All calls go through one small
module, [`incus.py`](../src/labnet/incus.py).

### 2. Does an address-less Incus bridge behave as a plain switch?

**Yes.** A network created with `ipv4.address=none ipv6.address=none` gets no
address on the host, and Incus starts no DHCP or DNS service on it. Two
containers with hand-assigned addresses on it ping each other in under 0.2 ms.
The host is not reachable from it over IPv4.

One thing to know: the `br_netfilter` kernel module is loaded on this machine
(`net.bridge.bridge-nf-call-iptables = 1`), so bridged frames also pass
through the host's firewall. With Incus alone that is harmless. If a firewall
with a "drop" forwarding policy runs in the same Linux system (Docker Engine
is the usual one), it can drop traffic between nodes on the same switch. This
could not be reproduced here because Docker does not run inside this distro;
`labnet install-check` warns when it sees a Docker socket, and the fix is in
`docs/troubleshooting.md`.

### 3. Can a multi-NIC unprivileged container route and NAT?

**Yes.** `sysctl net.ipv4.ip_forward=1` works inside an unprivileged
container (the setting belongs to the container's network namespace). A
router container forwarded between two LANs, and an nftables `masquerade`
rule on its WAN interface gave LAN clients working internet access
(`ping 1.1.1.1`, about 18 ms).

### 4. Can a DHCP server in one container serve clients in others?

**Yes.** dnsmasq in the router container leased `192.168.20.140` to a client
on the same bridge, including the default route option, and the client then
reached the internet through the router's NAT.

### 5. Does FRR run OSPF inside a container?

**Yes.** FRR 10.3 installs from the Debian archive, starts with `ospfd`
enabled, and accepts OSPF configuration through `vtysh`. A full three-router
OSPF lab is exercised as scenario 7.

### 6. VLAN-aware bridging and Open vSwitch

**Both are available in the kernel** (`CONFIG_BRIDGE_VLAN_FILTERING=y`,
`CONFIG_VLAN_8021Q=m`, `CONFIG_OPENVSWITCH=m`). Creating an 802.1Q
sub-interface inside a container works, and Incus accepts the `vlan` option on
a bridged NIC of a native Linux bridge. The Open vSwitch user-space tools are
not installed and are not needed: native bridges are enough.

A second spike (`spikes/03-vlan.sh`) then tested the behaviour. Incus turns
on VLAN filtering on the bridge as soon as a port has a VLAN setting. Two
access ports in VLAN 10 reached each other; a port in VLAN 20 was unreachable
from them **even with all three hosts in one IP subnet**; a trunk port with
802.1Q sub-interfaces reached both VLANs, and its untagged traffic reached
neither. That is a VLAN-aware switch, so VLAN support was built (see "VLANs
are port settings in the topology" below).

### 7. Attaching a browser terminal to a container

**Decision: a pseudo-terminal (PTY) wrapped around `incus exec`.**

A 20-line Python test opened a PTY, started `incus exec NODE -- bash -l` on
it, set the window size, sent a command and read the output: the shell saw a
real terminal (`/dev/pts/1`, 30x100). The web backend forwards bytes between
that PTY and a WebSocket.

Rejected alternative: Incus's own exec-over-WebSocket API. It needs the REST
API, three WebSockets per session (data, control, and an operation to track)
and TLS or unix-socket plumbing in the browser path. The PTY approach reuses
exactly the command the CLI's `labnet shell` runs.

### 8. Does `tc netem` work on container interfaces?

**Yes, from inside the container.** `tc qdisc add dev eth1 root netem delay
50ms` succeeded in an unprivileged container, and the kernel loaded the
`sch_netem` module on demand. labnet applies netem through `incus exec` on the
node's own interface, so it needs no root on the host. The consequence is
that shaping applies to packets the node *sends* on that link.

### Other facts found during the spikes

- **WSL stops the distro when the last terminal closes.** A few seconds after
  the last `wsl.exe` session ends, WSL shuts the distro down, and with it Incus
  and every container. On the next start, Incus brings back whatever was
  running. This explains the prototype's "containers came back after a
  restart and used memory", and it means anything configured only in memory
  (`ip addr add ...`) is lost. labnet's answers: `boot.autostart=false` on
  every node, reference solutions that use configuration files, and a clear
  section in the troubleshooting guide.
- **The stock Debian image is bare.** No `nano`, `tcpdump`, `nft`, `dig`,
  `traceroute`, `sshd` or Python. Hence role images (below).
- **Boot does not block on the network.** A PC with a DHCP client and no DHCP
  server still reaches systemd state `running` within seconds.
- **Ansible can manage nodes without any lab networking**, through the
  `community.general.incus` connection plugin (ships with Debian's Ansible).
- **Incus 6.0.4 has no "link down" option for a NIC** (`connected=false` is
  rejected), so cable pulls are done another way (below).
- Publishing a container as an image takes about 25 seconds; creating a
  container from a local image takes about 4 seconds with the `dir` backend.

## Part 2: design choices

### Console access goes through `incus exec`, never the lab network

`labnet shell` and the web terminal run `incus exec`, which enters the
container directly from the host. It cannot be broken by anything the user
does to the node's addresses, routes or firewall. This is the direct answer
to the prototype's lost-SSH-session problem. SSH between lab nodes still
works and is worth practising, but it is never the way *into* the lab.

### Role images instead of installing packages at boot

Nodes start unconfigured, and most have no internet access, so they cannot
install anything when they boot. labnet builds one image per role (`labnet-pc`,
`labnet-router`, `labnet-server`) from `images:debian/13` and creates nodes as
copies. The recipe is plain data in [`images.py`](../src/labnet/images.py):
a package list, a list of services to leave disabled, and a short shell
script.

Rejected alternative: a second "management" interface on every node with
internet access. It would put an extra, already-configured interface and
default route on every device, which defeats exercises about addressing and
routing, and it would let traffic bypass the lab's own router.

Details that matter:

- The image build removes the SSH host keys and empties `/etc/machine-id`, and
  a small first-boot unit regenerates the keys. Otherwise every node would
  share one SSH identity and one DHCP client identity.
- Services are installed but stopped and disabled. Starting them is the
  exercise.
- Routers ship with `net.ipv4.ip_forward=1` (as the plan asked). Everything
  else on a router is unconfigured.
- PCs run a DHCP client on every interface, like a desktop.

### Readiness means systemd says so

`up` waits until `systemctl is-system-running` inside each node reports
`running` (or `degraded`). The base image is not a cloud image, so there is
no cloud-init to wait for; SSH host keys are generated by a normal unit that
is part of boot. For images without systemd the fallback is "a command can
run".

### Ownership by config key, not by name

Every network and container gets `user.labnet.managed=true` plus keys for the
lab, node, role and links. `list`, `down` and everything else find lab objects
by these keys only. Before creating anything, `up` checks that none of the
names it needs is taken by an object without the matching lab key, and stops
if one is. An integration test proves a foreign container with a clashing
name is left alone.

### Names

- Container: `<lab>-<node>`, for example `office-r1`. It is also the hostname,
  so the prompt shows where you are.
- Bridge: `ln-` plus 8 hex digits of SHA-1(`lab/network`). Linux limits
  interface names to 15 characters, and `office-lan-office` is already 17.
  A fixed-length name derived from the real names is stable across runs. The
  readable names are stored in the config keys and the description, and
  users only ever type the readable ones.

### Idempotent `up` that never changes existing nodes

Running `up` again creates what is missing and starts what is stopped. If an
existing node differs from the file (different links or role), `up` says so
and leaves it. Changing a node in place would mean deleting work the user did
on it; that must be an explicit `down`.

### Cable pull and link quality are done inside the node

`labnet link down` runs `ip link set ethN down` in the node; `link set` adds
a netem qdisc on the node's interface. Both go through `incus exec`.

Rejected alternative: acting on the host end of the veth pair, which would
show as a true carrier loss. That needs root on the host for every call,
while everything else in labnet needs only membership of `incus-admin`.
The limits of this choice are stated in `docs/limitations.md`.

### Few dependencies, plain code

The CLI uses `argparse` from the standard library and one third-party
package, PyYAML. Validation is hand-written so that error messages can name
the file, the node and the fix. The web UI's packages (FastAPI, uvicorn) are
an optional extra. Types are plain dataclasses.

### Forwarding is set explicitly on every node

Found while testing roles: servers and PCs reported
`net.ipv4.ip_forward = 1`. A new container's network namespace inherits the
host's value, and the host forwards because Incus needs it for NAT. A PC
that silently routes is wrong and would spoil exercises, so each image
writes `/etc/sysctl.d/90-labnet.conf`: 1 on routers, 0 elsewhere.

### Persistent configuration is the documented way

Lab nodes restart more often than real machines: on `stop`/`start`, on
snapshot restore, and on WSL whenever the distro shuts down. Every reference
solution therefore uses configuration files (systemd-networkd, sysctl.d,
`/etc/nftables.conf`, service configs plus `systemctl enable`), and the
scenario tests restart the lab and re-run the checks to prove it.

Nodes keep the base image's `systemd-networkd` and `systemd-resolved`
instead of switching to ifupdown and a plain `/etc/resolv.conf`. They are
what current Debian and Ubuntu use, so what is learned transfers. The cost
is two gotchas, both documented: file order decides which `.network` file
wins, and dnsmasq must be told not to claim port 53 on every address.

### Scenario checks are data, and test behaviour

A check is a YAML entry with a node and one of three things to test
(`address_in`, `ping`, `run` with an expected result). That covers every
scenario shipped, and a new scenario needs no Python. Checks are written
against behaviour ("pc1 resolves this name") rather than a file's contents,
so a learner may solve a task with a different tool than the reference
solution did.

Rejected alternative: checks as Python functions per scenario. More
flexible, but teachers and students would need to write Python to add an
exercise, and each scenario would become code to maintain.

### Reference solutions are executed from the documentation

The solution documents mark runnable code blocks with `node=<name>`. The
integration tests extract exactly those blocks and run them, so the
solution a reader sees cannot drift from the solution that was tested.

Rejected alternative: separate solution scripts next to the documents.
Simpler to run, but two copies of every answer, one of which nobody reads.

### Scenario reset rebuilds instead of restoring a snapshot

`scenario reset` deletes the lab and builds it again. Taking a snapshot at
every scenario start would make reset slightly faster, but with the `dir`
storage backend a snapshot is a full copy of each node, paid on every start
for a reset that rarely happens.

### No state files

labnet stores nothing outside Incus. Labs, their nodes and networks, and
which scenario a lab belongs to are all read from `user.labnet.*` keys each
time. There is no database to get out of sync with reality. The price is
that hints are not remembered: `labnet scenario hint 2` asks for the second
hint by number.

### Web UI: plain page, same engine, local only

- **No framework and no build step.** One HTML file, one CSS file, one
  JavaScript file of about 650 lines, and three vendored libraries at pinned
  versions (Cytoscape.js, xterm.js, marked). The owner can read and change
  it without a Node.js toolchain.
- **No logic in the backend.** Each endpoint is one engine call. A test
  drives the HTTP API and checks the effect on real nodes.
- **Local only, and checked.** Binding to 127.0.0.1 keeps other machines
  out, but not other *websites*: a page on any site can ask the browser to
  connect to `ws://127.0.0.1:8080`. The server therefore checks the `Origin`
  header (cross-site requests) and the `Host` header (DNS rebinding) on
  every request and WebSocket. Both attacks are covered by tests.

Rejected alternative: a token in the URL, as Jupyter uses. It also works,
but adds something to copy around, and the header checks need nothing from
the user.

- **Layered layout.** A force-directed layout was tried first and produced
  cramped, overlapping pictures. The layered ("breadthfirst") layout rooted
  at the internet uplink gives the conventional network-diagram shape.

### VLANs are port settings in the topology

On real equipment a VLAN belongs to the switch port, not to the device
plugged into it. The topology file follows that: a link may be written
`{network: sw, vlan: 10}` (access port) or `{network: sw, trunk: [10, 20]}`
(trunk port), and labnet passes these to Incus as the NIC options `vlan` and
`vlan.tagged`. Tagging on the device side of a trunk is left to the learner,
as it would be on a real router.

Limit of this choice: port VLANs are fixed when the lab is built.

### Stretch goals not built

- **Windows VMs as nodes.** Not attempted. `/dev/kvm` exists in this WSL
  kernel, so Incus VMs may be possible, but each Windows node needs a
  licensed image, several GB of memory and nested virtualisation, on a
  machine where all of WSL has 6 GB.
- **Drag-and-drop topology editor.** Not built. It is the largest stretch
  item, and writing a five-line YAML file is quick. Labs can be created from
  the web UI only by starting a scenario.

### License

MIT, the usual choice for a portfolio project. Change it if you prefer.
