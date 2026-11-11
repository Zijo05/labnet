# Scenario 1: Static addressing

Two PCs, `pc1` and `pc2`, are plugged into the same switch (`lan`). Both ask
for an address by DHCP, but there is no DHCP server on this network, so
neither has an IPv4 address and they cannot talk to each other.

```
   pc1 ----[ lan ]---- pc2
```

## Your task

Give the PCs these addresses, by hand:

| Node | Interface | Address         |
|------|-----------|-----------------|
| pc1  | eth0      | 192.168.1.11/24 |
| pc2  | eth0      | 192.168.1.12/24 |

Then make sure each PC can ping the other.

## Getting started

- Open a console on a node: `labnet shell pc1` (leave it with `exit`).
- See what an interface has now: `ip -brief address`
- Test reachability: `ping -c 3 192.168.1.12`

## Extra

An address set with the `ip` command is gone after a reboot. Can you make it
permanent? Test it with `labnet stop static` followed by `labnet start static`.
