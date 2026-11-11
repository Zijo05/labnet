# Scenario "nat": the PC is fully configured, with r1 as gateway and a public DNS server.
cat > /etc/systemd/network/05-static.network <<'EOF'
[Match]
Name=eth0

[Network]
Address=192.168.10.12/24
Gateway=192.168.10.1
DNS=1.1.1.1
EOF
networkctl reload
