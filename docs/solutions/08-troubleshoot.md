# Solution 8: Troubleshooting

Scenario: `labnet scenario start troubleshoot`

**Spoilers below.** The value of this scenario is in the searching. Try the
hints first (`labnet scenario hint`).

There are five independent faults. They are listed in the order a bottom-up
investigation finds them.

## Fault 1: pc2 has a static address from another network

**Symptom.** On pc2, `ip -brief address` shows `192.168.100.12/24`. The
office network is 192.168.10.0/24, so pc2 cannot even reach the router.

**Cause.** A leftover file from the branch office,
`/etc/systemd/network/05-static.network`. It sorts before the DHCP file, so
it wins.

**Fix.** Remove it; pc2 then falls back to DHCP like every other PC.

```sh node=pc2
rm /etc/systemd/network/05-static.network
networkctl reload
```

## Fault 2: the DHCP server hands out the wrong gateway

**Symptom.** pc1 gets a lease and can ping srv1 and 192.168.10.1, but
`ip route` shows `default via 192.168.10.254`. Nothing lives at .254, so
every packet for the internet goes nowhere.

**Cause.** A typo in `/etc/dnsmasq.d/office.conf` on srv1:
`dhcp-option=option:router,192.168.10.254`.

## Fault 3: the DNS server forwards to a resolver that does not exist

**Symptom.** Visible only after the routing faults are fixed: `ping 1.1.1.1`
works, `ping debian.org` does not. `dig @192.168.10.2 debian.org` times out
or returns SERVFAIL, so the clients are asking the right server and the
server has no answer.

**Cause.** Same file: `server=192.0.2.53`. That address is from a range
reserved for documentation; nothing answers there. The description says the
upstream resolver should be 1.1.1.1.

**Fix for faults 2 and 3.** Correct both lines on **srv1** and restart the
service:

```sh node=srv1
sed -i 's/^dhcp-option=option:router,.*/dhcp-option=option:router,192.168.10.1/' /etc/dnsmasq.d/office.conf
sed -i 's/^server=.*/server=1.1.1.1/' /etc/dnsmasq.d/office.conf
systemctl restart dnsmasq
```

## Fault 4: the router does not forward

**Symptom.** With the right gateway, pc1 still cannot ping 1.1.1.1, although
r1 itself can. `sysctl net.ipv4.ip_forward` on r1 prints `0`.

**Cause.** `sysctl -w net.ipv4.ip_forward=1` appears to fix it, but the
setting returns to 0 at the next boot. Something sets it:

```sh
grep -r ip_forward /etc/sysctl.d/ /etc/sysctl.conf
```

finds `/etc/sysctl.d/99-hardening.conf`, a "security hardening" file meant
for servers. Files are applied in name order, so `99-` overrides the
router's own `90-labnet.conf`.

**Fix.** Remove the offending line so it is no longer applied at boot (the
other two lines in the file are harmless and can stay), and switch
forwarding on for the running system:

```sh node=r1
sed -i '/ip_forward/d' /etc/sysctl.d/99-hardening.conf
sysctl -w net.ipv4.ip_forward=1
```

(`sysctl --system` re-applies every file, as a boot does. Inside a container
it also prints "permission denied" for kernel-wide settings it is not allowed
to change. Those messages are harmless.)

## Fault 5: NAT is applied on the wrong interface

**Symptom.** r1 forwards now, but pc1 still gets no replies from 1.1.1.1.
`labnet capture r1 wan icmp` shows the requests leaving with source
`192.168.10.x`: they were not translated.

**Cause.** `nft list ruleset` on r1 shows `oifname "eth0" masquerade`. eth0
is the LAN side. The WAN interface is eth1.

**Fix.** Correct the file, then load it:

```sh node=r1
sed -i 's/oifname "eth0" masquerade/oifname "eth1" masquerade/' /etc/nftables.conf
nft -f /etc/nftables.conf
```

## Give the clients their new lease

The PCs still hold the lease with the wrong gateway. It would expire by
itself after an hour; to renew now:

```sh node=pc1
networkctl reconfigure eth0
```

```sh node=pc2
networkctl reconfigure eth0
```

## Verify

```sh
ip route                       # on a PC: default via 192.168.10.1
ping -c 2 1.1.1.1
getent hosts deb.debian.org
```

Then `labnet scenario check`, and for the full proof restart the lab
(`labnet stop troubleshoot`, `labnet start troubleshoot`) and check again.

## What to take away

- One ticket, five causes. Fixing the first fault you find and declaring
  victory is the most common troubleshooting mistake.
- Work in layers: address, gateway, forwarding, NAT, DNS. Each layer can be
  tested on its own, and each test cuts the search space in half.
- "Addresses work, names do not" always means DNS.
- A setting that keeps coming back is being set by a file. Find the file.
