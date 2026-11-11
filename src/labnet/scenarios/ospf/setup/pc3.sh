# Scenario "ospf": a host on r3's LAN.
cat > /etc/systemd/network/05-static.network <<'EOF'
[Match]
Name=eth0

[Network]
Address=10.0.3.10/24
Gateway=10.0.3.1
EOF
networkctl reload
