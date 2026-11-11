# Scenario "troubleshoot": pc2 was moved from another office and still has
# its old static configuration. That is the fault on this node.
cat > /etc/systemd/network/05-static.network <<'EOF'
# Static address for the accounting PC (branch office).
[Match]
Name=eth0

[Network]
Address=192.168.100.12/24
Gateway=192.168.100.1
DNS=192.168.100.2
EOF
networkctl reload
