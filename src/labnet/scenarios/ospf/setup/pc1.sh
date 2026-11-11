# Scenario "ospf": a host on r1's LAN.
cat > /etc/systemd/network/05-static.network <<'EOF'
[Match]
Name=eth0

[Network]
Address=10.0.1.10/24
Gateway=10.0.1.1
EOF
networkctl reload
