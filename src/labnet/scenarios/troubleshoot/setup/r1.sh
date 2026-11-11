# Scenario "troubleshoot": the office router, with two faults planted in it.
cat > /etc/systemd/network/10-lan.network <<'EOF'
[Match]
Name=eth0

[Network]
Address=192.168.10.1/24
EOF
cat > /etc/systemd/network/20-wan.network <<'EOF'
[Match]
Name=eth1

[Network]
DHCP=ipv4
EOF
networkctl reload

# Fault: the masquerade rule names the LAN interface instead of the WAN one.
cat > /etc/nftables.conf <<'EOF'
#!/usr/sbin/nft -f
# Office router: NAT for the LAN.
flush ruleset

table ip nat {
    chain postrouting {
        type nat hook postrouting priority srcnat;
        oifname "eth0" masquerade
    }
}
EOF
systemctl enable --now nftables

# Fault: a "hardening" file that switches packet forwarding off.
cat > /etc/sysctl.d/99-hardening.conf <<'EOF'
# Security hardening applied from the server baseline checklist.
net.ipv4.conf.all.rp_filter = 1
net.ipv4.ip_forward = 0
net.ipv4.tcp_syncookies = 1
EOF
# Apply it now as well; at every boot the file does it again.
sysctl -w net.ipv4.ip_forward=0 >/dev/null
