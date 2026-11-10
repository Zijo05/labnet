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
