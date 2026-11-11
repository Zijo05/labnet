# Solution 3: DHCP server

Scenario: `labnet scenario start dhcp`

## The idea

A DHCP client broadcasts "who can give me an address?" on its network. A
DHCP server on the same network answers with an address from its pool, plus
options such as the gateway and DNS server. The server itself must have a
fixed address, otherwise it could not be found reliably.

This solution uses **Kea**. A dnsmasq alternative is at the end.

## 1. Static address for the server

On **srv1**:

```sh node=srv1
cat > /etc/systemd/network/10-lan.network <<'EOF'
[Match]
Name=eth0

[Network]
Address=192.168.50.2/24
EOF
networkctl reload
```

## 2. Kea configuration

Kea is configured in JSON. The file below says: listen on eth0, keep leases
in a file, and serve one subnet with one pool.

```sh node=srv1
cat > /etc/kea/kea-dhcp4.conf <<'EOF'
{
  "Dhcp4": {
    "interfaces-config": {
      "interfaces": [ "eth0" ]
    },
    "lease-database": {
      "type": "memfile",
      "persist": true,
      "name": "/var/lib/kea/kea-leases4.csv"
    },
    "valid-lifetime": 3600,
    "subnet4": [
      {
        "id": 1,
        "subnet": "192.168.50.0/24",
        "pools": [ { "pool": "192.168.50.100 - 192.168.50.150" } ]
      }
    ]
  }
}
EOF
kea-dhcp4 -t /etc/kea/kea-dhcp4.conf
systemctl enable --now kea-dhcp4-server
```

`kea-dhcp4 -t` checks the file without starting the server; a JSON mistake is
reported with its line number. `enable --now` starts the service and also
makes it start at every boot.

## 3. Clients

Nothing needs configuring on the PCs. They have been asking since they
booted, but a client that gets no answer waits longer and longer between
attempts, so it can take up to a minute until they try again. To ask
immediately, on **pc1** and **pc2**:

```sh node=pc1
networkctl reconfigure eth0
```

```sh node=pc2
networkctl reconfigure eth0
```

## Verify

```sh
ip -brief address                        # on a PC: 192.168.50.1xx/24
networkctl status eth0                   # shows the lease and the DHCP server
cat /var/lib/kea/kea-leases4.csv         # on srv1: one line per lease
journalctl -u kea-dhcp4-server -e        # DHCP4_LEASE_ALLOC lines
```

To watch the four-step exchange (Discover, Offer, Request, Ack), run
`labnet capture srv1 lan port 67 or port 68` and then
`networkctl reconfigure eth0` on a PC.

## Alternative: dnsmasq

The same result in four lines. `port=0` switches off dnsmasq's DNS function,
which is not wanted here; `bind-dynamic` makes it listen only on the named
interface.

```sh
cat > /etc/dnsmasq.d/lab.conf <<'EOF'
interface=eth0
bind-dynamic
port=0
dhcp-range=192.168.50.100,192.168.50.150,1h
EOF
systemctl enable --now dnsmasq
```

(Use one server or the other, not both at once. The dnsmasq variant was
checked as part of labnet's test suite in a different lab; the Kea variant
above is the one run against this scenario's checks.)

## What to take away

- DHCP works by broadcast, so the server must be on the same network as the
  clients (or a relay must forward the requests).
- Leases last a limited time. Changing a server setting does not reach
  clients until they renew.
