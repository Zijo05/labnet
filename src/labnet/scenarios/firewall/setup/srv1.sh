# Scenario "firewall": the office file/web server.
cat > /etc/systemd/network/10-lan.network <<'EOF'
[Match]
Name=eth0

[Network]
Address=192.168.10.5/24
Gateway=192.168.10.1
DNS=1.1.1.1
EOF
networkctl reload
echo "Internal office server. Staff only." > /var/www/html/index.html
systemctl enable --now nginx
