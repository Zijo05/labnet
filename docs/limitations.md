# Limitations

What labnet does not do, stated plainly, so you know before you rely on it.

## It teaches Linux networking, not Cisco IOS

Every router, switch and server in a lab is Linux. You configure them with
`ip`, `nftables`, `systemd-networkd`, FRR, BIND, Kea and dnsmasq.

- The **concepts** carry over to any vendor: addressing, subnets, default
  gateways, routing tables, NAT, DHCP, DNS, stateful firewalls, VLANs and
  trunks, OSPF adjacencies and timers.
- The **commands** do not. There is no IOS, no `enable`, no `show running-config`
  outside FRR. FRR's `vtysh` is the closest thing: its syntax for routing
  protocols is deliberately similar to Cisco's, so the OSPF scenario looks
  familiar, but interface addressing, NAT and ACLs are done the Linux way.
- labnet is not a substitute for Packet Tracer, GNS3 or EVE-NG if the goal is
  practising vendor command lines for a certification.

## Switches are simpler than real ones

- A switch is a Linux bridge managed by Incus. You cannot log in to it, and
  it has no management interface or configuration of its own.
- **VLANs** are supported as port settings in the topology file (access and
  trunk ports). They are fixed when the lab is built; there is no command to
  change a port's VLAN on a running lab.
- No spanning tree exercises: there are no switch-to-switch links to form
  loops with, and no way to configure STP.
- No port security, port mirroring, link aggregation, or 802.1X.
- A switch connects nodes only. Two switches cannot be cabled to each other;
  use one network, or put a router (or a Linux node bridging two interfaces)
  between them.

## Links

- **Cable pull** (`labnet link down`) sets the node's interface
  administratively down. The node sees the interface as *down*, not as *up
  without carrier*, and someone on the node can bring it back up with
  `ip link set eth0 up`. The other end of the cable sees nothing, as with a
  real switch in between. A restart of the node also brings the interface
  back up.
- **Delay and loss** (`labnet link set`) apply to packets the node sends on
  that link, not to packets it receives. Set both ends for both directions.
  The settings are lost when the node restarts.
- No bandwidth limits, jitter, reordering or corruption (the kernel can do
  them; labnet has no option for them. `tc` on the node works by hand).
- Point-to-point links are modelled as a switch with two ports.

## Nodes are containers, not machines

- They share the host's Linux kernel. You cannot run another kernel, load
  kernel modules from inside a node, or change kernel-wide settings
  (`sysctl kernel.*` is refused). Network settings per node work normally.
- **No Windows, no BSD, no network appliance images.** Windows VMs as nodes
  were a stretch goal and were not attempted: each would need a licensed
  image, several GB of memory, and nested virtualisation, which does not fit
  the 6 GB WSL setup this was built on.
- Nodes are Debian 13. A different `image:` per node is accepted (anything
  Incus can run as a container; an Alpine node was tested: it boots, gets a
  console, and its cable can be pulled). Such a node gets none of labnet's
  tools or defaults, and capture, link shaping and scenario checks work on it
  only if it has `tcpdump`, `tc`, `ip` and `ping`. The image builder itself
  supports Debian and Ubuntu bases only.
- "Ready" is detected through systemd. Images without systemd are treated as
  ready as soon as a command can run in them.

## Addressing and protocols

- **IPv4 only** in everything labnet sets up or checks. Lab networks are
  created without IPv6 addressing; nodes still have IPv6 link-local addresses
  and you can configure IPv6 by hand on the switches, but the uplink does not
  provide it and no scenario covers it.
- The uplink is NAT behind the host. Lab nodes can reach the internet;
  nothing on the internet (or your LAN) can reach into the lab. The host
  itself can reach addresses on an uplink network, but not on plain switches.
- No built-in scenarios for BGP, IS-IS, VPNs, IPv6, Wi-Fi, or QoS. FRR
  includes BGP and IS-IS daemons if you want to try them yourself.

## Snapshots

- A snapshot stores files, not memory. Restoring a running node restarts it.
- With Incus's `dir` storage backend (the default from `incus admin init
  --minimal`), a snapshot is a full copy of the node's disk (several hundred
  MB) and takes a few seconds per node. On ZFS or Btrfs pools snapshots are instant and
  nearly free; that configuration was not tested.

## The web UI

- **No login and no encryption.** It is for use on your own computer and
  listens on 127.0.0.1 only. Do not expose it to a network.
- One user at a time is assumed. Two browser windows work, but each has its
  own consoles.
- You cannot create or edit a topology in the browser. The drag-and-drop
  **topology editor** was a stretch goal and was not built; labs are created
  from YAML files or by starting a scenario.
- A console is not kept alive when you close its tab or reload the page;
  whatever was running in it is stopped.
- The page was tested by hand in one Chromium-based browser. Its JavaScript
  has no automated tests.

## Platform and scale

- Developed and tested on **Debian 13 under WSL2** with Incus 6.0.4. See
  [install.md](install.md) for what was and was not tested elsewhere. Native
  Linux, Ubuntu 24.04 and Debian 12 should work but were not run.
- Not targeted at macOS. Nothing is hard-coded to x86 (Incus picks the image
  architecture), but ARM was not tested.
- On WSL, labs stop when the last terminal closes (see
  [troubleshooting](troubleshooting.md#my-lab-is-stopped-or-my-settings-are-gone-after-i-closed-the-terminal)).
- The largest lab run during development had 20 PCs on one switch: 74
  seconds to build, about 34 MiB of memory per idle node, 22 seconds to
  delete. `count:` allows up to 200, but nothing larger than 20 was measured.
  Nodes are created one after another, about 3 to 4 seconds each.
- Everything runs on one machine. There is no way to spread a lab across
  hosts.

## Safety

- labnet needs membership of the `incus-admin` group, which is equivalent to
  root on the machine. That is an Incus property, not something labnet adds.
- Lab nodes are unprivileged containers: root inside a node is not root on
  the host. They are not a hard security boundary; do not run untrusted code
  in them.
