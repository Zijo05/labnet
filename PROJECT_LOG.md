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

## Milestone 4: scenarios

**Done**

- `scenario.py`: scenario loader and validator, declarative checks
  (`address_in`, `ping`, `run` with expected output or exit code, optional
  `expect: fail` and `wait`), per-node setup scripts, hints, reset.
- CLI: `scenario list | start | task | check | hint | reset`.
- Eight scenarios, each with task text, three or more hints, and checks:
  static, routing, dhcp, nat, dns, firewall, ospf, troubleshoot.
- A written reference solution for each in `docs/solutions/`. Code blocks
  marked `node=<name>` are the executable steps.
- Integration test per scenario: start it, confirm the checks fail unsolved,
  run the solution blocks straight out of the Markdown, confirm every check
  passes, restart the whole lab, confirm every check still passes.
- Extra OSPF test: pull the r1-r3 cable, traffic resumes via r2 within 15
  seconds (tuned hello/dead timers), and returns to the direct path when the
  cable is plugged back in.

**Result**

All 8 scenario tests, the OSPF reroute test and the hint/reset test pass
(about 9 minutes in total). The nat, firewall and troubleshoot scenarios need
the host to be online.

**Tried and failed / surprises**

- `sysctl --system` exits non-zero inside an unprivileged container, because
  it may not set kernel-wide keys such as `kernel.sysrq`. With `sh -e` that
  aborted the troubleshooting scenario's setup script. The script and the
  written solution now set the one key directly with `sysctl -w`; the check
  that re-applies all files ignores the exit code.
- dnsmasq's default is to bind port 53 on every address, which collides with
  systemd-resolved on 127.0.0.53. All dnsmasq configurations in the project
  use `bind-dynamic`.
- Reset is "delete and rebuild", not "restore a snapshot". With the `dir`
  storage backend a snapshot is a full copy, so snapshotting every scenario
  at start would cost more than rebuilding on the rare reset.

## Milestone 5: web UI

**Done**

- `labnet web`: FastAPI backend (`web/app.py`) that only calls the engine and
  scenario modules, plus a plain HTML/CSS/JavaScript page with no build step.
  Cytoscape.js, xterm.js and marked are vendored at pinned versions.
- Topology picture (layered: internet at the top, hosts at the bottom) with
  node state, addresses, unplugged links (red, dashed) and shaped links
  (orange, with the delay/loss shown).
- Clicking a device opens a console tab: xterm.js over a WebSocket to a PTY
  running the same `incus exec` command as `labnet shell`.
- Controls: start/stop lab and node, snapshot/restore, delete, cable
  unplug/plug, delay and loss per interface.
- Scenario panel: list and start scenarios, rendered task text, "Check my
  work" with per-check results and explanations, hints, reset.
- Security: binds to 127.0.0.1 by default; every HTTP request and WebSocket
  is checked against cross-site requests (Origin) and DNS rebinding (Host);
  `--host` other than local prints a warning and asks for confirmation.

**Verified**

- 16 unit tests for the backend with the engine faked (trust check, JSON
  shapes, error mapping, static files).
- 2 integration tests against real nodes: the WebSocket terminal is a root
  shell with a real PTY of the requested size, closing it leaves no process
  behind on the node, a cross-site WebSocket is refused, and the HTTP API
  changes link state, shaping, node state and snapshots.
- By hand, in a browser on the Windows side against the server in WSL
  (`http://localhost:8080`, WSL mirrored networking): the topology, a console
  on r1 with coloured output, unplugging a cable and seeing it in the
  picture, hints, and the firewall scenario going from "4 of 7" to
  "Solved: all 7 checks passed" after applying the written solution. The
  screenshots in `docs/images/` are from that session.

**Tried and failed / surprises**

- The first layout (force-directed "cose") was cramped and overlapped labels.
  Replaced with Cytoscape's layered "breadthfirst" layout rooted at the
  uplink, which looks like a normal network diagram.
- Inline SVG icons without a width and height rendered at a few pixels.
- The browser kept running a cached `app.js` after it changed. Static files
  are now served with `Cache-Control: no-cache` (revalidate every time).
- The page redrew its side panels on every 4-second poll, so a button could
  be replaced just as it was clicked. Panels now redraw only when their data
  changes.
- In a hidden browser pane the page did not paint at all (animation frames
  are paused for background pages). Not a labnet bug, but it cost some time.

**Not covered by automated tests**

- The JavaScript itself. It was exercised by hand as described above.

## Milestone 6: polish

**Done**

- `install.sh`: one command from a clone. Installs Incus and pipx if
  missing, initialises Incus, adds the user to `incus-admin`, installs labnet
  with pipx, runs `install-check`. Skips whatever is already done.
- Documentation: `README.md` with screenshots, `docs/install.md`,
  `docs/user-guide.md`, `docs/architecture.md`, `docs/design-decisions.md`,
  `docs/scenarios.md`, `docs/troubleshooting.md`, `docs/limitations.md`,
  eight reference solutions, `CHANGELOG.md`.
- Release tagged `v0.1.0` (local tag; nothing was pushed or published).

**Verified**

- `install.sh` on Debian 13: Incus, its initialisation and the group
  membership were already in place, so those steps were skipped; the pipx
  install and `install-check` ran. The pipx-installed copy (outside the
  development virtualenv) lists the scenarios and contains the web assets.
- Ubuntu 26.04 with Python 3.14: all unit tests and the linter pass, and
  `install-check` reports the missing Incus with the command to install it.
- Claims in the documentation that had not been exercised were run first:
  a 20-PC lab (74 s to build, about 34 MiB per idle node, 22 s to delete);
  a node with a custom Alpine image; SSH between nodes with a key for root,
  root password login refused, `adduser` available.

**Not verified**

- The apt, `incus admin init` and `usermod` steps of `install.sh` on a blank
  system (they need an interactive sudo password).
- Building labs on Ubuntu, on Debian 12, or on native (non-WSL) Linux.
  Incus was deliberately not installed in the Ubuntu WSL distro: all WSL
  distros share one network namespace, and a second Incus daemon there could
  have interfered with the Debian one and with the prototype lab.

**Tried and failed / surprises**

- The installer's hints first told users to `pipx install 'labnet[web]'`,
  which would look on PyPI, where labnet is not published. They now say to
  install from the repository.
- `/usr/bin/time` does not exist on a minimal Debian; timings were taken with
  the shell's `time`.

## Milestone 7: stretch goals

| Goal | Result |
|------|--------|
| VLANs | **Done.** Access and trunk ports as link settings in the topology, shown in `list` and the web UI, with unit tests and an integration test (isolation between VLANs in one IP subnet, then router-on-a-stick over a trunk). `examples/vlan.yaml`. |
| Windows VMs as nodes | **Not attempted.** Needs a licensed image, several GB of memory per node and nested virtualisation; the whole WSL VM has 6 GB. |
| Topology editor | **Not built.** Labs are created from YAML or by starting a scenario. |

## What remains / ideas for next steps

- Publish: create the GitHub repository, replace `<repository-url>` in
  `README.md` and `docs/install.md`, push the `main` branch and the `v0.1.0`
  tag. Put the owner's full name in `LICENSE`.
- Run the installer and the integration tests on a clean Ubuntu 24.04 and on
  native Linux, and update the table in `docs/install.md`.
- A ninth scenario for VLANs (the lab and test exist; it needs a task, hints
  and a solution).
- A GIF of the web UI for the README.
- Continuous integration for the unit tests and linter (they need no Incus).
- Automated tests for the web page's JavaScript.
- A true carrier-loss cable pull (needs a root helper on the host).
- Ideas beyond that: IPv6 scenarios, a BGP scenario, bandwidth limits on
  links, the visual topology editor.

## Final state (v0.1.0)

- 67 unit tests pass in about 3 seconds (Debian 13 / Python 3.13 and
  Ubuntu 26.04 / Python 3.14).
- 26 integration tests pass in 13 minutes on Debian 13 under WSL2 with
  Incus 6.0.4: core commands, roles and uplink, snapshots, links, capture,
  Ansible, all eight scenarios with their written solutions (before and
  after a lab restart), OSPF rerouting, the web backend, VLANs.
- `ruff format --check` and `ruff check` are clean.
- Last change before the tag: `labnet web --host 0.0.0.0` is refused with an
  explanation, because the request check needs one specific host address.
- The prototype lab was never touched: `pc01`..`pc20` are still present and
  stopped, with network `ured` and profile `lab`.
