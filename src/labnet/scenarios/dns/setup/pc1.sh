# Scenario "dns": the PC has an address but no DNS server configured.
cat > /etc/systemd/network/05-static.network <<'EOF'
[Match]
Name=eth0

[Network]
Address=192.168.60.11/24
EOF
networkctl reload
