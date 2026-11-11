# Solution 7: Dynamic routing with OSPF

Scenario: `labnet scenario start ospf`

## The idea

Each router tells its neighbours which networks it is attached to. OSPF
floods that information through the area, every router builds the same map,
and each one calculates the shortest path to every network. When a link
fails, the map changes and the paths are recalculated without anyone typing
a route.

Three steps per router:

1. Switch the OSPF daemon on in `/etc/frr/daemons`.
2. Write the routing configuration to `/etc/frr/frr.conf`.
3. Enable and start the `frr` service.

The configuration lists, per router, the networks it should speak OSPF on
(`network ... area 0`). The links between routers are declared
`point-to-point` (there are exactly two routers on each, so no designated
router needs to be elected) and given short timers, so a silent neighbour is
noticed after 4 seconds instead of the default 40. The LAN interface is
`passive`: its network is announced, but no OSPF packets are sent to the PCs.

## r1

```sh node=r1
sed -i 's/^ospfd=no/ospfd=yes/' /etc/frr/daemons
cat > /etc/frr/frr.conf <<'EOF'
frr defaults traditional
hostname r1
!
interface eth1
 ip ospf network point-to-point
 ip ospf hello-interval 1
 ip ospf dead-interval 4
!
interface eth2
 ip ospf network point-to-point
 ip ospf hello-interval 1
 ip ospf dead-interval 4
!
router ospf
 ospf router-id 1.1.1.1
 passive-interface eth0
 network 10.0.1.0/24 area 0
 network 10.0.12.0/30 area 0
 network 10.0.13.0/30 area 0
!
EOF
systemctl enable frr
systemctl restart frr
```

## r2

```sh node=r2
sed -i 's/^ospfd=no/ospfd=yes/' /etc/frr/daemons
cat > /etc/frr/frr.conf <<'EOF'
frr defaults traditional
hostname r2
!
interface eth0
 ip ospf network point-to-point
 ip ospf hello-interval 1
 ip ospf dead-interval 4
!
interface eth1
 ip ospf network point-to-point
 ip ospf hello-interval 1
 ip ospf dead-interval 4
!
router ospf
 ospf router-id 2.2.2.2
 network 10.0.12.0/30 area 0
 network 10.0.23.0/30 area 0
!
EOF
systemctl enable frr
systemctl restart frr
```

## r3

```sh node=r3
sed -i 's/^ospfd=no/ospfd=yes/' /etc/frr/daemons
cat > /etc/frr/frr.conf <<'EOF'
frr defaults traditional
hostname r3
!
interface eth1
 ip ospf network point-to-point
 ip ospf hello-interval 1
 ip ospf dead-interval 4
!
interface eth2
 ip ospf network point-to-point
 ip ospf hello-interval 1
 ip ospf dead-interval 4
!
router ospf
 ospf router-id 3.3.3.3
 passive-interface eth0
 network 10.0.3.0/24 area 0
 network 10.0.23.0/30 area 0
 network 10.0.13.0/30 area 0
!
EOF
systemctl enable frr
systemctl restart frr
```

The router ID is just a unique label in dotted form; it does not have to be
a real address. Timers must match on both ends of a link, or the routers
refuse to become neighbours.

### The interactive way

The same configuration can be typed into `vtysh`, as on a hardware router:

```
r1# configure terminal
r1(config)# router ospf
r1(config-router)# network 10.0.1.0/24 area 0
r1(config-router)# network 10.0.12.0/30 area 0
r1(config-router)# network 10.0.13.0/30 area 0
r1(config-router)# end
r1# write memory
```

`write memory` saves the running configuration to `/etc/frr/frr.conf`.

## Verify

In `vtysh` on r1:

```
show ip ospf neighbor       two neighbours, state Full
show ip route ospf          10.0.3.0/24 via 10.0.13.2 (direct), 10.0.23.0/30 with two paths
```

On pc1:

```sh
ping -c 3 10.0.3.10
traceroute -n 10.0.3.10     # 10.0.1.1, 10.0.13.2, 10.0.3.10
```

## Break it and watch

```sh
labnet link down r1 net13
```

A ping running on pc1 loses a few packets and continues. `traceroute -n
10.0.3.10` now shows the longer path: 10.0.1.1, 10.0.12.2, 10.0.23.2,
10.0.3.10. Put the cable back with `labnet link up r1 net13` and the short
path returns.

Why a few packets are lost: r1 sees its own interface go down and switches
at once. r3's interface stays up (there is a switch in between), so r3 keeps
sending replies towards the dead link until four hellos are missed. With the
default timers that gap would be 40 seconds.

## What to take away

- OSPF neighbours must agree on area, network, and timers.
- A link failure is only detected immediately at the end where the
  interface actually goes down. Timers (or BFD, in real networks) cover the
  other end.
- `passive-interface` on LANs is good practice: hosts have no business
  receiving, or injecting, routing packets.
