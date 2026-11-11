# Scenario "troubleshoot": DHCP and DNS server, with two faults planted in it.
cat > /etc/systemd/network/10-lan.network <<'EOF'
[Match]
Name=eth0

[Network]
Address=192.168.10.2/24
Gateway=192.168.10.1
DNS=1.1.1.1
EOF
networkctl reload

# Faults: the gateway handed to clients is wrong, and the upstream DNS
# server does not exist.
cat > /etc/dnsmasq.d/office.conf <<'EOF'
# Office DHCP and DNS.
interface=eth0
bind-dynamic

# DHCP: addresses .100 to .150, router and DNS server options.
dhcp-range=192.168.10.100,192.168.10.150,1h
dhcp-option=option:router,192.168.10.254
dhcp-option=option:dns-server,192.168.10.2

# DNS: forward everything to the provider's resolver.
no-resolv
server=192.0.2.53
EOF
systemctl enable --now dnsmasq
