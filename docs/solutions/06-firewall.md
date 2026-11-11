# Solution 6: Firewall for a guest network

Scenario: `labnet scenario start firewall`

## The idea

A router forwards everything by default. To keep guests out of the office
LAN, filter in the **forward** hook on `r1`: the path taken by packets that
pass *through* the router.

The design is "deny by default, allow what is needed":

| From | To | Allowed? |
|------|----|----------|
| office (eth0) | anywhere | yes |
| guest (eth1) | internet (eth2) | yes |
| guest (eth1) | office (eth0) | **no** |
| replies to connections that were allowed | | yes |

The last row is what makes the firewall *stateful*. The kernel's connection
tracking remembers each allowed connection, and `ct state established,related`
matches packets that belong to one. Without it, the office could send to
guests but never hear the answer.

## The rules

On **r1**, replace `/etc/nftables.conf`. The NAT table from before stays; a
filter table is added.

```sh node=r1
cat > /etc/nftables.conf <<'EOF'
#!/usr/sbin/nft -f
flush ruleset

table inet filter {
    chain forward {
        type filter hook forward priority filter; policy drop;

        # Replies to anything that was allowed out.
        ct state established,related accept

        # The office LAN may start connections to anywhere.
        iifname "eth0" accept

        # Guests may start connections to the internet only.
        iifname "eth1" oifname "eth2" accept

        # Everything else (guest -> office, internet -> inside) hits the
        # chain's policy and is dropped.
    }
}

table ip nat {
    chain postrouting {
        type nat hook postrouting priority srcnat;
        oifname "eth2" masquerade
    }
}
EOF
nft -c -f /etc/nftables.conf
nft -f /etc/nftables.conf
```

`nft -c` checks the file without loading it. `nft -f` loads it; because the
file begins with `flush ruleset`, the old rules are replaced in one step, with
no moment in which the router is unprotected. The `nftables` service is
already enabled in this scenario, so the file is loaded at every boot.

## Verify

On guest1:

```sh
ping -c 2 1.1.1.1                    # works
ping -c 2 -W 1 192.168.10.11         # no reply
curl -m 3 http://192.168.10.5        # times out
```

On pc1:

```sh
ping -c 2 1.1.1.1                    # works
curl http://192.168.10.5             # "Internal office server. Staff only."
ping -c 2 192.168.20.11              # works: office -> guest is allowed
```

On r1, see the counters and the tracked connections:

```sh
nft list ruleset
```

## What to take away

- `input` is for packets *to* the router, `forward` for packets *through*
  it. This scenario only filters `forward`. Guests can therefore still reach
  the router's own addresses, including 192.168.10.1. Closing that would
  need an `input` chain as well. Try it as an extension.
- Rule order matters: the first matching `accept` or `drop` ends the search.
- Blocking only ping (ICMP) is a common half-fix. The check with `curl`
  exists to catch it.
