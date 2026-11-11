#!/usr/bin/env bash
# Spike 3 (stretch goal, handoff section 4 question 6): do per-port VLAN
# settings on an Incus-managed Linux bridge behave like a VLAN-aware switch?
#   - access ports:  nic option  vlan=<id>          (untagged member of one VLAN)
#   - trunk port:    nic option  vlan.tagged=<ids>  (carries several VLANs, tagged)
# Uses the labnet-pc image because it already contains ping. Clean up with 99-cleanup.sh.
set -u
say() { printf '\n### %s\n' "$*"; }
x() { incus exec "$@"; }
new() {  # new <name> <nic options...>
    local name="$1"; shift
    incus init labnet-pc "$name" --no-profiles --storage default -q </dev/null
    incus config device add "$name" eth0 nic network=lnspike-sw name=eth0 "$@" >/dev/null
}

incus network create lnspike-sw ipv4.address=none ipv6.address=none
new lnspike-a10 vlan=10
new lnspike-b10 vlan=10
new lnspike-c20 vlan=20
new lnspike-trunk vlan.tagged=10,20
incus start lnspike-a10 lnspike-b10 lnspike-c20 lnspike-trunk
sleep 6

# All three hosts are deliberately in the SAME IP subnet, so only the VLAN
# setting can separate them.
x lnspike-a10 -- ip addr add 10.9.0.1/24 dev eth0
x lnspike-b10 -- ip addr add 10.9.0.2/24 dev eth0
x lnspike-c20 -- ip addr add 10.9.0.3/24 dev eth0

say "host bridge: vlan filtering and port membership"
cat /sys/class/net/lnspike-sw/bridge/vlan_filtering
bridge vlan show 2>/dev/null | grep -A1 -E 'veth' | head -20

say "same VLAN (10 <-> 10): expect replies"
x lnspike-a10 -- ping -c 2 -W 1 10.9.0.2 | tail -2
say "different VLAN (10 -> 20), same subnet: expect 100% loss"
x lnspike-a10 -- ping -c 2 -W 1 10.9.0.3 | tail -2

say "trunk port with tagged sub-interfaces reaches both VLANs"
x lnspike-trunk -- sh -c '
ip link add link eth0 name eth0.10 type vlan id 10 && ip link set eth0.10 up && ip addr add 10.9.0.10/24 dev eth0.10
ip link add link eth0 name eth0.20 type vlan id 20 && ip link set eth0.20 up && ip addr add 10.9.1.10/24 dev eth0.20'
x lnspike-c20 -- ip addr add 10.9.1.3/24 dev eth0
echo "- trunk -> a10 over VLAN 10:"; x lnspike-trunk -- ping -c 2 -W 1 -I eth0.10 10.9.0.1 | tail -2
echo "- trunk -> c20 over VLAN 20:"; x lnspike-trunk -- ping -c 2 -W 1 -I eth0.20 10.9.1.3 | tail -2
echo "- trunk untagged (no VLAN) -> a10: expect loss"
x lnspike-trunk -- sh -c 'ip addr add 10.9.0.11/24 dev eth0; ping -c 2 -W 1 -I eth0 10.9.0.1 | tail -2'
