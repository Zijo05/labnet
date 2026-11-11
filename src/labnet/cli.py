"""The ``labnet`` command line.

This file only parses arguments, calls the engine and prints results. All the
real work lives in ``labnet.engine`` and friends, which the web UI shares.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from labnet import __version__, ansible_export, engine, hostcheck, images, scenario
from labnet.errors import LabnetError
from labnet.topology import ROLES, load_topology

# --- small output helpers --------------------------------------------------


def _say(message: str = "") -> None:
    """Print a progress or result line, flushing so output appears live."""
    print(message, flush=True)


def _colour(text: str, code: str) -> str:
    """Wrap ``text`` in an ANSI colour, but only when writing to a terminal."""
    if sys.stdout.isatty():
        return f"\033[{code}m{text}\033[0m"
    return text


def _table(headers: list[str], rows: list[list[str]], indent: str = "  ") -> str:
    """Format rows as a plain text table with aligned columns."""
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = [indent + "  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)).rstrip()]
    for row in rows:
        lines.append(
            indent + "  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip()
        )
    return "\n".join(lines)


def _confirm(question: str, assume_yes: bool) -> bool:
    """Ask a yes/no question. ``--yes`` answers it without asking."""
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        raise LabnetError(
            "This command needs confirmation, but there is no terminal to ask on.",
            hint="Add --yes to confirm without being asked.",
        )
    answer = input(f"{question} [y/N] ").strip().lower()
    return answer in ("y", "yes")


# --- commands --------------------------------------------------------------


def cmd_install_check(_args: argparse.Namespace) -> int:
    labels = {
        hostcheck.OK: _colour("[ ok ]", "32"),
        hostcheck.WARN: _colour("[warn]", "33"),
        hostcheck.FAIL: _colour("[FAIL]", "31"),
        hostcheck.INFO: _colour("[info]", "36"),
    }
    results = hostcheck.run_checks()
    for result in results:
        _say(f"{labels[result.status]} {result.title}")
        if result.fix:
            _say(f"       -> {result.fix}")
    failed = sum(1 for result in results if result.status == hostcheck.FAIL)
    _say()
    if failed:
        _say(f"{failed} problem(s) must be fixed before labnet can build labs.")
        return 1
    _say("This machine is ready. Try: labnet up examples/office.yaml")
    return 0


def cmd_up(args: argparse.Namespace) -> int:
    topology = load_topology(args.file)
    _say(f"Building lab '{topology.name}' from {topology.source}")
    lab = engine.up(topology, report=_say, timeout=args.timeout)
    _say(f"Lab '{lab.name}' is ready: {len(lab.nodes)} node(s), {len(lab.networks)} network(s).")
    _say()
    _print_lab(lab)
    _say()
    first = lab.nodes[0].node if lab.nodes else "<node>"
    _say(f"Open a console with:  labnet shell {first}")
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    lab = engine.get_lab(args.lab)
    question = (
        f"Delete lab '{lab.name}' with {len(lab.nodes)} node(s) and "
        f"{len(lab.networks)} network(s)? Everything configured on them is lost."
    )
    if not _confirm(question, args.yes):
        _say("Nothing was deleted.")
        return 0
    engine.down(lab.name, report=_say)
    _say(f"Lab '{lab.name}' was removed.")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    labs = engine.read_labs()
    if args.lab:
        labs = {args.lab: engine.get_lab(args.lab)}
    if not labs:
        _say("No labs exist. Create one with: labnet up examples/office.yaml")
        return 0
    for index, lab in enumerate(labs.values()):
        if index:
            _say()
        for node in lab.nodes:
            engine.read_shaping(node)
        _print_lab(lab)
    return 0


def _print_lab(lab: engine.LabState) -> None:
    """Print one lab as a table of nodes and interfaces, then its networks."""
    running = sum(1 for node in lab.nodes if node.running)
    title = f"Lab {lab.name}: {running} of {len(lab.nodes)} node(s) running"
    if lab.scenario:
        title += f"  (scenario: {lab.scenario})"
    _say(_colour(title, "1"))

    rows: list[list[str]] = []
    for node in lab.nodes:
        state = "running" if node.running else "stopped"
        for index, interface in enumerate(node.interfaces):
            link = "-"
            if node.running:
                link = "up" if interface.up else "DOWN"
                if interface.shaping:
                    link += f" ({interface.shaping})"
            first = index == 0
            rows.append(
                [
                    node.node if first else "",
                    node.role if first else "",
                    state if first else "",
                    interface.name,
                    interface.network,
                    link,
                    ", ".join(interface.addresses) or "-",
                ]
            )
    headers = ["NODE", "ROLE", "STATE", "IFACE", "NETWORK", "LINK", "IPv4 ADDRESS"]
    _say(_table(headers, rows))

    described = []
    for network in lab.networks:
        if network.uplink:
            described.append(
                f"{network.network} (uplink to the internet, gateway {network.gateway})"
            )
        else:
            described.append(f"{network.network} (switch)")
    _say("  Networks: " + "; ".join(described))


def cmd_shell(args: argparse.Namespace) -> int:
    node = engine.resolve_node(args.node)
    engine.require_running(node)
    return subprocess.call(engine.shell_argv(node))


def cmd_exec(args: argparse.Namespace) -> int:
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise LabnetError(
            "No command given.", hint="Example: labnet exec pc01 -- ip -brief address"
        )
    node = engine.resolve_node(args.node)
    engine.require_running(node)
    return subprocess.call(engine.exec_argv(node, command))


def cmd_start(args: argparse.Namespace) -> int:
    started = engine.start(args.lab, args.node, report=_say)
    _say(f"Started: {', '.join(started)}." if started else "Already running; nothing to do.")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    stopped = engine.stop(args.lab, args.node, report=_say)
    _say(f"Stopped: {', '.join(stopped)}." if stopped else "Already stopped; nothing to do.")
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    nodes = engine.snapshot(args.lab, args.name, report=_say)
    _say(f"Snapshot '{args.name}' saved for {len(nodes)} node(s) of lab '{args.lab}'.")
    _say(f"Go back to it any time with: labnet restore {args.lab} {args.name}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    question = (
        f"Restore lab '{args.lab}' to snapshot '{args.name}'? "
        "Changes made since the snapshot are lost and running nodes restart."
    )
    if not _confirm(question, args.yes):
        _say("Nothing was changed.")
        return 0
    nodes = engine.restore(args.lab, args.name, report=_say)
    _say(f"Lab '{args.lab}' is back at snapshot '{args.name}' ({len(nodes)} node(s)).")
    return 0


def cmd_link(args: argparse.Namespace) -> int:
    node = engine.resolve_node(args.node)
    if args.action in ("down", "up"):
        interface = engine.set_link_state(node, args.network, up=args.action == "up")
        verb = "plugged back in" if interface.up else "unplugged"
        _say(f"{node.node} {interface.name} ({args.network}): cable {verb}.")
        return 0
    if args.action == "clear":
        interface = engine.shape_link(node, args.network)
    else:
        if args.delay is None and args.loss is None:
            raise LabnetError(
                "Give --delay and/or --loss.",
                hint="Example: labnet link set r1 wan --delay 50ms --loss 2",
            )
        interface = engine.shape_link(node, args.network, delay=args.delay, loss=args.loss)
    what = interface.shaping or "no delay or loss"
    _say(f"{node.node} {interface.name} ({args.network}): {what} on packets it sends.")
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    node = engine.resolve_node(args.node)
    packet_filter = " ".join(args.filter) if args.filter else None
    if args.file:
        argv = engine.capture_argv(node, args.network, args.count, packet_filter, raw=True)
        _say(f"Capturing on {node.node} ({args.network}) into {args.file}. Stop with Ctrl+C.")
        with open(args.file, "wb") as output:
            code = _run_until_interrupt(argv, stdout=output)
        _say(f"Saved {args.file}. Open it with Wireshark or 'tcpdump -r {args.file}'.")
        return code
    argv = engine.capture_argv(node, args.network, args.count, packet_filter)
    return _run_until_interrupt(argv)


def _run_until_interrupt(argv: list[str], stdout: object = None) -> int:
    """Run a command until it ends or the user presses Ctrl+C (which is not an error)."""
    process = subprocess.Popen(argv, stdout=stdout)  # noqa: S603 - argv built by the engine
    try:
        return process.wait()
    except KeyboardInterrupt:
        # Ctrl+C also reached the child, which is already shutting down and
        # flushing its output. Give it a moment before insisting.
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait()
        return 0


def cmd_scenario(args: argparse.Namespace) -> int:
    handlers = {
        "list": _scenario_list,
        "start": _scenario_start,
        "task": _scenario_task,
        "check": _scenario_check,
        "hint": _scenario_hint,
        "reset": _scenario_reset,
    }
    return handlers[args.action](args)


def _scenario_list(_args: argparse.Namespace) -> int:
    running = set(scenario.active_scenarios().values())
    rows = [
        [str(item.number), item.name, item.title, "running" if item.reference in running else ""]
        for item in scenario.list_scenarios()
    ]
    _say(_table(["#", "NAME", "TITLE", "STATUS"], rows, indent=""))
    _say()
    _say("Start one with: labnet scenario start <name>")
    return 0


def _scenario_footer() -> None:
    _say("When you think you are done:  labnet scenario check")
    _say("If you are stuck:             labnet scenario hint")
    _say("To start over:                labnet scenario reset")


def _scenario_start(args: argparse.Namespace) -> int:
    _say(f"Starting scenario '{args.name}'")
    item = scenario.start(args.name, report=_say)
    _say(f"Scenario '{item.name}' is ready in lab '{item.lab}'.")
    _say()
    _say(item.task.rstrip())
    _say()
    _scenario_footer()
    return 0


def _scenario_task(args: argparse.Namespace) -> int:
    item = scenario.find_active(args.scenario)
    _say(item.task.rstrip())
    _say()
    _scenario_footer()
    return 0


def _scenario_check(args: argparse.Namespace) -> int:
    item = scenario.find_active(args.scenario)
    _say(f"Checking scenario '{item.name}' ({len(item.checks)} checks)")

    def show(result: scenario.CheckResult) -> None:
        label = _colour("PASS", "32") if result.passed else _colour("FAIL", "31")
        _say(f"  {label}  {result.check.name}")
        if not result.passed:
            _say(f"        Found: {result.detail}")
            if result.check.explain:
                _say(f"        {result.check.explain}")

    results = scenario.run_checks(item, patient=not args.no_wait, report=show)
    passed = sum(1 for result in results if result.passed)
    _say()
    if passed == len(results):
        _say(f"All {passed} checks passed. Scenario '{item.name}' is solved.")
        _say(f"Remove the lab when you are finished: labnet down {item.lab}")
        return 0
    _say(f"{passed} of {len(results)} checks passed. Keep going, or try: labnet scenario hint")
    return 1


def _scenario_hint(args: argparse.Namespace) -> int:
    item = scenario.find_active(args.scenario)
    if not item.hints:
        _say(f"Scenario '{item.name}' has no hints.")
        return 0
    if not 1 <= args.number <= len(item.hints):
        raise LabnetError(
            f"Scenario '{item.name}' has hints 1 to {len(item.hints)}.",
            hint="Run 'labnet scenario hint' for the first one.",
        )
    _say(f"Hint {args.number} of {len(item.hints)}: {item.hints[args.number - 1]}")
    if args.number < len(item.hints):
        _say(f"Next hint: labnet scenario hint {args.number + 1}")
    return 0


def _scenario_reset(args: argparse.Namespace) -> int:
    item = scenario.find_active(args.scenario)
    question = f"Reset scenario '{item.name}'? Everything you configured in it is lost."
    if not _confirm(question, args.yes):
        _say("Nothing was changed.")
        return 0
    scenario.reset(args.scenario, report=_say)
    _say(f"Scenario '{item.name}' is back at its starting point.")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    lab = engine.get_lab(args.lab)
    text = ansible_export.inventory_text(lab)
    if args.output == "-":
        print(text, end="")
        return 0
    output = args.output or f"inventory-{lab.name}.ini"
    with open(output, "w", encoding="utf-8") as file:
        file.write(text)
    _say(f"Wrote {output} ({len(lab.nodes)} node(s)).")
    _say(f"Try it:  ansible -i {output} all -m ping")
    return 0


def cmd_images(args: argparse.Namespace) -> int:
    if args.action == "list":
        all_images = images.incus.list_images()
        rows = [[role, images.image_status(role, all_images)] for role in ROLES]
        _say(_table(["ROLE", "IMAGE"], rows, indent=""))
        return 0
    if args.action == "remove":
        if not _confirm("Delete the labnet role images? Existing labs keep working.", args.yes):
            return 0
        removed = images.remove_role_images()
        _say(f"Removed: {', '.join(removed) or 'nothing'}.")
        return 0
    all_images = images.incus.list_images()
    for role in args.roles or ROLES:
        if not args.rebuild and images.image_status(role, all_images) == "ok":
            _say(f"The '{role}' image is up to date (use --rebuild to build it again).")
            continue
        _say(f"Building the '{role}' image from {args.base}")
        images.build_role_image(role, base=args.base, report=_say)
    return 0


# --- argument parsing ------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Define every command and its arguments."""
    parser = argparse.ArgumentParser(
        prog="labnet",
        description="Build and operate simulated computer networks from Incus containers.",
        epilog="Nodes are named 'node' or, when several labs share a name, 'lab/node'.",
    )
    parser.add_argument("--version", action="version", version=f"labnet {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    def add(name: str, handler: object, help_text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text, description=help_text)
        sub.set_defaults(handler=handler)
        return sub

    def add_yes(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")

    add("install-check", cmd_install_check, "check that this machine can run labs")

    sub = add("up", cmd_up, "build a lab from a topology file and wait until it is ready")
    sub.add_argument("file", help="topology file, for example examples/office.yaml")
    sub.add_argument(
        "--timeout",
        type=float,
        default=engine.READY_TIMEOUT,
        help="seconds to wait for nodes to boot (default: %(default)s)",
    )

    sub = add("down", cmd_down, "delete a lab and everything in it")
    sub.add_argument("lab")
    add_yes(sub)

    sub = add("list", cmd_list, "show labs, nodes, interfaces and addresses")
    sub.add_argument("lab", nargs="?", help="show only this lab")

    sub = add("shell", cmd_shell, "open a root console on a node (works without lab network)")
    sub.add_argument("node")

    sub = add("exec", cmd_exec, "run one command on a node: labnet exec <node> -- <command>")
    sub.add_argument("node")
    sub.add_argument("command", nargs=argparse.REMAINDER, help="-- followed by the command")

    sub = add("start", cmd_start, "start a stopped lab, or one node of it")
    sub.add_argument("lab")
    sub.add_argument("node", nargs="?")

    sub = add("stop", cmd_stop, "stop a lab, or one node of it, keeping everything on disk")
    sub.add_argument("lab")
    sub.add_argument("node", nargs="?")

    sub = add("snapshot", cmd_snapshot, "save the current state of every node in a lab")
    sub.add_argument("lab")
    sub.add_argument("name", nargs="?", default=engine.DEFAULT_SNAPSHOT)

    sub = add("restore", cmd_restore, "return every node in a lab to a snapshot")
    sub.add_argument("lab")
    sub.add_argument("name", nargs="?", default=engine.DEFAULT_SNAPSHOT)
    add_yes(sub)

    sub = add("link", cmd_link, "unplug, re-plug or degrade the cable between a node and a network")
    sub.add_argument("action", choices=["down", "up", "set", "clear"])
    sub.add_argument("node")
    sub.add_argument("network")
    sub.add_argument("--delay", help="with 'set': delay added to sent packets, e.g. 50ms")
    sub.add_argument("--loss", type=float, help="with 'set': percent of sent packets to drop")

    sub = add("capture", cmd_capture, "watch the packets on a node's link (tcpdump)")
    sub.add_argument("node")
    sub.add_argument("network")
    sub.add_argument("-w", "--file", help="write a pcap file instead of printing packets")
    sub.add_argument("-c", "--count", type=int, help="stop after this many packets")
    sub.add_argument("filter", nargs="*", help="tcpdump filter, e.g. icmp or 'port 53'")

    sub = add("scenario", cmd_scenario, "guided exercises with automatic checking")
    actions = sub.add_subparsers(dest="action", metavar="<action>", required=True)
    actions.add_parser("list", help="show the available scenarios")
    action = actions.add_parser("start", help="build a scenario's lab and show the task")
    action.add_argument("name", help="scenario name, or the path of a scenario directory")
    for name, help_text in (
        ("task", "show the task again"),
        ("check", "test whether the task is solved"),
        ("hint", "show a hint"),
        ("reset", "rebuild the scenario's lab from scratch"),
    ):
        action = actions.add_parser(name, help=help_text)
        action.add_argument("--scenario", help="which scenario, if several are running")
        if name == "check":
            action.add_argument(
                "--no-wait",
                action="store_true",
                help="do not retry checks that normally wait (for leases, routing, ...)",
            )
        if name == "hint":
            action.add_argument("number", nargs="?", type=int, default=1, help="hint number")
        if name == "reset":
            add_yes(action)

    sub = add("export", cmd_export, "write an Ansible inventory for a lab")
    sub.add_argument("format", choices=["ansible"])
    sub.add_argument("lab")
    sub.add_argument(
        "-o", "--output", help="file to write (default: inventory-<lab>.ini; '-' prints it)"
    )

    sub = add("images", cmd_images, "build, list or remove the per-role node images")
    sub.add_argument("action", choices=["build", "list", "remove"])
    sub.add_argument("roles", nargs="*", help="roles to build: pc, router, server (default: all)")
    sub.add_argument("--base", default=images.DEFAULT_BASE_IMAGE, help="base image to build from")
    sub.add_argument("--rebuild", action="store_true", help="build even if the image exists")
    add_yes(sub)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse arguments, run the command, print errors plainly."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args) or 0
    except LabnetError as exc:
        print(_colour("Error:", "31"), exc.message, file=sys.stderr)
        if exc.hint:
            print(f"  What to do: {exc.hint}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
