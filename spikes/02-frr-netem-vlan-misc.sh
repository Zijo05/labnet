#!/usr/bin/env bash
# Spike 2: answers handoff section 4, questions 5, 6 and 8, plus a few facts
# the engine design depends on. Run after 01-l2-routing-nat-dhcp.sh.
set -u
say() { printf '\n### %s\n' "$*"; }
x() { incus exec "$@"; }

say "base image facts (pc1)"
x lnspike-pc1 -- bash -c 'for c in ip ping ss nano vim curl wget dig nft tcpdump tc traceroute ssh sshd python3 dhclient resolvectl networkctl; do printf "%s=%s " $c $(command -v $c >/dev/null && echo yes || echo no); done; echo'
x lnspike-pc1 -- bash -c 'cat /etc/systemd/network/*.network 2>/dev/null; echo "machine-id: $(cat /etc/machine-id)"'
x lnspike-pc1 -- bash -c 'dpkg-query -W | wc -l; du -sh / 2>/dev/null --exclude=/proc --exclude=/sys --exclude=/dev | tail -1'

say "readiness: systemd state on a node with no DHCP server (pc2), and wait-online"
x lnspike-pc2 -- systemctl is-system-running
x lnspike-pc2 -- systemctl is-active systemd-networkd-wait-online.service
x lnspike-pc2 -- systemctl list-jobs --no-legend | head -5

say "Q8: tc netem inside an unprivileged container (pc1 egress)"
x lnspike-pc1 -- bash -c 'command -v tc || (echo "tc missing in base image")'
x lnspike-r1 -- bash -c 'tc qdisc add dev eth1 root netem delay 50ms loss 0% && tc qdisc show dev eth1'
echo "module loaded on host now?"; lsmod | grep -c sch_netem
x lnspike-pc1 -- ping -c 3 -W 2 192.168.10.1 | tail -2
x lnspike-r1 -- tc qdisc del dev eth1 root

say "Q5: FRR with OSPF inside a container (r1)"
x lnspike-r1 -- bash -c 'export DEBIAN_FRONTEND=noninteractive; apt-get install -y -qq frr >/dev/null 2>&1; echo apt-exit=$?'
x lnspike-r1 -- bash -c 'sed -i "s/^ospfd=no/ospfd=yes/" /etc/frr/daemons && systemctl restart frr; sleep 3; systemctl is-active frr'
x lnspike-r1 -- vtysh -c 'conf t' -c 'router ospf' -c 'network 192.168.10.0/24 area 0' -c 'network 192.168.20.0/24 area 0' -c 'end'
sleep 2
x lnspike-r1 -- vtysh -c 'show ip ospf interface' | grep -E '^eth|State|Area' | head -8
x lnspike-r1 -- vtysh -c 'show version' | head -1

say "Q6: VLAN-aware bridge / Open vSwitch"
echo "inside container: create vlan sub-interface"
x lnspike-r1 -- bash -c 'ip link add link eth1 name eth1.10 type vlan id 10 && ip -d link show eth1.10 | grep -o "vlan protocol 802.1Q id 10" ; ip link del eth1.10'
echo "incus nic 'vlan' option on a bridged nic (needs bridge vlan filtering):"
incus network set lnspike-lan2 bridge.driver 2>&1 | head -1
incus config device set lnspike-pc3 eth0 vlan=20 2>&1 | head -2
incus config device unset lnspike-pc3 eth0 vlan 2>&1 | head -2
echo "ovs userspace installed on host?"; command -v ovs-vsctl || echo "no (kernel module exists, tools not installed)"

say "NIC link-state options in this Incus"
incus config device set lnspike-pc3 eth0 connected=false 2>&1 | head -2
x lnspike-pc3 -- ip -br link show eth0
incus config device unset lnspike-pc3 eth0 connected 2>&1 | head -1

say "Q1: JSON from the CLI"
incus list lnspike-pc1 --format json | python3 -c 'import json,sys; d=json.load(sys.stdin)[0]; print(d["name"], d["status"], sorted(d["state"]["network"].keys()), d["expanded_devices"]["eth0"])'
incus query /1.0 | python3 -c 'import json,sys; d=json.load(sys.stdin); print("api", d["api_version"], "server", d["environment"]["server_version"], "fw", d["environment"]["firewall"], "driver", d["environment"]["driver"])'

say "Ansible incus connection plugin on host"
ansible --version | head -1
ansible-doc -t connection -l 2>/dev/null | grep -i incus
ansible -i 'lnspike-pc1,' all -c community.general.incus -m raw -a 'hostname' 2>&1 | tail -3
