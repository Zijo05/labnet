# Solution 1: Static addressing

Scenario: `labnet scenario start static`

## The idea

Two hosts on the same switch can talk as soon as both have an address in the
same IP network. `192.168.1.11/24` and `192.168.1.12/24` share the network
`192.168.1.0/24`, so no router is involved.

## The quick way (lost at reboot)

```sh
ip address add 192.168.1.11/24 dev eth0     # on pc1
ip address add 192.168.1.12/24 dev eth0     # on pc2
```

This is fine for experiments. The address lives only in the kernel's memory
and is gone when the node restarts.

## The permanent way

On these nodes the network is managed by `systemd-networkd`, which reads
files in `/etc/systemd/network/`. The PC image ships a file
`10-labnet-dhcp.network` that makes every interface a DHCP client. Files are
read in name order and the **first one that matches an interface wins**, so a
file whose name sorts earlier (`05-...`) replaces the DHCP behaviour for eth0.

On **pc1**:

```sh node=pc1
cat > /etc/systemd/network/05-static.network <<'EOF'
[Match]
Name=eth0

[Network]
Address=192.168.1.11/24
EOF
networkctl reload
```

On **pc2**:

```sh node=pc2
cat > /etc/systemd/network/05-static.network <<'EOF'
[Match]
Name=eth0

[Network]
Address=192.168.1.12/24
EOF
networkctl reload
```

`networkctl reload` makes networkd read its files again and apply what
changed.

## Verify

```sh
ip -brief address          # eth0 UP 192.168.1.11/24
ping -c 3 192.168.1.12     # from pc1
networkctl status eth0     # shows which .network file is in use
```

Then `labnet scenario check`.

## What to take away

- Same network + same switch = direct communication. The `/24` says the
  first 24 bits (three numbers) identify the network.
- `ip` changes the running system; configuration files make it permanent.
  A good habit: test with `ip`, then write the file, then reboot to prove it.
