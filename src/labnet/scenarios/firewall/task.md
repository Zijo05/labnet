# Scenario 6: Firewall for a guest network

The office has added a guest network for visitors. Everything works, but too
well: a visitor's laptop can reach the office PCs and the internal server.

```
   srv1 ----+
            |
   pc1 ----[ lan-office ]----+
                        eth0 |
                             r1 ----[ wan ]---- internet
                        eth1 |  eth2
   guest1 --[ lan-guest ]----+
```

## Already configured for you

| Node   | Network    | Address          |
|--------|------------|------------------|
| r1     | lan-office | 192.168.10.1/24 (eth0) |
| r1     | lan-guest  | 192.168.20.1/24 (eth1) |
| r1     | wan        | by DHCP (eth2), with NAT for both LANs |
| srv1   | lan-office | 192.168.10.5/24, runs a web server on port 80 |
| pc1    | lan-office | 192.168.10.11/24 |
| guest1 | lan-guest  | 192.168.20.11/24 |

Try it: on `guest1`, `ping 192.168.10.11` and `curl http://192.168.10.5`
both work. They should not.

## Your task

Add packet filtering on `r1` so that:

1. **Guests reach the internet** (`ping 1.1.1.1` from guest1 works).
2. **Guests cannot reach the office LAN** 192.168.10.0/24: no ping to pc1,
   no web page from srv1.
3. **The office is unaffected**: pc1 reaches the internet, the web server,
   and can even ping guest1 (the office may start connections to guests;
   only the other direction is forbidden).
4. The existing NAT keeps working, and the rules **survive a reboot**.

The router's rules are in `/etc/nftables.conf`. Edit that file and load it
with `nft -f /etc/nftables.conf`.

## Things to think about

- Packets passing *through* a router go through the `forward` hook, not
  `input` (which is for packets addressed to the router itself).
- Point 3 needs a *stateful* rule. If you simply drop everything from the
  guest network to the office network, pc1's ping to guest1 breaks too,
  because the reply is a packet from guest to office. The firewall has to
  tell "a reply to something the office started" apart from "a new
  connection from a guest": `ct state established,related accept`.
- A safe design is "drop by default, then allow what is needed".

This scenario needs the machine running labnet to be online.
