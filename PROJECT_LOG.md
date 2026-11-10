# Project log

A running record of what was done in each milestone, what was tried and
failed, and what remains. Newest milestone last.

## Milestone 0: recon and spikes

**Done**

- Checked the machine. The plan's assumptions were partly wrong:
  - Incus and the prototype lab are in the **Debian 13** WSL distro, not Ubuntu.
  - The Ubuntu distro is 26.04 (not 24.04) and has no Incus.
  - The project folder was empty; `lab.sh` was in the Debian home directory
    and was copied (unchanged) to `legacy/lab.sh`.
- Decided to develop and test on Debian 13 and to leave the prototype lab
  (`pc01`..`pc20`, network `ured`, profile `lab`) completely alone.
- Installed `python3-venv`, `pipx` and `curl` in the Debian distro (needed to
  create a virtualenv; nothing else on the system was changed).
- Answered all eight "verify, do not assume" questions by experiment. Results
  and decisions are in `docs/design-decisions.md`; scripts are in `spikes/`.
- Scaffolded the repository (`pyproject.toml`, `src/` layout, tests, lint).

**Tried and failed / surprises**

- Passing shell one-liners from PowerShell into WSL mangled the quoting, and
  `wsl -- bash -c` expands `$VARIABLES` in the default shell first. Solution:
  run script files, or use `wsl --exec`.
- Addresses set by hand in the first spike had vanished by the second. The
  cause was not networking: WSL had shut the distro down between the two
  commands and Incus restarted the containers. A background `sleep infinity`
  session kept the distro alive for the rest of development. This behaviour
  is now documented for users.

## Milestone 1: engine and core CLI

**Done**

- `topology.py`: YAML reader and validator that reports every problem at once
  and names the file and node, with "did you mean" suggestions.
- `plan.py`: pure mapping from a topology to Incus networks and containers.
- `incus.py`: the only module that runs the `incus` command.
- `images.py`: role image builder (all three roles are defined; the `pc`
  image is exercised in this milestone).
- `engine.py`: `up` (idempotent, waits for readiness), `down`, `start`, `stop`,
  reading lab state, node name resolution.
- `cli.py`: `install-check`, `up`, `down`, `list`, `shell`, `exec`, `start`,
  `stop`, `images`.
- 28 unit tests (no Incus needed) and 5 integration tests.

**Measured**

- First `up` of three PCs including building the `pc` image: 54 seconds.
- Repeating `up` on an existing lab: 0.7 seconds, nothing recreated.
- An idle PC node uses about 26 MiB of its 256 MiB limit.

**Tried and failed / surprises**

- `shutil.which("docker")` found Docker Desktop's Windows client through the
  WSL PATH, which would have produced a false warning. The check now looks for
  the Docker daemon's socket instead.

## Milestone 2: roles and multi-segment labs

**Done**

- Built and used all three role images (`labnet-pc`, `labnet-router`,
  `labnet-server`), each 130–145 MiB compressed.
- `examples/office.yaml`: router with two LANs and an uplink, a server and
  three PCs. Hand-configured it with config files: routing between the LANs,
  WAN address by DHCP from the uplink, nftables masquerade, Kea DHCP on the
  server. LAN clients reached the internet.
- Integration tests configure a lab the same way (with dnsmasq for DHCP) and
  check routing, DHCP, NAT, and that it all survives `stop` and `start`.

**Measured**

- First `up` of the five-node office lab, including building the router and
  server images: 2 minutes. Rebuilding all three images: 2 minutes 6 seconds.

**Tried and failed / surprises**

- Every container had `net.ipv4.ip_forward = 1`, servers and PCs included,
  because a new network namespace inherits the host's value and the host
  forwards (Incus needs that for NAT). The image recipe now writes the value
  explicitly for every role: 1 for routers, 0 for the rest.
- A PC took about 20 seconds to get a lease after the DHCP server was
  started. That is the DHCP client's normal retry back-off, not a fault. The
  scenario checks retry for this reason, and the docs mention
  `networkctl renew eth0` as the impatient option.

## Milestone 3: snapshots, link control, capture, Ansible export

**Done**

- `snapshot` / `restore` (per-lab, named, restore restarts running nodes and
  waits until they are ready again).
- `link down|up` (cable pull), `link set --delay --loss` and `link clear`
  (netem), with the state shown in `labnet list`.
- `capture` to the screen or to a pcap file.
- `export ansible`: inventory using the Incus connection plugin, verified by
  running `ansible -m ping` against nodes that have no addresses at all.
- 5 more integration tests, one per command group.

**Tried and failed / surprises**

- A capture with `-c 6` hung in a manual test because fewer than six matching
  packets ever arrived; tcpdump was still waiting inside the node. Nothing to
  fix in the tool, but the user guide says how `-c` behaves.
- pcap output must not pass through a terminal, so file captures run
  `incus exec --force-noninteractive`.
