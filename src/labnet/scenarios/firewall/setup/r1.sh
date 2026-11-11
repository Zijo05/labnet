# Scenario "firewall": a working router with NAT and no packet filtering at all.
cat > /etc/systemd/network/10-office.network <<'EOF'
[Match]
Name=eth0

[Network]
Address=192.168.10.1/24
EOF
cat > /etc/systemd/network/20-guest.network <<'EOF'
[Match]
Name=eth1

[Network]
Address=192.168.20.1/24
EOF
cat > /etc/systemd/network/30-wan.network <<'EOF'
[Match]
Name=eth2

[Network]
DHCP=ipv4
EOF
networkctl reload

cat > /etc/nftables.conf <<'EOF'
#!/usr/sbin/nft -f
# Router rules. So far: NAT only. Everything is forwarded.
flush ruleset

table ip nat {
    chain postrouting {
        type nat hook postrouting priority srcnat;
        oifname "eth2" masquerade
    }
}
EOF
systemctl enable --now nftables
