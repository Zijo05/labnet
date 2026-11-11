# Scenario 5: DNS

People remember names, not addresses. In this lab you run the DNS server for
an internal domain and point the clients at it.

```
   srv1 ----+           192.168.60.2
            |
   pc1 ----[ lan ]      192.168.60.11
            |
   pc2 ----+            192.168.60.12
```

## Already configured for you

The three addresses above (all /24). The nodes can ping each other by
address. No DNS server is running, and the PCs have no DNS server configured.

## Your task

1. Run a DNS server on `srv1` that is authoritative for the zone
   **`office.internal`** with these records:

   | Name                   | Type  | Value         |
   |------------------------|-------|---------------|
   | `srv1.office.internal` | A     | 192.168.60.2  |
   | `pc1.office.internal`  | A     | 192.168.60.11 |
   | `pc2.office.internal`  | A     | 192.168.60.12 |
   | `www.office.internal`  | CNAME | `srv1.office.internal` |

2. Make the DNS service start at boot.
3. Configure `pc1` and `pc2` to use 192.168.60.2 as their DNS server, so
   that `ping pc2.office.internal` works on pc1.

## Choosing the software

`srv1` has **BIND 9** (service `named`, config in `/etc/bind/`) and
**dnsmasq** installed, both stopped. BIND is the classic choice and worth
knowing; dnsmasq is quicker to set up. The checks accept either.

## Useful commands

- Ask a specific server: `dig @192.168.60.2 pc1.office.internal`
- Ask the way applications do: `getent hosts pc1.office.internal`
- What does this node use as DNS server? `resolvectl status`
- BIND: check your work with `named-checkconf` and
  `named-checkzone office.internal <zone file>`; logs with `journalctl -u named -e`.

Note: this lab has no internet connection, so only the names you define
will resolve.
