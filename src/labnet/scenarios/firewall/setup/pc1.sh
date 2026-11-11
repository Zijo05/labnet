# Scenario "firewall": an office PC.
cat > /etc/systemd/network/05-static.network <<'EOF'
[Match]
Name=eth0

[Network]
Address=192.168.10.11/24
Gateway=192.168.10.1
DNS=1.1.1.1
EOF
networkctl reload
