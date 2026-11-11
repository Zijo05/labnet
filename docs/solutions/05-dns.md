# Solution 5: DNS for an internal domain

Scenario: `labnet scenario start dns`

## The idea

Two separate jobs:

1. A **server** that knows the answers for the zone `office.internal`.
2. **Clients** that know which server to ask.

Keep them apart while testing. `dig @192.168.60.2 name` asks the server
directly and ignores the client's own settings, so it tests job 1 alone.

This solution uses **BIND 9**. A dnsmasq alternative is at the end.

## 1. Declare the zone

On **srv1**, tell BIND that it is the primary server for the zone and where
the records are:

```sh node=srv1
cat >> /etc/bind/named.conf.local <<'EOF'

zone "office.internal" {
    type master;
    file "/etc/bind/db.office.internal";
};
EOF
```

## 2. Write the zone file

```sh node=srv1
cat > /etc/bind/db.office.internal <<'EOF'
$TTL 3600
@       IN  SOA   srv1.office.internal. admin.office.internal. (
                  1        ; serial: increase it whenever you change the zone
                  3600     ; refresh
                  600      ; retry
                  86400    ; expire
                  300 )    ; negative-answer TTL
        IN  NS    srv1.office.internal.

srv1    IN  A     192.168.60.2
pc1     IN  A     192.168.60.11
pc2     IN  A     192.168.60.12
www     IN  CNAME srv1
EOF
named-checkconf
named-checkzone office.internal /etc/bind/db.office.internal
systemctl enable --now named
```

How to read it:

- `@` means the zone itself (`office.internal`).
- The **SOA** record is required and names the primary server and a contact
  mail address (written with a dot instead of `@`).
- **NS** says which server is responsible for the zone.
- Names without a trailing dot are relative to the zone: `pc1` means
  `pc1.office.internal`. Names *with* a trailing dot are complete. Forgetting
  the dot after `srv1.office.internal` is the classic zone-file mistake.
- **CNAME** makes `www` an alias for `srv1`.

`named-checkconf` and `named-checkzone` find mistakes before the server does.

Test the server on its own, from any node:

```sh
dig @192.168.60.2 pc1.office.internal +short     # 192.168.60.11
dig @192.168.60.2 www.office.internal +short     # srv1.office.internal. then 192.168.60.2
```

## 3. Point the clients at the server

On **pc1**, add the DNS server (and, for convenience, the search domain) to
the interface's configuration:

```sh node=pc1
cat > /etc/systemd/network/05-static.network <<'EOF'
[Match]
Name=eth0

[Network]
Address=192.168.60.11/24
DNS=192.168.60.2
Domains=office.internal
EOF
networkctl reload
```

On **pc2**:

```sh node=pc2
cat > /etc/systemd/network/05-static.network <<'EOF'
[Match]
Name=eth0

[Network]
Address=192.168.60.12/24
DNS=192.168.60.2
Domains=office.internal
EOF
networkctl reload
```

`Domains=office.internal` is a search domain: `ping pc2` is then tried as
`pc2.office.internal`.

## Verify

```sh
resolvectl status eth0            # Current DNS Server: 192.168.60.2
getent hosts pc2.office.internal  # the way programs resolve names
ping -c 2 pc2                     # short name, thanks to the search domain
ping -c 2 www.office.internal
```

A note on `/etc/resolv.conf`: on these nodes it contains `nameserver
127.0.0.53`. That is `systemd-resolved`, a small local resolver that forwards
to the server configured for each interface. `resolvectl status` shows the
real servers.

## Alternative: dnsmasq

```sh
cat > /etc/dnsmasq.d/office.conf <<'EOF'
interface=eth0
bind-dynamic
no-resolv
local=/office.internal/
host-record=srv1.office.internal,192.168.60.2
host-record=pc1.office.internal,192.168.60.11
host-record=pc2.office.internal,192.168.60.12
cname=www.office.internal,srv1.office.internal
EOF
systemctl enable --now dnsmasq
```

(Not run against this scenario's checks; the BIND solution above is the one
that was. `bind-dynamic` matters: without it dnsmasq tries to take port 53 on
every address and collides with systemd-resolved on 127.0.0.53.)

## What to take away

- "DNS does not work" is two questions: does the server answer (`dig
  @server`), and is the client asking the right server (`resolvectl status`)?
- Zone files are picky about trailing dots, and the serial number must grow
  with every change once other servers copy the zone.
