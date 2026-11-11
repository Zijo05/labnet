# Scenario 2: Routing between two networks

Two separate LANs are joined by one router. Nothing has an address yet.

```
   pc1 ----[ lan-a ]---- r1 ----[ lan-b ]---- pc2
                     eth0    eth1
```

## Your task

Configure these addresses:

| Node | Interface | Address       | Default gateway |
|------|-----------|---------------|-----------------|
| r1   | eth0      | 10.1.1.1/24   | -               |
| r1   | eth1      | 10.1.2.1/24   | -               |
| pc1  | eth0      | 10.1.1.10/24  | 10.1.1.1        |
| pc2  | eth0      | 10.1.2.10/24  | 10.1.2.1        |

When you are done, `pc1` and `pc2` must be able to ping each other. They are
in different networks, so every packet has to pass through `r1`.

## Things to notice

- A PC can only send directly to addresses in its own network. For anything
  else it needs a *default gateway*: the router's address on its own LAN.
- The router already forwards packets (`sysctl net.ipv4.ip_forward` shows 1).
  It only needs an address in each network.
- `traceroute 10.1.2.10` on pc1 shows the router as the first hop.
