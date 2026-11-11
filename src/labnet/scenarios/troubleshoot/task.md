# Scenario 8: Troubleshooting

You are the new IT administrator. The ticket on your desk says:

> **"The internet is down in the whole office. pc2 has not worked since it
> was brought over from the branch office last week."**

The previous administrator left this description of how the network is
*supposed* to work.

```
   srv1 ----+
            |
   pc1 ----[ lan ]---- r1 ----[ wan ]---- internet
            |      eth0   eth1
   pc2 ----+
```

| Node | Role | Intended configuration |
|------|------|------------------------|
| r1   | router | eth0 = 192.168.10.1/24. eth1 gets its address from the provider by DHCP. Forwards packets and hides the LAN behind its WAN address (NAT). |
| srv1 | DHCP and DNS server (dnsmasq) | 192.168.10.2/24. Leases 192.168.10.100-150 with gateway 192.168.10.1 and DNS server 192.168.10.2. Forwards DNS questions to the public resolver 1.1.1.1. |
| pc1, pc2 | office PCs | Get everything by DHCP. |

## Your task

Find and fix **every** fault, so that the network works as described: both
PCs get their settings from DHCP, reach the internet, and resolve names.
There are several independent faults, on more than one node.

Rules of the game:

- Repair, do not rebuild. Change what is wrong and leave the rest.
- Fix the cause, not the symptom. A fix that disappears at the next reboot
  is not a fix.

## How to approach it

Work from the bottom up, one hop at a time, and do not guess:

1. Does the PC have an address in the right network? (`ip -brief address`)
2. Can it reach its gateway? Which gateway does it think it has? (`ip route`)
3. Can the router reach the internet itself?
4. Do packets from the PC pass through the router, and what do they look
   like when they leave? (`labnet capture r1 wan icmp`)
5. Does an address work where a name does not? Then it is DNS.
   (`dig @192.168.10.2 debian.org`, `journalctl -u dnsmasq -e`)

After changing DHCP settings on the server, remember that clients keep their
old lease. `networkctl reconfigure eth0` on a PC requests a new one.

This scenario needs the machine running labnet to be online.
