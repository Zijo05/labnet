# Legacy prototype

`lab.sh` is the original bash prototype that labnet grew out of. It is kept
unchanged for reference and is not used by labnet.

It creates one Incus network (`ured`, 10.10.20.0/24) on which Incus itself
provides DHCP, DNS and NAT, plus 20 Ubuntu containers `pc01`..`pc20` with
fixed addresses and an Ansible inventory. Because Incus does all the network
services, there is nothing left for the user to configure, which is the gap
labnet fills.

labnet never touches the prototype's containers, network or profile: it only
manages objects that carry its own `user.labnet.managed` marker.
