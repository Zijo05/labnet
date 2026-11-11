# Changelog

All notable changes to labnet are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- One-line install straight from GitHub:
  `wget -qO- https://raw.githubusercontent.com/Zijo05/labnet/main/install.sh | bash`.
  The installer also still works from a clone. `LABNET_REF` picks a branch
  or tag.
- The example topologies ship inside the package. `labnet up office` works
  without a clone, and `labnet examples` lists them or prints one to copy.

### Changed

- Examples moved from `examples/` to `src/labnet/examples/`.
- The installer installs `python3-venv` when it is missing, stops with a
  clear message when Incus is not in the package sources (Debian 12 without
  backports), and runs the final check with the new group right away.

## [0.1.0] - 2026-11-11

First release.

### Added

- **Topology files** in YAML: networks (plain switches and internet uplinks)
  and nodes with a role (`pc`, `router`, `server`), a `count` shorthand, and
  optional `memory`, `cpu` and `image`. Validation reports every problem at
  once and names the file and node.
- **VLANs**: a link may configure its switch port as an access port
  (`vlan: 10`) or a trunk (`trunk: [10, 20]`).
- **Role images** built locally from Debian 13, with networking tools
  preinstalled and services installed but disabled.
- **Core commands**: `up` (idempotent, waits until nodes are ready), `down`,
  `list`, `shell`, `exec`, `start`, `stop`.
- **Operations**: `snapshot` and `restore`; `link down|up` (cable pull);
  `link set --delay --loss` and `link clear`; `capture` to the screen or a
  pcap file; `export ansible`.
- **Scenarios**: `scenario list | start | task | check | hint | reset`, a
  declarative check format, and eight scenarios with written reference
  solutions: static addressing, routing, DHCP, NAT, DNS, firewall, OSPF,
  troubleshooting.
- **Web UI** (`labnet web`): topology picture with link state, browser
  consoles, lab and node controls, link controls, and a scenario panel.
  Local-only by default.
- `install-check`, `install.sh`, and `images build | list | remove`.
- Documentation: install guide, user guide, architecture, design decisions,
  scenarios, reference solutions, troubleshooting, limitations.

### Safety

- labnet only manages Incus objects carrying its own `user.labnet.managed`
  marker, and refuses to build a lab whose names clash with anything else.
- Destructive commands ask for confirmation unless `--yes` is given.
- Lab nodes are created with autostart off.

### Tested on

- Debian 13 under WSL2 (Windows 11), Incus 6.0.4, Python 3.13: all unit and
  integration tests.
- Ubuntu 26.04 under WSL2, Python 3.14: unit tests and `install-check` only.
