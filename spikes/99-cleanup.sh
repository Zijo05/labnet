#!/usr/bin/env bash
# Remove everything the spike scripts created (only names starting "lnspike-").
set -u
for inst in $(incus list --format csv -c n | grep '^lnspike-'); do
  incus delete --force "$inst" && echo "deleted instance $inst"
done
for net in $(incus network list --format csv | cut -d, -f1 | grep '^lnspike-'); do
  incus network delete "$net" && echo "deleted network $net"
done
for img in $(incus image alias list --format csv | cut -d, -f1 | grep '^lnspike-'); do
  incus image delete "$img" && echo "deleted image $img"
done
