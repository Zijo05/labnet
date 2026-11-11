# Scenario "nat": the router's LAN side is already configured. Its WAN side is not.
cat > /etc/systemd/network/10-lan.network <<'EOF'
[Match]
Name=eth0

[Network]
Address=192.168.10.1/24
EOF
networkctl reload
