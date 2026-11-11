# Solution 4: NAT to the internet

Scenario: `labnet scenario start nat`

## The idea

The PCs use private addresses (192.168.10.x). The provider's network has no
route to them, so a packet from pc1 can leave, but the reply can never come
back. **Source NAT** fixes this on the router: every packet leaving through
the WAN interface gets the router's own WAN address as its source. The router
remembers each connection and rewrites the replies back. `masquerade` is
source NAT that uses whatever address the outgoing interface currently has,
which is what you want when that address comes from DHCP.

## 1. Connect the router to the provider

On **r1**, make eth1 a DHCP client:

```sh node=r1
cat > /etc/systemd/network/20-wan.network <<'EOF'
[Match]
Name=eth1

[Network]
DHCP=ipv4
EOF
networkctl reload
```

After a few seconds `ip -brief address` shows an address on eth1 and
`ip route` shows a default route via the provider's gateway. `ping 1.1.1.1`
works from r1 itself, because r1's own address is one the provider knows.

## 2 and 3. NAT, saved in the configuration file

Write the rules to `/etc/nftables.conf` and enable the service that loads
that file at boot:

```sh node=r1
cat > /etc/nftables.conf <<'EOF'
#!/usr/sbin/nft -f
flush ruleset

table ip nat {
    chain postrouting {
        type nat hook postrouting priority srcnat;
        oifname "eth1" masquerade
    }
}
EOF
systemctl enable --now nftables
```

Reading the rule: in the `postrouting` hook (the last step before a packet
leaves), for packets going out through `eth1`, replace the source address.

## Verify

```sh
nft list ruleset                 # on r1
ping -c 3 1.1.1.1                # on pc1 and pc2
getent hosts deb.debian.org      # on pc1: name resolution works too
```

See NAT happen: run `labnet capture r1 wan icmp` and ping from pc1. The
packets on the WAN side carry r1's WAN address, not 192.168.10.11. Compare
with `labnet capture r1 lan icmp`, where the original address is visible.

## What to take away

- Two different problems looked the same ("no internet"): the router had no
  uplink, and the LAN had no NAT. Testing from the router first separates
  them.
- `nft add rule ...` changes the running system only. The file plus
  `systemctl enable nftables` is what makes it survive a reboot.
