# Scenario "firewall": a visitor's laptop on the guest network.
cat > /etc/systemd/network/05-static.network <<'EOF'
[Match]
Name=eth0

[Network]
Address=192.168.20.11/24
Gateway=192.168.20.1
DNS=1.1.1.1
EOF
networkctl reload
