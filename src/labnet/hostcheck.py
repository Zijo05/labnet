"""``labnet install-check``: is this machine ready to run labs?

Each check returns a ``CheckResult`` with a status and, when something is
wrong, the exact command or step that fixes it. The checks only read; they
never change the system.
"""

from __future__ import annotations

import gzip
import importlib.util
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path

from labnet import images, incus
from labnet.errors import LabnetError
from labnet.topology import ROLES

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"


@dataclass
class CheckResult:
    """Outcome of one prerequisite check."""

    status: str  # ok / warn / fail / info
    title: str
    fix: str = ""  # what to do about it; empty when nothing is needed


def run_checks() -> list[CheckResult]:
    """Run every check in a sensible order and return the results."""
    results = [_check_os(), *_check_wsl()]
    incus_result = _check_incus_installed()
    results.append(incus_result)
    if incus_result.status == OK:
        daemon = _check_incus_daemon()
        results.append(daemon)
        if daemon.status != FAIL:
            results.append(_check_storage())
            results.append(_check_images())
    results.append(_check_memory())
    results.append(_check_netem())
    results.extend(_check_bridge_firewall())
    results.extend(_check_optional_tools())
    return results


def _os_release() -> dict[str, str]:
    """Parse /etc/os-release into a dictionary."""
    values: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                values[key] = value.strip().strip('"')
    except OSError:
        pass
    return values


def _is_wsl() -> bool:
    return "microsoft" in platform.release().lower()


def _check_os() -> CheckResult:
    release = _os_release()
    pretty = release.get("PRETTY_NAME", platform.system())
    distro, version = release.get("ID", ""), release.get("VERSION_ID", "")
    where = " on WSL2" if _is_wsl() else ""
    tested = (distro == "debian" and version == "13") or (
        distro == "ubuntu" and version in ("24.04", "26.04")
    )
    if platform.system() != "Linux":
        return CheckResult(
            FAIL,
            f"labnet needs Linux, this is {platform.system()}",
            "On Windows, install WSL2 and run labnet inside it; see docs/install.md.",
        )
    if tested or distro == "debian" and version == "12":
        return CheckResult(OK, f"Operating system: {pretty}{where}")
    return CheckResult(
        WARN,
        f"Operating system: {pretty}{where} (not a system labnet was tested on)",
        "It will probably work if Incus 6.0 or newer is available. "
        "Tested hosts are listed in docs/install.md.",
    )


def _check_wsl() -> list[CheckResult]:
    if not _is_wsl():
        return []
    try:
        init = Path("/proc/1/comm").read_text(encoding="utf-8").strip()
    except OSError:
        init = "unknown"
    if init == "systemd":
        return [CheckResult(OK, "WSL: systemd is running (Incus needs it)")]
    return [
        CheckResult(
            FAIL,
            "WSL: systemd is not enabled, so the Incus service cannot run",
            "Add these two lines to /etc/wsl.conf:  [boot]  and  systemd=true  "
            "then run 'wsl --shutdown' in Windows PowerShell and open the distro again.",
        )
    ]


def _check_incus_installed() -> CheckResult:
    if shutil.which("incus"):
        return CheckResult(OK, "Incus is installed")
    distro = _os_release().get("ID", "")
    version = _os_release().get("VERSION_ID", "")
    if distro == "debian" and version == "12":
        fix = (
            "Debian 12 has Incus in backports: enable bookworm-backports, then "
            "'sudo apt install -t bookworm-backports incus'."
        )
    else:
        fix = "Install it with: sudo apt update && sudo apt install -y incus"
    return CheckResult(FAIL, "Incus is not installed", fix)


def _check_incus_daemon() -> CheckResult:
    try:
        info = incus.server_info()
    except LabnetError as exc:
        return CheckResult(
            FAIL,
            f"Cannot talk to the Incus daemon: {exc.message}",
            exc.hint
            or "Run 'sudo usermod -aG incus-admin $USER', log out and in again, "
            "and check 'systemctl status incus'.",
        )
    version = info.get("environment", {}).get("server_version", "?")
    try:
        major = int(version.split(".")[0])
    except ValueError:
        major = 0
    if major < 6:
        return CheckResult(
            WARN,
            f"Incus {version} is older than the tested 6.0",
            "Upgrade Incus to 6.0 or newer if you run into problems.",
        )
    return CheckResult(OK, f"Incus daemon is reachable (version {version})")


def _check_storage() -> CheckResult:
    try:
        pool = incus.default_storage_pool()
    except LabnetError:
        return CheckResult(
            FAIL,
            "Incus has no storage pool (it has not been initialised)",
            "Run once: sudo incus admin init --minimal",
        )
    return CheckResult(OK, f"Incus storage pool '{pool}' is available")


def _check_images() -> CheckResult:
    all_images = incus.list_images()
    built = [role for role in ROLES if images.image_status(role, all_images) == "ok"]
    if len(built) == len(ROLES):
        return CheckResult(OK, "Role images are built (pc, router, server)")
    missing = [role for role in ROLES if role not in built]
    return CheckResult(
        INFO,
        f"Role images not built yet: {', '.join(missing)}",
        "Nothing to do: 'labnet up' builds them when first needed (a few minutes, "
        "needs internet). To build them now: labnet images build",
    )


def _check_memory() -> CheckResult:
    total_mib = 0
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                total_mib = int(line.split()[1]) // 1024
    except (OSError, ValueError):
        return CheckResult(INFO, "Could not read the amount of memory")
    if total_mib >= 3500:
        return CheckResult(OK, f"Memory: {total_mib} MiB available to Linux")
    fix = "Small labs still work. Close other programs or use fewer nodes."
    if _is_wsl():
        fix = (
            "Raise the WSL limit: in Windows, edit %UserProfile%\\.wslconfig, set "
            "'memory=6GB' under [wsl2], then run 'wsl --shutdown'. See docs/install.md."
        )
    return CheckResult(WARN, f"Memory: only {total_mib} MiB available to Linux", fix)


def _kernel_option(name: str) -> str | None:
    """Return 'y', 'm' or None for a kernel build option, if the config is readable."""
    try:
        with gzip.open("/proc/config.gz", "rt", encoding="utf-8") as config:
            for line in config:
                if line.startswith(f"{name}="):
                    return line.strip().split("=", 1)[1]
    except OSError:
        return "unknown"
    return None


def _check_netem() -> CheckResult:
    option = _kernel_option("CONFIG_NET_SCH_NETEM")
    if option in ("y", "m") or Path("/sys/module/sch_netem").exists():
        return CheckResult(OK, "Kernel supports link delay and loss (netem)")
    if option == "unknown":
        return CheckResult(INFO, "Could not check kernel support for link delay/loss (netem)")
    return CheckResult(
        WARN,
        "Kernel lacks netem: 'labnet link set --delay/--loss' will not work",
        "Everything else works. A kernel with CONFIG_NET_SCH_NETEM is needed for link shaping.",
    )


def _check_bridge_firewall() -> list[CheckResult]:
    """Warn about the one setup known to break traffic between nodes on a switch."""
    try:
        value = Path("/proc/sys/net/bridge/bridge-nf-call-iptables").read_text().strip()
    except OSError:
        return []
    # Look for the Docker daemon's socket, not the 'docker' command: on WSL the
    # command is often just Docker Desktop's Windows client on the PATH.
    docker_here = Path("/var/run/docker.sock").exists()
    if value == "1" and docker_here:
        return [
            CheckResult(
                WARN,
                "Docker is present and bridged traffic is filtered by the host firewall",
                "If nodes on the same switch cannot ping each other, Docker's firewall "
                "rules are dropping the frames. See 'Nodes on one switch cannot reach "
                "each other' in docs/troubleshooting.md.",
            )
        ]
    return []


def _check_optional_tools() -> list[CheckResult]:
    results = []
    if importlib.util.find_spec("fastapi") and importlib.util.find_spec("uvicorn"):
        results.append(CheckResult(OK, "Web UI dependencies are installed"))
    else:
        results.append(
            CheckResult(
                INFO,
                "Web UI dependencies are not installed ('labnet web' will not start)",
                "Install them with: pipx install --force 'labnet[web]'  "
                "(or pip install 'labnet[web]' in your virtual environment)",
            )
        )
    if shutil.which("ansible"):
        results.append(CheckResult(OK, "Ansible is installed (for 'labnet export ansible')"))
    else:
        results.append(
            CheckResult(
                INFO,
                "Ansible is not installed (only needed to use exported inventories)",
                "Install it with: sudo apt install -y ansible",
            )
        )
    return results
