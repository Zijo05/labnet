#!/usr/bin/env bash
# Spike 1: answers handoff section 4, questions 2, 3 and 4.
#   2. Does an Incus bridge with no address/DHCP/DNS act as a plain L2 segment?
#   3. Can a multi-NIC unprivileged container route and NAT (nftables)?
#   4. Can a DHCP server in one container serve clients on the same bridge?
# Everything it creates is prefixed "lnspike-". Clean up with 99-cleanup.sh.
set -u
IMG="${IMG:-images:debian/13}"
say() { printf '\n### %s\n' "$*"; }
x() { incus exec "$@"; }

say "create networks"
incus network create lnspike-lan1 ipv4.address=none ipv6.address=none
incus network create lnspike-lan2 ipv4.address=none ipv6.address=none
incus network create lnspike-wan ipv4.address=10.250.250.1/24 ipv4.nat=true ipv6.address=none

say "launch nodes"
# r1: eth0 = wan (gets DHCP + internet from Incus), eth1 = lan1, eth2 = lan2
incus init "$IMG" lnspike-r1 --no-profiles --storage default --network lnspike-wan
incus config device add lnspike-r1 eth1 nic network=lnspike-lan1 name=eth1
incus config device add lnspike-r1 eth2 nic network=lnspike-lan2 name=eth2
incus init "$IMG" lnspike-pc1 --no-profiles --storage default --network lnspike-lan1
incus init "$IMG" lnspike-pc2 --no-profiles --storage default --network lnspike-lan1
incus init "$IMG" lnspike-pc3 --no-profiles --storage default --network lnspike-lan2
incus start lnspike-r1 lnspike-pc1 lnspike-pc2 lnspike-pc3
sleep 8

say "Q2: L2 segment - static addresses, pc1 <-> pc2 on lan1"
x lnspike-pc1 -- ip addr add 192.168.10.11/24 dev eth0
x lnspike-pc2 -- ip addr add 192.168.10.12/24 dev eth0
x lnspike-pc1 -- ping -c 2 -W 2 192.168.10.12 | tail -3
echo "host bridge has address?"; ip -4 -br addr show lnspike-lan1

say "r1: wait for WAN address, install packages"
for i in $(seq 1 20); do x lnspike-r1 -- ip -4 -br addr show eth0 | grep -q 10.250 && break; sleep 1; done
x lnspike-r1 -- ip -4 -br addr
x lnspike-r1 -- bash -c 'export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq nftables dnsmasq >/dev/null 2>&1; echo apt-exit=$?'

say "Q3: routing between lan1 and lan2"
x lnspike-r1 -- ip addr add 192.168.10.1/24 dev eth1
x lnspike-r1 -- ip link set eth1 up
x lnspike-r1 -- ip addr add 192.168.20.1/24 dev eth2
x lnspike-r1 -- ip link set eth2 up
x lnspike-r1 -- sysctl -w net.ipv4.ip_forward=1
x lnspike-pc3 -- ip addr add 192.168.20.13/24 dev eth0
x lnspike-pc3 -- ip route add default via 192.168.20.1
x lnspike-pc1 -- ip route add default via 192.168.10.1
x lnspike-pc1 -- ping -c 2 -W 2 192.168.20.13 | tail -3

say "Q3: NAT with nftables on r1, pc1 -> internet"
x lnspike-r1 -- nft add table ip nat
x lnspike-r1 -- nft 'add chain ip nat postrouting { type nat hook postrouting priority 100 ; }'
x lnspike-r1 -- nft add rule ip nat postrouting oifname eth0 masquerade
x lnspike-pc1 -- ping -c 2 -W 3 1.1.1.1 | tail -3

say "Q4: DHCP server (dnsmasq) on r1 serving lan2, pc3 as client"
x lnspike-r1 -- systemctl stop dnsmasq
x lnspike-r1 -- bash -c 'printf "interface=eth2\nbind-interfaces\ndhcp-range=192.168.20.100,192.168.20.150,12h\ndhcp-option=option:router,192.168.20.1\n" > /etc/dnsmasq.d/lab.conf; systemctl restart dnsmasq; systemctl is-active dnsmasq'
x lnspike-pc3 -- ip addr flush dev eth0
x lnspike-pc3 -- networkctl reconfigure eth0
sleep 6
x lnspike-pc3 -- ip -4 -br addr show eth0
x lnspike-pc3 -- ip route
x lnspike-pc3 -- ping -c 2 -W 3 1.1.1.1 | tail -3
