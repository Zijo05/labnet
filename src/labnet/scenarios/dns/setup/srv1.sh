# Scenario "dns": addressing is done; the DNS server is the exercise.
cat > /etc/systemd/network/10-lan.network <<'EOF'
[Match]
Name=eth0

[Network]
Address=192.168.60.2/24
EOF
networkctl reload
