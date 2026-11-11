# Scenario 3: DHCP

Setting addresses by hand does not scale. In this lab a server hands them
out. The PCs are already DHCP clients; they keep asking, and nobody answers.

```
   srv1 ----+
            |
   pc1 ----[ lan ]
            |
   pc2 ----+
```

## Your task

1. Give `srv1` the static address **192.168.50.2/24** on eth0.
2. Run a DHCP server on `srv1` for the network 192.168.50.0/24 that hands out
   addresses from **192.168.50.100 to 192.168.50.150**.
3. Make the DHCP service start automatically at boot.

`pc1` and `pc2` must then receive an address from that range without you
configuring anything on them.

## Choosing the software

`srv1` has two DHCP servers installed, both stopped:

- **Kea** (`kea-dhcp4-server`, config in `/etc/kea/kea-dhcp4.conf`), the
  current server from ISC, configured in JSON.
- **dnsmasq** (config in `/etc/dnsmasq.d/`), small and simple.

Use whichever you like; the checks do not care.

## Useful commands

- On a PC, see the lease: `ip -brief address`, `networkctl status eth0`
- A client that has been waiting retries slowly. Ask again right away with
  `networkctl reconfigure eth0`.
- Watch the conversation: `labnet capture srv1 lan port 67 or port 68`
- Server logs: `journalctl -u kea-dhcp4-server -e` or `journalctl -u dnsmasq -e`
