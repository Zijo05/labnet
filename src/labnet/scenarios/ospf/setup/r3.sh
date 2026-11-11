# Scenario "ospf": interface addresses are set; routing is the exercise.
write_net() {  # write_net <file> <interface> <address>
    printf '[Match]\nName=%s\n\n[Network]\nAddress=%s\n' "$2" "$3" > "/etc/systemd/network/$1.network"
}
write_net 10-lan3 eth0 10.0.3.1/24
write_net 20-net23 eth1 10.0.23.2/30
write_net 30-net13 eth2 10.0.13.2/30
networkctl reload
