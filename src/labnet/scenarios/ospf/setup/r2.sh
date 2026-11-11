# Scenario "ospf": interface addresses are set; routing is the exercise.
write_net() {  # write_net <file> <interface> <address>
    printf '[Match]\nName=%s\n\n[Network]\nAddress=%s\n' "$2" "$3" > "/etc/systemd/network/$1.network"
}
write_net 10-net12 eth0 10.0.12.2/30
write_net 20-net23 eth1 10.0.23.1/30
networkctl reload
