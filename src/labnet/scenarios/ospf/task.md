# Scenario 7: Dynamic routing with OSPF

Three routers are connected in a triangle. Two of them have a LAN with a PC.
With static routes you would have to tell every router about every network,
and again whenever something changes. A routing protocol does that for you.

```
                 r2
           eth0 /  \ eth1
      [ net12 ]      [ net23 ]
     eth1 /              \ eth1
        r1 ---[ net13 ]--- r3
   eth0 |  eth2       eth2 | eth0
     [ lan1 ]           [ lan3 ]
        |                  |
       pc1                pc3
```

## Already configured for you

| Link  | Network       | Addresses                          |
|-------|---------------|------------------------------------|
| lan1  | 10.0.1.0/24   | r1 eth0 = .1, pc1 = .10 (gateway .1) |
| lan3  | 10.0.3.0/24   | r3 eth0 = .1, pc3 = .10 (gateway .1) |
| net12 | 10.0.12.0/30  | r1 eth1 = .1, r2 eth0 = .2         |
| net23 | 10.0.23.0/30  | r2 eth1 = .1, r3 eth1 = .2         |
| net13 | 10.0.13.0/30  | r1 eth2 = .1, r3 eth2 = .2         |

Each router can ping its direct neighbours, but `pc1` cannot reach `pc3`:
r1 has never heard of 10.0.3.0/24.

## Your task

1. Enable **OSPF** on `r1`, `r2` and `r3` using FRR, with all networks in
   **area 0**. Every router must form a full adjacency with both neighbours.
2. `pc1` and `pc3` must be able to ping each other.
3. FRR must start at boot on all three routers.

FRR is installed but its OSPF daemon is switched off. Turn it on in
`/etc/frr/daemons` (`ospfd=yes`), start the `frr` service, and configure it
either interactively with `vtysh` (remember `write memory`) or by writing
`/etc/frr/frr.conf`.

## Then break it

Once it works, watch the network repair itself:

1. On pc1, see the path: `traceroute -n 10.0.3.10` (r1, then r3).
2. Start a continuous ping on pc1: `ping 10.0.3.10`
3. In another terminal, pull the direct cable: `labnet link down r1 net13`
4. The ping stops, then resumes. `traceroute` now shows three routers.
5. Plug it back in: `labnet link up r1 net13`

How long is the interruption? r1 notices at once, because its own interface
went down. r3 does not: its interface is still plugged into the switch and
stays up, exactly as with a real switch between two routers. r3 only finds
out when OSPF hello packets stop arriving, after the **dead interval**
(40 seconds by default). Try `ip ospf hello-interval 1` and
`ip ospf dead-interval 4` on the router links and measure again.

## Useful commands (in `vtysh` on a router)

- `show ip ospf neighbor` - who am I adjacent to, and in which state?
- `show ip route ospf` - which routes did OSPF install?
- `show running-config`
