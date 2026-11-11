# Scenarios

A scenario is a guided exercise: labnet builds a lab, gives you a task, and
can check whether you solved it.

## The built-in scenarios

| # | Name | You practise | Needs internet |
|---|------|--------------|----------------|
| 1 | `static` | Static IP addresses; two PCs on one switch | no |
| 2 | `routing` | A router between two networks; default gateways | no |
| 3 | `dhcp` | Running a DHCP server (Kea or dnsmasq) | no |
| 4 | `nat` | A router's uplink and source NAT with nftables | yes |
| 5 | `dns` | An authoritative DNS zone (BIND or dnsmasq) and client settings | no |
| 6 | `firewall` | Stateful packet filtering: a guest network kept out of the office | yes |
| 7 | `ospf` | Dynamic routing with FRR on three routers, and a link failure | no |
| 8 | `troubleshoot` | Finding five independent faults in a broken office network | yes |

They get harder in that order, and later ones assume what earlier ones
taught. Every scenario has a written reference solution in
[`docs/solutions/`](solutions/). Try the hints first.

## Working through one

```bash
labnet scenario list
labnet scenario start routing
```

`start` builds the lab and prints the task. Then work on the nodes:

```bash
labnet shell r1          # a console on a node; leave with 'exit'
labnet list routing      # the lab at a glance
```

When you think you are done:

```console
$ labnet scenario check
Checking scenario 'routing' (8 checks)
  PASS  r1 has 10.1.1.1 on lan-a
  PASS  r1 has 10.1.2.1 on lan-b
  PASS  pc1 has the address 10.1.1.10
  FAIL  pc2 has the address 10.1.2.10
        Found: it has no IPv4 address
  ...
5 of 8 checks passed. Keep going, or try: labnet scenario hint
```

Other commands:

```bash
labnet scenario task       # show the task again
labnet scenario hint       # hint 1; 'labnet scenario hint 2' for the next
labnet scenario reset      # throw the lab away and start over
labnet down routing        # remove the lab when you are finished
```

Some checks wait a while before failing, because DHCP leases and routing
protocols take a moment. `labnet scenario check --no-wait` skips the waiting.

With several scenarios running at once, add `--scenario <name>` to `task`,
`check`, `hint` and `reset`.

The same things are in the web UI's Scenario tab (`labnet web`).

### A tip that applies to all of them

Nodes restart: when you stop and start the lab, and on WSL whenever Windows
shuts WSL down. Configuration typed as commands is lost; configuration in
files is kept. Several scenarios check that your work is persistent. The
user guide has a
[short reference](user-guide.md#configuring-a-node-so-that-it-survives-a-reboot).

## Writing your own scenario

A scenario is a directory with these files:

```
my-scenario/
    scenario.yaml     title, hints, checks, setup
    topology.yaml     the lab (an ordinary topology file)
    task.md           what the learner should do (Markdown)
    setup/            optional scripts, one per node
        r1.sh
```

Start it by path:

```bash
labnet scenario start ./my-scenario
```

No Python is needed. The built-in scenarios in
[`src/labnet/scenarios/`](../src/labnet/scenarios) are good examples to copy.

### `scenario.yaml`

```yaml
title: NAT to the internet
number: 4                      # position in 'scenario list' (built-ins only)
summary: One line shown in listings.

setup:                         # optional: prepare nodes before the learner starts
  r1: setup/r1.sh
  pc1: setup/pc1.sh

hints:                         # shown one at a time
  - First hint, the gentlest.
  - Second hint, more specific.

checks:
  - name: pc1 has an address in the office network
    node: pc1
    address_in: 192.168.10.0/24

  - name: guests cannot reach the office LAN
    node: guest1
    ping: 192.168.10.11
    expect: fail

  - name: r1 has a source NAT rule
    node: r1
    run: nft list ruleset
    output_matches: 'masquerade|snat'
    explain: Shown under the check when it fails.
```

### Checks

Every check has a `name`, a `node` to run on, and **exactly one** of:

| Key | Passes when |
|-----|-------------|
| `address_in: <network>` | the node has an IPv4 address inside that network. Use a `/32` to require one exact address. |
| `ping: <address or name>` | the node gets a reply (two packets are sent). |
| `run: <shell command>` | the command exits with code 0 (or `exit_code`), and its output satisfies `output_contains` / `output_matches` if given. |

Optional keys:

| Key | Meaning |
|-----|---------|
| `expect: fail` | Invert the check: pass when the ping gets no reply, the address is absent, or the command fails. |
| `exit_code: N` | For `run`: the exit code that counts as success. Default 0. |
| `output_contains: text` | For `run`: this text must appear in the output. |
| `output_matches: regex` | For `run`: this regular expression must match somewhere in the output (it may span lines). Quote it with single quotes in YAML. |
| `wait: seconds` | Keep retrying for up to this long before failing. Use it for anything that takes time: DHCP leases, OSPF adjacencies. |
| `explain: text` | Shown to the learner when the check fails. Say what to look at, without giving the answer away. |

Good checks test **behaviour**, not a particular way of achieving it: "pc1
can resolve this name", not "this file contains this line". The built-in DHCP
and DNS scenarios accept either of two server programs for that reason.

### Setup scripts

A setup script is piped into `sh -e` on its node, as root, right after the
lab has booted. Use it to configure the parts that are not the exercise, or
to plant faults for a troubleshooting scenario. Two rules:

- **Write configuration files**, not just commands, so the starting state
  survives a restart of the lab.
- The script must exit with 0. Note that `sysctl --system` does not, inside a
  container (it is refused some kernel-wide settings); set individual keys
  with `sysctl -w` instead.

### Reference solutions and how they are tested

Each built-in scenario has a solution document in `docs/solutions/`. Code
blocks that are meant to be run are marked with the node they belong to:

````markdown
```sh node=r1
systemctl enable --now nftables
```
````

The integration test for scenarios (`tests/integration/test_scenarios.py`)
does this for every built-in scenario:

1. starts it and confirms that the checks do **not** all pass;
2. runs the marked blocks from the solution document, in order;
3. confirms that every check passes;
4. stops and starts the whole lab, and confirms that every check still passes.

So the solution you read is exactly the solution that was tested, and it is
known to survive a reboot. Blocks without `node=` are explanation and are
not run.
