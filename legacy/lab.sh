#!/usr/bin/env bash
# lab.sh - simulirana uredska mreza od N Linux "racunara" (Incus system containers)
#
# Pokretanje (unutar Ubuntu 24.04 VM-a):
#   ./lab.sh install     # instalira Incus + Ansible (jednom), pa se odjavi/prijavi
#   ./lab.sh create      # pravi mrezu 10.10.20.0/24 i racunare pc01..pc20
#   ./lab.sh list        # pregled racunara i adresa
#   ./lab.sh snapshot    # snapshot "cisto" na svim racunarima
#   ./lab.sh restore     # vrati sve na snapshot "cisto"
#   ./lab.sh stop|start  # ugasi / upali sve racunare
#   ./lab.sh destroy     # obrisi racunare, mrezu i profil
#
# Postavke mozes promijeniti preko varijabli, npr.:  BROJ=10 ./lab.sh create

set -euo pipefail

BROJ="${BROJ:-20}"                    # broj racunara
PREFIKS="${PREFIKS:-pc}"              # imena: pc01, pc02, ...
MREZA="${MREZA:-ured}"                # ime Incus mreze
SUBNET="${SUBNET:-10.10.20}"          # prva tri okteta
GATEWAY="${SUBNET}.1"                 # adresa "rutera" (Incus bridge)
PRVA_IP="${PRVA_IP:-101}"             # pc01 = .101, pc02 = .102, ...
PROFIL="${PROFIL:-lab}"
IMAGE="${IMAGE:-images:ubuntu/24.04/cloud}"
KORISNIK="${KORISNIK:-admin}"         # korisnik na svakom racunaru
KLJUC="${KLJUC:-$HOME/.ssh/lab_key}"  # SSH kljuc za pristup labu
SNAP="${SNAP:-cisto}"

imena() { for i in $(seq 1 "$BROJ"); do printf "%s%02d\n" "$PREFIKS" "$i"; done; }
ip_za() { echo "${SUBNET}.$((PRVA_IP + 10#${1#"$PREFIKS"} - 1))"; }

cmd_install() {
  sudo apt-get update
  sudo apt-get install -y incus ansible
  sudo incus admin init --minimal
  sudo usermod -aG incus-admin "$USER"
  echo ">> Gotovo. Odjavi se i ponovo prijavi (ili: newgrp incus-admin), pa pokreni: ./lab.sh create"
}

cmd_create() {
  # 1) SSH kljuc
  if [[ ! -f "$KLJUC" ]]; then
    ssh-keygen -t ed25519 -N "" -f "$KLJUC" -C "lab"
  fi
  local pub; pub="$(cat "$KLJUC.pub")"

  # 2) Mreza (bridge s DHCP-om, DNS-om i NAT-om prema internetu)
  if ! incus network show "$MREZA" >/dev/null 2>&1; then
    incus network create "$MREZA" \
      ipv4.address="${GATEWAY}/24" ipv4.nat=true ipv6.address=none
  fi

  # 3) Profil: disk, mrezna kartica i cloud-init (korisnik, SSH, python za Ansible)
  if ! incus profile show "$PROFIL" >/dev/null 2>&1; then
    incus profile create "$PROFIL"
    incus profile device add "$PROFIL" root disk path=/ pool=default
    incus profile device add "$PROFIL" eth0 nic network="$MREZA" name=eth0
    incus profile set "$PROFIL" limits.memory=256MiB limits.cpu=1
  fi
  incus profile set "$PROFIL" cloud-init.user-data - <<EOF
#cloud-config
users:
  - name: ${KORISNIK}
    groups: [sudo]
    shell: /bin/bash
    sudo: "ALL=(ALL) NOPASSWD:ALL"
    ssh_authorized_keys:
      - ${pub}
packages: [openssh-server, python3, curl, htop]
timezone: Europe/Sarajevo
EOF

  # 4) Racunari s fiksnim adresama
  for ime in $(imena); do
    if incus info "$ime" >/dev/null 2>&1; then
      echo "-- $ime vec postoji, preskacem"; continue
    fi
    local ip; ip="$(ip_za "$ime")"
    echo ">> $ime  ->  $ip"
    incus launch "$IMAGE" "$ime" --profile "$PROFIL" \
      --device "eth0,ipv4.address=${ip}"
  done

  # 5) Sacekaj da cloud-init zavrsi (korisnik, SSH, paketi)
  echo ">> Cekam da se racunari podignu..."
  for ime in $(imena); do
    incus exec "$ime" -- cloud-init status --wait >/dev/null 2>&1 || \
      echo "!! $ime: cloud-init nije uspjesno zavrsio (provjeri: incus exec $ime -- cloud-init status --long)"
  done

  cmd_inventory
  echo
  echo ">> Lab spreman. Probaj:"
  echo "   ssh -i $KLJUC ${KORISNIK}@$(ip_za "${PREFIKS}01")"
  echo "   ansible -i inventory.ini svi -m ping"
}

cmd_inventory() {
  # Ansible inventar: grupe 'ured' (prva polovina), 'gosti' (druga polovina), 'svi'
  local pola=$(( (BROJ + 1) / 2 )) n=0
  {
    echo "[ured]"
    for ime in $(imena); do
      n=$((n + 1)); (( n <= pola )) && echo "$ime ansible_host=$(ip_za "$ime")"
    done
    echo
    echo "[gosti]"
    n=0
    for ime in $(imena); do
      n=$((n + 1)); (( n > pola )) && echo "$ime ansible_host=$(ip_za "$ime")"
    done
    echo
    echo "[svi:children]"
    echo "ured"
    echo "gosti"
    echo
    echo "[svi:vars]"
    echo "ansible_user=${KORISNIK}"
    echo "ansible_ssh_private_key_file=${KLJUC}"
    echo "ansible_ssh_common_args='-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'"
  } > inventory.ini
  echo ">> Napravljen inventory.ini"
}

cmd_list()     { incus list "$PREFIKS" -c ns4m; }
cmd_snapshot() { for ime in $(imena); do incus snapshot create "$ime" "$SNAP" --reuse; echo "snapshot: $ime"; done; }
cmd_restore()  { for ime in $(imena); do incus snapshot restore "$ime" "$SNAP"; echo "vraceno: $ime"; done; }
cmd_stop()     { incus stop $(imena) --force; }
cmd_start()    { incus start $(imena); }

cmd_destroy() {
  read -r -p "Obrisati sve ${PREFIKS}* racunare, mrezu '$MREZA' i profil '$PROFIL'? [da/NE] " odg
  [[ "$odg" == "da" ]] || { echo "Odustao."; exit 0; }
  for ime in $(imena); do incus delete "$ime" --force 2>/dev/null || true; done
  incus profile delete "$PROFIL" 2>/dev/null || true
  incus network delete "$MREZA" 2>/dev/null || true
  rm -f inventory.ini
  echo ">> Lab obrisan."
}

case "${1:-}" in
  install|create|inventory|list|snapshot|restore|stop|start|destroy) "cmd_$1" ;;
  *) sed -n '2,15p' "$0"; exit 1 ;;
esac
