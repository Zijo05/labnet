# Scenario 4: NAT to the internet

A small office: two PCs behind a router. The router's second interface is
plugged into the internet provider's network (`wan`).

```
   pc1 ----+
           |
          [ lan ]---- r1 ----[ wan ]---- internet provider ---- internet
           |      eth0   eth1
   pc2 ----+
```

## Already configured for you

| Node | Interface | Address          | Gateway      | DNS     |
|------|-----------|------------------|--------------|---------|
| r1   | eth0      | 192.168.10.1/24  | -            | -       |
| pc1  | eth0      | 192.168.10.11/24 | 192.168.10.1 | 1.1.1.1 |
| pc2  | eth0      | 192.168.10.12/24 | 192.168.10.1 | 1.1.1.1 |

The PCs can ping the router, but nothing can reach the internet.

## Your task

1. **Connect the router to the provider.** `r1`'s WAN interface `eth1` has
   no address. The provider hands out addresses by DHCP, as a home or office
   connection usually does. Make `r1` request one. Afterwards `r1` itself
   must be able to `ping 1.1.1.1`.
2. **Let the PCs out.** The addresses 192.168.10.x are private: the provider
   does not know them and cannot send replies back to them. Configure
   **source NAT** (masquerading) on `r1` with nftables, so that packets
   leaving through `eth1` carry the router's own WAN address.
3. **Make it permanent.** The rules must be loaded again after a reboot.

When you are done, `pc1` and `pc2` can ping 1.1.1.1 and resolve names.

## Useful commands

- `ip -brief address`, `ip route` on r1 to see what DHCP gave it.
- `nft list ruleset` shows the active firewall and NAT rules.
- Watch what leaves the router: `labnet capture r1 wan icmp` while pinging
  from pc1. Before NAT works you will see packets leaving with the source
  192.168.10.11 and no replies.

This scenario needs the machine running labnet to be online.
