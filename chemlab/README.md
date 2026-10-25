# chemlab

chemlab is a command line for LLM agents that operate the 203 simulated instruments of ChemLab-Assets. An agent works
in a shell where `chemlab` is the only way to reach the lab. It lists instruments, reads their state, calls their
functions, waits for simulated time to pass and reads their screens, much as a person would use a terminal.

It has two layers on top of the instrument runtimes in `scripts/`:

* **Generic layer.** Every instrument exposes the capabilities and state slots of its own contract (`asset.contract.json`)
  that are visible to an agent. The commands are the same for all 203: `chemlab call mt_xpr204e balance.tare`.
* **Compile layer.** The instruments are grouped into 30 classes (balance, thermal, liquid handling, chromatography,
  spectroscopy, ...), plus a thin class and a shared layer for verbs that most instruments have. Each class has one set of
  verbs in plain units, for example `chemlab run ika_plate_rctdigital set-temp 80C`. A verb is translated into the
  instrument's own calls. Units are converted, ranges and preconditions are checked first, and errors say how to fix them.

The lab runs on the fast simulation runtimes (Python, no physics engine). The default lab has all 203 instruments set up
as at the start of a working day, with an inventory of bottles, standards, consumables and gas cylinders in their storage
places. There are 276 acceptance tasks with automatic checkers.

## Requirements

* **Python 3.10+** with the runtime requirements of this repository (`pip install -r requirements.txt`; NumPy must be below
  2.4, and two runtimes need CoolProp 7.2.0).
* **The dataset files.** chemlab reads the contracts and data in `assets/` and imports the runtimes in `scripts/`. The
  Hugging Face dataset contains everything. A clone of the GitHub repository needs the asset data first:
  `python tools/download_assets.py --data-only` (3D models are not needed).
* **A C compiler** (`cc`, `gcc` or `clang`, or set `CC`). The command-line client (`client/chemlab_client.c`) is compiled
  once, on first use, into `chemlab/build/bin/chemlab`.
* **For the agent sandbox:** Linux on x86_64 with Landlock ABI 3 or newer (Linux 6.2+ with `landlock` in the kernel's LSM
  list; TCP blocking needs Linux 6.7+) and `/usr/bin/bash`. `jq` is useful inside the sandbox. No root, Docker or user
  namespaces are needed.

Without the sandbox, chemlab stops with a message that says what is missing. For local, trusted use you can opt out with
`--no-sandbox`: the shell then runs as you, with no isolation, and a warning is printed. Do not evaluate agents that way.

## Start a lab

Run everything from this `chemlab/` folder:

```bash
cd chemlab
python -m chemlab.gserve --only mt_xpr204e --run 'chemlab list'                 # one command in the sandbox
python -m chemlab.gserve --only ika_plate_rctdigital mt_xpr204e --shell          # interactive sandboxed bash
python -m chemlab.gserve --run 'chemlab list'                                    # all 203 instruments (ready in about 20 s)
python -m chemlab.gserve --only mt_xpr204e --mcp                                 # the generic commands as MCP tools on stdio
python -m chemlab.gserve --only mt_xpr204e                                       # serve; prints CHEMLAB_SOCKET / CHEMLAB_TOKEN
```

| option | meaning |
|---|---|
| `--only ID ...` | only these instruments (default: every instrument of the config) |
| `--config FILE` | lab config (default `configs/all203.json`, the default lab) |
| `--run CMD` / `--shell` / `--mcp` | one sandboxed command, an interactive sandboxed shell, or an MCP server on stdio |
| `--dir DIR` | run directory: socket, `core_log.jsonl`, `captures/`, `work/` (default `build/glab/<name>`) |
| `--no-setup` | start every instrument as its runtime creates it (no default lab, no bring-up) |
| `--step S` | lab clock step in simulated seconds (default 1.0) |
| `--no-sandbox` | no agent sandbox (opt-in, local and trusted use only; prints a warning) |

The agent's shell starts in `work/`, the only folder it can write to. Report files, saved records and screen captures go
there.

## Commands

| command | what it does |
|---|---|
| `chemlab list [--domain D]` | instruments in the lab: id, name, domain; `[panel]` marks a front panel |
| `chemlab list --inventory [ID\|PLACE]` | the lab's containers: what is in them, how much, and where they are stored |
| `chemlab describe INST` | agent-visible capabilities (argument types, units, ranges; `[async]`), state slots, front panel, class verbs |
| `chemlab help INST CAP` | one capability in full, with an example call (`help INST panel` explains the gestures) |
| `chemlab read INST [SLOT ...]` | observations with units and quality (valid / estimated / unavailable) |
| `chemlab call INST CAP [--ARG VALUE ...] [--args JSON] [--save-to FILE]` | call one native capability |
| `chemlab write INST SLOT VALUE` | set a writable state slot |
| `chemlab run INST [VERB ...] [--dry-run]` | class verbs (see below); `chemlab run INST` lists them |
| `chemlab status [OP\|JOB]`, `chemlab cancel OP\|JOB` | asynchronous operations (`op-N`) and class jobs (`job-N`) |
| `chemlab wait TIME`, `chemlab wait OP\|JOB [--max TIME]` | let simulated time pass: `60`, `30s`, `5min`, `1.5h` |
| `chemlab time` | the simulated clock |
| `chemlab panel INST press KEY [--hold S] [--with KEY2]` | front-panel gestures; also `turn KNOB N` (`+3` clockwise, `-3`), `switch SW POS`, `touch DISPLAY X Y [--hold S] [--to X2 Y2]` |
| `chemlab screen INST [DISPLAY] [-o FILE.png]` | capture a display as a PNG in the work dir |
| `chemlab log [--last N] [--full]` | your own past requests and replies |

Every command takes `--json`. `--request-id ID` makes a repeat of the same command return the first reply instead of
running it again; the same id with a different command is refused.

## Class verbs and native capabilities

`chemlab run INST VERB` uses the compile layer. Values take units (`80C`, `50uL`, `300rpm`, `5min`). Ranges, the
instrument's state and its preconditions are checked before anything is sent. `--dry-run` lists the native calls that
would be made. Long actions return a job (`job-N`) that you follow with `chemlab status`, `wait` and `cancel`. Liquid
handlers use named locations (`plate:A1`, `reservoir:A1-A6`). Verbs that delete or reset need `--yes`; without it they
only show what they would do.

`chemlab call INST CAP` uses the generic layer. It calls the contract capability as it is: values are given as the
contract defines them (SI units, e.g. `--temperature_K 353.15`), and nothing is converted or checked beforehand. The
runtime checks every call itself. Use it for anything a class does not cover. `chemlab describe INST` shows both the
class verbs and the native capabilities.

## Exit codes

| code | meaning |
|---|---|
| 0 | success, including a started asynchronous operation or job, and a dry run |
| 2 | usage error: unknown command, instrument, capability, slot or verb, bad value or unit, path outside the work dir. Nothing was sent to the instrument |
| 3 | refused or failed by the instrument (or by a class precondition), with its error code |
| 4 | unexpected: a runtime exception or an internal error |
| 5 | the lab service cannot be reached (printed by the client) |
| 6 | not supported by this instrument twin |

## Errors

Every error says what is wrong, how to fix it and whether the instrument changed:

```
$ chemlab run ika_plate_rctdigital set-temp 900C          # exit code 2
error[usage/OUT_OF_RANGE]: set point 900.0 °C is outside the range of ika_plate_rctdigital (0.0 °C to 310.0 °C).
fix: give a set point from 0.0 °C to 310.0 °C.
instrument changed: no
request: r-0003
```

From the generic layer, a refusal also prints the contract's explanation of the error code (`contract:`), the runtime's
own message when it differs, and the side effects. `--json` gives the same content as
`{"ok": false, "error": {"class", "sila_class", "code", "message", "fix", ...}}`. The error classes follow SiLA 2:
ValidationError (exit 2), DefinedExecutionError (exit 3 and 6) and UndefinedExecutionError (exit 4). A composite verb that
fails partway lists the steps it had already done.

## Agent view and evaluator view

Each caller gets its own agent identity in every runtime. It sees only what the contract marks as visible to agents:
hidden capabilities answer exactly like missing ones, and evaluator-only state (true values, injected faults) never
appears in any output. Host file paths are replaced by `[host path]`. The evaluator view (`lab.backend.evaluator_read(...)`)
exists only in the host process, for task checkers. The sandboxed shell cannot reach it.

## Lab config, default lab and inventory

A lab config is a JSON file that lists the instruments and how each one is set up. `configs/all203.json` is the
**default lab**: all 203 instruments as a well-run lab has them at the start of a day, with utilities connected, standard
accessories installed, consumables stocked, and check standards and reference materials in place. The lab is ready at
simulated t = 1800 s, the longest warm-up. Set-up steps that only the lab may do run as the evaluator or as a lab-operator
identity, never as the agent. Some steps are tagged with a group (utility, accessory, consumable, standard or state), and
a task world can switch groups off.

`configs/inventory_default.json` lists the lab's containers, one record each: substance ids from
`assets/reactions/substances.json`, amounts in physical units, storage places (cabinets, fridge, freezer, gas cylinder rack,
bench shelf) and the labware types of `assets/labware/catalog.json`. Agents see it with `chemlab list --inventory`. Moving
a container is a robot or human action, not a chemlab command. `configs/all203_quantos_powder.json` with
`inventory_task_quantos_powder.json` is a task-world variant (powder dosing with an empty Quantos).

How each runtime host is built and clocked comes from `configs/host_recipes.json`. `python -m chemlab.make_config`
regenerates the configs.

## The clock

The lab has one simulated clock, and `chemlab wait` is the only way it moves. Time passes in steps (1 s by default) for all
instruments together, so time spent in thought or in shell commands does not count. An instrument catches up on its
steps the next time it is read or called. The result is the same as stepping it at once, but idle instruments cost
nothing. Verbs that say they wait (`weigh --stable`, `measure --wait`, `set-temp --wait`) move the clock themselves and
report how long they waited.

## Tasks

The 276 acceptance tasks (5 to 14 per class) each come with a task world, an instruction, a reference solution and an
automatic checker. The checker reads the evaluator view, the request log and the report files the instruction asks for.

```bash
python -m chemlab.tasks list [--class balance]                 # id, class, kind, instruments
python -m chemlab.tasks show b01_tare_beaker                   # the instruction the agent gets
python -m chemlab.tasks run b01_tare_beaker --solution my.sh   # run a bash solution in the task's sandbox, then check it
python -m chemlab.tasks run b01_tare_beaker --gold             # the reference solution
python -m chemlab.tasks shell b01_tare_beaker                  # work in the task world by hand; checked when the shell exits
```

To put your agent on a task, give it the instruction, let it produce `chemlab` commands, and run them as the solution (or
drive `shell`). Results go to `build/tasks/<id>/<time>/`: `transcript.json`, `core_log.jsonl`, `check.json` and the work
dir. The exit code is 0 when the checker passes and 1 when it fails.

## Adding a class

See [COMPILE_GUIDE.md](COMPILE_GUIDE.md). A class is a module in `chemlab/compile/classes/`, a mapping fragment in
`configs/adapters/<class>.json`, a lowering table and acceptance tasks in `chemlab/compile/tasks/`, and a two-line test.

## Tests

Run each test module on its own, from the folder it lives in:

```bash
cd chemlab/tests_generic && python -m unittest test_generic_core test_generic_sandbox test_mcp_shell test_host_recipes   # under a minute
cd chemlab/tests_generic && python -m unittest test_default_world                     # all 203 from the default lab, minutes
cd chemlab/tests_compile && python -m unittest test_units test_framework test_framework_rules test_shared
cd chemlab/tests_compile && python -m unittest test_class_balance                      # one class: mappings, lowering, acceptance
cd chemlab && python -m unittest discover -s tests                                      # the Opentrons Flex pilot gate and sandbox
cd chemlab/tests_generic && python -m unittest test_all203                             # every capability of all 203, slow
```

Sandbox tests skip when the sandbox cannot run on the machine, and the check against the Matterix simulator bridge skips
when that checkout is not present. Do not run two of the all-203 sweeps (`test_all203`, `test_generic_parity`) at the
same time.

## Limits

* **Simulation, not hardware.** The runtimes are fast models of the instruments built from published specifications.
  Readings, timings and failure modes follow those models, not a real device, and chemlab has no hardware back end.
* **Sandbox.** The sandbox uses Landlock and seccomp, not a container. Landlock does not restrict `connect()` on UNIX
  sockets, so other same-user UNIX sockets stay reachable in principle. A hand-written shared library loaded with
  `LD_PRELOAD` into an allowed tool would still be confined, but could try those sockets. The shell shares the user id and
  the PID namespace with the host. For hard isolation, run chemlab inside a container or a separate user account. The
  seccomp filter is written for x86_64.
* **Compile layer coverage.** Class verbs cover each class's common workflow. Instrument-specific functions stay on
  `chemlab call`. Five one-of-a-kind instruments (thin class) have only the shared verbs. Some world changes (loading a
  sample into an instrument whose loading is not an agent action, swapping cells, placing loads on a balance pan) are done
  by the lab or a robot, not by chemlab commands.
* **Inventory.** The inventory is the state at the start of the day. It does not follow containers that are moved during
  a session.
* **Text.** The English help is generated from the contract structure. Contract descriptions written in Chinese are shown
  as they are, marked `(zh)`.
* **MCP.** The MCP server exposes the generic commands (list, describe, help, read, call, write, status, cancel, wait,
  panel, screen, log). Class verbs are not MCP tools yet; use the command line for `chemlab run`.
