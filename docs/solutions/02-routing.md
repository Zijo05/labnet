# Solution 2: Routing between two networks

Scenario: `labnet scenario start routing`

## The idea

`pc1` (10.1.1.10/24) and `pc2` (10.1.2.10/24) are in different networks.
A host sends directly only to its own network; for everything else it hands
the packet to its **default gateway**. The router `r1` has a foot in both
networks and forwards between them. Three things are needed:

1. `r1` has an address in each network.
2. Each PC has an address and a default route pointing at `r1`.
3. `r1` forwards packets. On a labnet router that is already switched on
   (`/etc/sysctl.d/90-labnet.conf` sets `net.ipv4.ip_forward = 1`).

## Router

On **r1**, one file per interface:

```sh node=r1
cat > /etc/systemd/network/10-lan-a.network <<'EOF'
[Match]
Name=eth0

[Network]
Address=10.1.1.1/24
EOF

cat > /etc/systemd/network/20-lan-b.network <<'EOF'
[Match]
Name=eth1

[Network]
Address=10.1.2.1/24
EOF
networkctl reload
```

## PCs

On **pc1**:

```sh node=pc1
cat > /etc/systemd/network/05-static.network <<'EOF'
[Match]
Name=eth0

[Network]
Address=10.1.1.10/24
Gateway=10.1.1.1
EOF
networkctl reload
```

On **pc2**:

```sh node=pc2
cat > /etc/systemd/network/05-static.network <<'EOF'
[Match]
Name=eth0

[Network]
Address=10.1.2.10/24
Gateway=10.1.2.1
EOF
networkctl reload
```

The same with commands only (not permanent) would be
`ip address add 10.1.1.10/24 dev eth0` and `ip route add default via 10.1.1.1`.

## Verify

On pc1:

```sh
ip route                    # default via 10.1.1.1 dev eth0
ping -c 3 10.1.2.10
traceroute -n 10.1.2.10     # hop 1 is 10.1.1.1, hop 2 is pc2
```

## What to take away

- Routing needs to work in **both directions**. The classic mistake is to
  configure the gateway on pc1 only: the ping request reaches pc2, but pc2
  has no route back and the reply is never sent.
- The router needs no routes of its own here. It is directly connected to
  both networks, and connected networks are in the routing table
  automatically (`ip route` on r1).
