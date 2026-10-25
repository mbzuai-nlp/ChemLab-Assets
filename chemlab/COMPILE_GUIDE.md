# chemlab compile layer: adding an instrument class

The compile layer gives every instrument class one set of plain commands — `set-temp 80C`, `tare`, `transfer 50uL --from
plate:A1 --to plate:B1` — and lowers each command to that instrument's own contract capabilities. It sits between the generic
service (`chemlab/generic.py`, GenericCore) and the lab backend, plugged into the adapter hook. It has 30 classes (for example
`balance`, `thermal` for temperature control and `liquid` for pipetting), a thin class and a shared layer that together cover
all 203 instruments. This guide is what you need to add or extend a class: the rules, the files, the steps and the tests.

```
agent (sandbox) ── chemlab run INST VERB ... ──> GenericCore ──> adapter of INST (compile layer) ──> LabBackend ──> runtime
                                                   │  generic commands unchanged:       │ same agent principal,
                                                   │  list/describe/read/call/wait/...   │ the runtime checks again
```

An adapter only ever reaches the instrument through `core.backend` with the caller's own agent principal, exactly like
`chemlab call`. Visibility rules, the output filter and the runtime's own checks stay in force. `chemlab call` stays available
for everything a class does not cover.

## 1. Files

| part | file | notes |
|---|---|---|
| framework | `chemlab/compile/{units,errors,verbs,ctx,jobs,base,fragments,shared,deck,lowering,acceptance,mapgen}.py` | shared by every class; change with care and run the full test set |
| a class | `chemlab/compile/classes/<class>.py` — verb table, handlers, class checks, summary | one module per class |
| its mapping | `configs/adapters/<class>.json` — one fragment per class, members and per-instrument data | one fragment per class |
| host-side world | `chemlab/compile/tasks/<class>_world.py` (lab-config set-up steps), `chemlab/compile/scenes/<x>.py` (only if the runtime needs a trusted scene, like the Flex) | per class |
| lowering table | `chemlab/compile/tasks/<class>_lowering.py` | per class |
| acceptance tasks | `chemlab/compile/tasks/<class>_tasks.py` | per class |
| tests | `tests_compile/test_class_<class>.py` (two lines, see §8) | per class |
| shared hooks | `chemlab/{generic,labbackend,adapters}.py` | the generic layer; a class never needs to change them |

Suites and lowering tables are found by name (`tasks/<class>_tasks.py`, `tasks/<class>_lowering.py`); fragments by directory.
There is no shared registry to edit, so two classes developed at the same time never touch the same file. Do not edit `configs/all203.json`.

## 2. Steps to add a class

1. **Read** each member's agent-visible capabilities and slots (`chemlab describe INST --json`), its contract, and the
   manufacturer's documentation for the native vocabulary. Cite page references only; never copy manual text into code,
   fragments or help.
2. **Probe** each member in a small lab before writing code (what the default lab state is, what a call needs, how fast things
   change): `GenericLab(dir, single([...]))` and `lab.core.handle(['chemlab', 'call', ...], 'agent')`; use `class_adapters: False`
   in the config to see the plain generic layer. Note what needs host world (filled bath, sample, fixture) — the default lab
   does not provide it.
3. **Draft the mapping**: `python -m chemlab.compile.mapgen <class>` drafts a fragment from rules in `mapgen.RULES` (add a rule set
   for a new class there), or write the fragment by hand. `python -m chemlab.compile.mapgen <class> --compare` shows how much the
   rules find for an existing fragment.
4. **Write the class module** (§3) and the **fragment** (§4). Keep instrument facts in the fragment, class logic in the module.
5. **Lowering table** (§7): per member, verbs in order with the native calls each must make, written from the contracts.
   `python -m chemlab.compile.lowering --class <class>` must PASS (calls equal, twin state equal after every step, one path).
6. **Acceptance tasks** (§7): 8–10 for classes with 5 or more members (4–6 for small classes), at least two recovery tasks,
   gold with `chemlab run` passes in the sandbox, NoAct fails: `python -m chemlab.compile.acceptance --class <class>`.
7. **Tests**: `tests_compile/test_class_<class>.py`; then the full set (§8), including the generic parity for your members.
8. **Document** in the fragment: membership decisions with reasons (who is in, who is out and why, primary / secondary) and
   verbs not supported per member with reasons. Fix runtime or contract problems in the runtime or contract, not in the class.

## 3. The class module

```python
from ..base import ClassAdapter, finite
from ..errors import invalid, refused
from ..verbs import Arg, Opt, Verb, save_to_opt

class CentrifugeAdapter(ClassAdapter):
    CLASS = 'agitation'                  # = the fragment's "class"
    TITLE = 'spin, shake, mix'
    ABOUT = 'one paragraph for describe'
    STANDARDS = 'SiLA 2 ..., PyLabRobot ..., ASTM / pharmacopoeia terms'
    ALWAYS = ('status',)                 # verbs every member has without a mapping entry
    UNIT_KINDS = ('rotation', 'duration')
    PATH_GROUPS = (('start', 'stop', 'result'),)   # one native path per group (§5.2)
    CODES = {'LID_OPEN': ('LID_OPEN', 'the lid of {inst} is open.', 'close it: `chemlab run {inst} close lid`.')}

    def build_verbs(self):
        return {'spin': Verb('spin', 'spin at a speed for a time', self.h_spin,
                             positionals=[Arg('SPEED', 'e.g. 4000rpm', kind='rotation')],
                             options={'time': Opt('run time', kind='duration', metavar='TIME', default='5min')},
                             preconditions=['lid closed', 'speed within the rotor range'],
                             errors=['OUT_OF_RANGE (usage)', 'LID_OPEN (refused)'],
                             reference='manual pages ...', example='chemlab run INST spin 4000rpm --time 5min')}

    def summary(self, values):           # values: {slot: value} or a Ctx (use self.slot_si / self.val helpers)
        return ['...one line...'], {'json': 'view'}

    def h_spin(self, ctx, p):
        m = self.vmap('spin')                                   # this member's mapping for the verb
        w = p.pos[0]                                            # already SI (rad/s): verbs parse units
        lo, hi = self.arg_range_si(m['cap'], m['arg'])          # range from the contract, in SI
        if (lo is not None and w < lo) or (hi is not None and w > hi):
            ctx.fail(invalid('OUT_OF_RANGE', '...', 'give a speed from ... to ...'))
        self.requirements(ctx, 'spin')                          # the member's "requires" entries for this verb
        ctx.check(not ctx.v(self.slot('lid_open')), 'LID_OPEN', 'the lid is open.', 'close it first.')
        ctx.call(m['cap'], {m['arg']: self.arg_value(m['cap'], m['arg'], w)}, 'set the speed to %.0f rpm' % (w * 9.549))
        return {'text': 'ok: ...\n', 'json': {...}}
```

What the base gives every class (do not re-implement): argument and unit parsing, `--dry-run` (checks run, every native call is
listed, none is sent), destructive verbs (`destructive=True`) only preview without `--yes`, `--save-to`, unknown verbs with close
matches, unsupported verbs with reasons (exit 6), `describe` / `help INST VERB` / `run INST VERB --help` / `run INST`, `read` in
friendly units with the class view on top, error format, partial-progress reports, refusal explanation, the mapping check
against the contract, `status`.

The request context `ctx` (`ctx.py`): `ctx.v(slot)` (current value; `slot#key` for a key of an object slot via `self.slot_si`),
`ctx.call(cap, args, step, refusal=, readback=, query=)` (one native call, validated against the contract schema first, recorded,
read back), `ctx.check(ok, CODE, message, fix)` (a class precondition), `ctx.fail(err)` (raise with the steps done),
`ctx.advance(seconds, until=fn, poll_s=)` (let lab time pass; the only way a class verb may move time — only in verbs that say
they wait; the generic `chemlab wait` takes 60, 30s, 5min or 1.5h), `ctx.plan` / `ctx.notes` / `ctx.done`.

## 4. The fragment (`configs/adapters/<class>.json`)

```json
{"class": "thermal", "adapter": "chemlab.compile.classes.thermal:ThermalAdapter", "about": "...",
 "membership": {"primary": [...], "secondary": {...}, "excluded": {"slug": "reason"}},
 "members": {
  "julabo_corio_cd200f": {
   "role": "primary",
   "verbs": {"set-temp": {"cap": "thermal.set_temperature", "arg": "temperature_K"},
             "heat": {"cap": "circulation.set_running", "arg": "running"},
             "stir": {"speed_cap": "stirring.set_speed", "speed_arg": "speed_rad_s", "on_cap": "stirring.set", "on_arg": "on"},
             "safety-limit": {"cap": "safety.configure", "arg": "limit_K", "args": {"mode": "limit"}}},
   "slots": {"temp": "thermal.indicated_temperature_k", "set": "setpoint.fixed#temperature_k",
             "display": {"slot": "display.temperature", "unit_slot": "unit"}},
   "requires": {"set-temp": [{"slot": "interface.remote", "value": true, "code": "REMOTE_OFF",
                              "message": "{inst} takes set points only in remote mode.", "fix": "`chemlab run {inst} remote on`."}]},
   "codes": {"circulation.set_running:PRECONDITION_FAILED": {"code": "NOT_READY", "message": "...", "fix": "..."}, "UNSTABLE": {...}},
   "unsupported": {"stir": "no stirrer: the bath is mixed by the circulation pump"},
   "alternatives": {"stir": "what to do instead (shown as the fix)"},
   "targets": {"zone": {"arg": "zone_index", "values": {"1": 0, "2": 1}}},
   "locations": {"tray_01": {"position": 1}},
   "path": "acquisition", "path_exceptions": ["sieving.measure"],
   "params": {"tolerance_k": 0.5},
   "notes": {"heat": ["shown in help INST heat"]}}}}
```

Keys a member may use: `verbs.<verb>` with `cap`, `arg`, `args` (fixed arguments), `<name>_cap` / `<name>_arg` for multi-call verbs,
`reads` (state slots, for read-only verbs); `slots.<role>` (slot id, `slot#key`, or `{"slot", "unit", "unit_slot"}`);
`requires`, `codes` (runtime code or `cap:CODE` -> class code, message, fix), `unsupported` (verb -> reason; every class verb a
member lacks needs one), `alternatives`, `hides` (shared verb -> reason, §5.3), `shared_fallthrough`, `targets`, `locations`,
`path` / `path_exceptions`, `params`, `notes` (INST is replaced by the instrument id), `role`, and `adapter` (a member-specific
subclass, rarely needed).

`requires` tests (base `requirements`; several tests in one entry must all hold; `"slot"` may be `slot#key`, or give `"key"`):
`value`, `not` (list), `in` (list), `prefix` (text or list of texts), `contains_not` (an item a list / text slot must not
contain), `min` / `max` (inclusive) / `gt` / `lt` (numbers; a missing value fails), `truthy`, `falsy`, `null`, `set` (not null); an
entry without a test asks for a truthy value. Message and fix may use `{inst}` and `{value}`.

Slot references in a mapping: `check_mapping` checks the values of keys named `slot`, `reads`, `slots_read` or ending in `_slot`
when they look like slot ids (`component.field`, optionally `#key`, or a visible slot id). Other values under such keys (a rack
slot `"A1"`, a number) are class data and are not checked. Never copy ranges, enums or units into a fragment: the adapter reads them
from the contract (`arg_range_si`, `arg_enum`, `arg_unit`). `check_mapping` verifies every capability (agent-visible and
implemented), argument name and slot against the contract at first use and in the tests.

Members are matched by asset type (`asset_type_id` without `instrument.`), so a lab may name its instruments freely ("flex",
"bal1", "bal2"). An entry that names its own `"adapter"` keeps it. The lab config switch `"class_adapters"`: `false` (none), a
directory path (other fragments), or a list of class names.

## 5. Rules

### 5.1 Units: convert to the unit each argument declares

The 203 contracts declare 145 unit strings; many arguments are not SI (°C / degC on 78 arguments, g, min, rpm, mL, mbar, nm, h,
days, %), and one unit is spelled several ways (m^3 / m3, °C / degC, 1/s / s^-1, ohm / Ω). `units.py` has the alias table
(`ALIASES`, `DECLARED` / `_DECL`: unit -> kind, factor, offset). Rules:

- Verbs parse what the agent types into SI (`Arg(kind='temperature')`, `Opt(kind='duration')`; bare numbers take the kind's default
  unit: µL, °C, s, rpm, mbar, g). Kinds: volume, mass, length, wavelength, wavenumber (cm-1), duration (fs ... days), temperature,
  temperature_delta, heating_rate, rotation, angle, pressure, flow, mass_flow, fraction, frequency (Hz ... GHz), frequency_offset
  (signed Hz, e.g. NMR), count_rate (/s, kcps), magnetic_field (T, mT, G), power, voltage, current, force, energy, speed, area,
  concentration (mol/L, mM, µM, nM, pM), density (also mass concentration: g/L, mg/L, µg/L, mg/mL, µg/m3), amount (mol, mmol, µmol,
  nmol, pmol), conductivity. Flow also takes m3/h, L/h and standard flows (sccm, slpm / slm, sccs), fraction
  ppm / ppb, force pN / nN / µN / mN / kN, energy eV / keV, power µW / mW / µcal/s / cal/s, duration us / µs / μs; the declared
  unit `1/min` is rpm (Linari). Kinds field_rate (mT/s), force_rate (N/s), resistance (Ω, kΩ, MΩ), sheet_resistance (Ω/sq),
  moment (emu, A·m²), heat_capacity (J/K) and number (plain) are framework kinds too. Add a missing unit to units.py (the shared
  table), not as a local copy in a class.
- `units.fmt` never shows a non-zero value as 0: it moves to a smaller unit (1 Pa -> `1 Pa`, 5e-12 N -> `5 pN`, 2e-6 -> `2 ppm`;
  durations under 0.05 s -> ms / µs; pressures below 1 mbar keep mbar with 3 significant digits, 5 Pa -> `0.05 mbar`;
  kg/m³ below 100 shows as g/L, mg/L or µg/L, above as g/cm³; X-ray energies in keV / eV). `read` never shows a K slot that
  holds a temperature difference (id or description says diff / delta / tdiff / 温差) in °C. Keep 15 significant digits when you round a native argument (`units.sig(x)`), never
  `round(x, n)` (0.02° is 3.49e-4 rad; `round(.., 8)` breaks the runtime's grid check).
- Handlers convert SI into the argument's declared unit with `self.arg_value(cap, arg, si)` (or `units.parse_for(text, schema)`
  in one step), and check ranges in SI with `self.arg_range_si(cap, arg)`. Never assume an argument is SI.
- Slots: `self.slot_si(values, spec)` gives SI for a slot in any declared unit, or a display slot whose unit is in another slot
  (`{"slot": "display.temperature", "unit_slot": "unit"}` for BINDER C/F).
- A temperature difference is `temperature_delta` (0.5K == 0.5C), not a temperature. Gauge and absolute pressure are never
  converted into each other: the argument name says which one it is.
- Tests on real arguments of several units are in `tests_compile/test_units.py` (`RealArguments`); add your class's odd units there
  or in your class's own test file.

### 5.2 One native path per verb

Some instruments have two native paths for the same action (DMA 5002 / Abbemat `measurement.start` vs `acquisition.start`, HX204,
MP70, Selekt, Extrahera). A member's mapping picks one path, and the verbs of one group (`PATH_GROUPS`, e.g. start / stop /
result) use that path: their capabilities share one namespace (`"path"`, or the namespace of the first capability), and a result
read from slots reads slots that the start capability changes (its contract `state_effects`). Exceptions are explicit
(`"path_exceptions"`, which may also list state slots a result reads although the start capability's `state_effects` leave them
out, e.g. the 8697 `sequence.start`). Enforced statically by `check_paths` (in `check_mapping`) and dynamically by the lowering check
(`path_problems`). Prefer the acquisition core where both exist unless the manual path is the one the class
verbs mean.

### 5.3 Shared vocabulary, the shared layer and the override rule

`compile/shared.py` defines the shared verbs as factories and `SharedAdapter` (class `shared`):

| verb | lowers to (mapping key) |
|---|---|
| `power on\|off` | `power`: `{"cap": "power.set", "arg": "enabled"}` or a toggle `{"cap": "power.toggle", "toggle": true}` + `slots.power` |
| `status` | the class view (every adapter) |
| `open PART` / `close PART` | `open`: `{"parts": {"door": {"cap": "door.set", "arg": "angle_rad", "open": 1.57, "close": 0.0}, "lid": {"open_cap": "lid.open", "close_cap": "lid.close"}}}` |
| `ack` / `estop` | `{"cap": "alarm.acknowledge"}` / `{"cap": "emergency.stop"}` |
| `method KEY=VALUE ...` | `{"cap": "method.configure", "keys": {...class dictionary...}, "passthrough": true}` (§5.5) |
| `start` | `{"cap": "acquisition.start", "job": "operation" \| "state" \| "none", "state": {...}}` (§5.4) |
| `stop` / `result [--save-to F]` | `{"cap": ...}` / `{"cap": ...}` or `{"reads": [...]}` |
| `wait [--max T]` | the caller's latest job on the instrument |

`compile/shared_gen.py` generates `configs/adapters/shared.json` with `"layer": "shared"` for 198 instruments (`python -m chemlab.compile.shared_gen
--write`; never hand-edit it — `test_shared_all203` checks that the generator reproduces it): power 194, open/close 101 (138 named
parts), method 127, start 126, stop 118, result 108, ack 39, estop 21. The other five are the thin class (`configs/adapters/
thin.json`, `classes/thin.py`: oxitopi, puresolvmd, dmt152, Liberty Blue, oligosynt), reviewed by hand. If your class needs a
verb name the shared layer also has (power, open, close, ack, estop, method, start, stop, result, wait), your class verb wins on
your members; give it the same meaning. In `method`, a native argument name with a bare number takes the unit the contract
declares for it (`duration_s=60`); with a unit it is converted (`duration_s=1min`); class keys always take units. **Override rule:** an
instrument in a class fragment and in the shared fragment gets a CompositeAdapter: class fragments first (primary role before
secondary), the shared layer last; a verb belongs to the first part that supports it. So a class verb overrides the shared one,
and the shared layer still answers the verbs the class does not have (`tests_compile/test_shared.py`). **A class's "unsupported"
also wins over the shared layer:** a verb in your class table that a member marks unsupported (with its reason and
alternative) is answered by your class with exit 6 on that member, never by the shared layer — unless the member lists it in
`"shared_fallthrough": ["open"]`, which hands that verb back to the shared layer (e.g. a class that has no part table of its own
for `open`). **Hiding a shared verb:** a class that replaces a shared verb by its own (GPC `run` instead of `start`, TLC
`chromatogram` instead of `result`) declares it: class attribute `HIDES = {"start": "use `chemlab run INST run`"}` (all members) or
member `"hides": {"result": "..."}`; or name the replacing verb: class `COVERS = {"start": "measure"}` / member `"covers":
{"start": "measure"}` (reason: use `chemlab run INST measure`; it applies only on members where the covering verb is supported); the hidden verb answers exit 6 with that reason on those members, `run INST` lists it under
"not on this instrument", `help INST start` says what replaces it; `shared_fallthrough` undoes it per member.
**Ownership order on a composite instrument** (fragments.py `CompositeAdapter.owner`): (1) the first class part that supports
the verb (primary before secondary); (2) else a class part that marks it unsupported or hides it (its reason); (3) else the
shared layer. A class's "unsupported" therefore never blocks another class: on KS 4000 / Elma (agitation primary + thermal
secondary) agitation's `"unsupported": {"set-temp": ...}` leaves `set-temp` to thermal. Class modules may reuse the factories
(`self.table.update(shared.base_verbs(self))`).

**Shared `method` sends the full argument set** (most contracts require every argument of `method.configure`): the keys the
agent gives, the mapping's fixed `args`, and for every other required argument (and every argument chemlab sent last time) in
this order: what the instrument reports (a visible slot `<namespace>.<arg>` such as `method.mode`, or a key of a visible object
slot holding several arguments such as `method.current`), the last method chemlab sent on that capability (request log, any verb
or `chemlab call`), the contract's `example_arguments`. The reply names the source of every value it did not get from the agent
(`--json` `sources`); a required argument with no source is a usage error naming it; values outside the contract ranges are a
usage error (BAD_SETTING) before anything is sent. Helpers for classes: `shared.full_method_args(ad, ctx, cap, given, fixed)`,
`shared.reported_method`, `shared.last_sent`, `shared.method_sources_text`. The other generated shared verbs never need
arguments they do not supply (checked over all 198 members).

### 5.4 Jobs: three kinds of long action

| kind | when | class | status / wait / cancel |
|---|---|---|---|
| operation | an async capability returns an operation_id (most `*.start`) | `jobs.OperationJob` | poll / cancel of the session |
| run-type / state | progress only in slots (Flex protocol runs, furnace programmes, GC `run.start`, `cycle.start`) | `jobs.StateJob` (or a subclass, e.g. the liquid class's `RunJob`) | slot values; cancel = the class's stop call |
| timed state change | no run object (reach a set point, a stable reading) | `jobs.ConditionJob` | a condition on the state; cancel stops waiting (or `on_cancel`) |

A verb that starts a long action returns `job-N` (text and `--json` `job`). The agent follows it with the generic `chemlab status
job-N`, `chemlab wait job-N [--max T]`, `chemlab cancel job-N`; `chemlab status` lists the caller's jobs. Jobs belong to their
caller. In `--json` the job's `status` is the framework's (running / succeeded / failed / cancelled); the class's own word is in
`<kind>_status` (e.g. `run_status: completed`). Job states: `jobs.RUNNING` lists the known running words (incl. `injecting`,
`equilibrating`, `run`, `post`, ...), and any other text state that is not terminal (`jobs.TERMINAL`: completed, succeeded,
failed, cancelled, aborted, stopped, idle, ready, ...) also counts as running, so an unknown run phase keeps the job alive. An
operation whose poll says `succeeded` while its result names a known running phase (`phase: injecting`) is still running.
`OperationJob(..., poll_s=N)` (or the member's `"params": {"job_poll_s": N}`) sets the lab seconds between polls while waiting:
use 10-60 s where the runtime answers a poll with a whole curve. `self.wait_until(ctx, test, what, max_s, poll_s)` (with
`self.moving_done([slots])`) waits for a lid, sash, drawer or stage to stop moving; refused NOT_SETTLED when it does not. Blocking variants (`wait-temp`, `weigh --stable`, `set-temp --wait`) move lab
time with `ctx.advance` and say how long they waited.

### 5.5 Method keys per class

`method` is the hardest verb (chromatography alone has 9 method shapes). Each class defines a small dictionary of canonical keys
with units — e.g. chromatography `flow=1mL/min oven=40C..250C@10K/min inj=1uL wavelength=254nm`, spectroscopy `from=200nm to=800nm
step=1nm` — and each member maps them: `"method": {"cap": ..., "keys": {"flow": {"arg": "flow_ml_min", "kind": "flow"}}}`. Keys the
dictionary does not have pass through by native argument name (`"passthrough": true`), still parsed into the declared unit.
Document the dictionary in the class module docstring and in `help INST method`.

### 5.6 Named locations and `--zone / --channel / --cell / --head`

- A deck of labware (Flex): `deck.py` (`LABWARE:WELL`, lists, ranges in Opentrons order, trash, home), built from the lab-config
  `"deck"` plus the contract's slot geometry; the trusted host's scene registers the same deck with the runtime.
- Other named places (autosampler trays, rack positions, robot ports): `self.location(ctx, name)` looks the name up in the lab
  config entry's `"locations"` (task / lab specific) and then the mapping's `"locations"` (fixed by the instrument), with close
  matches on a miss.
- Several identical units on one instrument: one option each, `--zone` (heating blocks, reactor pads), `--channel` (pumps, gas
  lines), `--cell` (measuring cells), `--head` (measuring / dosing heads): `verbs.target_opt('zone')` in the verb and the member's
  `"targets": {"zone": {"arg": NATIVE, "values": {"1": native, ...}, "required": false}}`; `self.target_args(ctx, parsed)` lowers
  them. Do not invent per-class spellings (`--pad`, `--line`, ...).

### 5.7 Records: `--save-to FILE`

Verbs that return a record (results, spectra, chromatograms, images, a temperature log) take `--save-to FILE` (`save_to_opt()`):
the full reply is written as JSON into the agent's work dir (paths outside it are a usage error, nothing sent). Files the runtime
writes are already copied into the work dir by the generic layer.

### 5.8 Errors and refusals

Every error says what is wrong, how to fix it, whether the instrument changed and the steps done before it (composite verbs):

```
error[refused/UNSTABLE]: the reading of ohaus_px224e is not stable (a draft-shield door is open), so it cannot tare.
fix: close the doors (`chemlab run ohaus_px224e door close`), then retry with --wait 30s.
instrument changed: no
request: r-0001
```

Exit codes are the generic ones: 2 usage (nothing sent), 3 refused, 4 unexpected, 6 not supported on this instrument. Check what
you can before sending (class checks with `ctx.check`, member requirements in `requires`); when the runtime still refuses with a
generic code, `explain_refusal` names the reason from the member's `codes`, the class `CODES`, or the contract preconditions with
the state already read (`It checks ...; now ...`). Override `explain_refusal` only to infer a reason from the state (the balance
class turns the XPR's bare PRECONDITION_FAILED into UNSTABLE / POWER_OFF / ZERO_RANGE).

### 5.9 World set-up belongs to the host

What the lab operator does before the agent starts (fill a bath, register a sample, install a fixture, place a load) is
lab-config set-up (`"setup"` steps: capability calls as operator or `"evaluator": true`, `{"host": method, "kwargs": ...}`) or task
`world(lab)` code — never class verbs and never adapter code. Examples: `tasks/thermal_world.py` (JULABO bath model before power-on,
Elma tank fill), `tasks/balance_tasks.py` (`place`: pan.set_load as the evaluator, or the XPR cell's contact force). A runtime that
needs a trusted scene during the session (the Flex's tip confirmations) gets a scene class in the entry (`"scene":
"module:Class"`, `scenes/flex.py`).

**The default lab.** `configs/all203.json` is a ready lab: `chemlab/default_world.py` (generated by
`python -m chemlab.default_world_gen` from the class worlds: lowering `config()`s and, recorded as steps, the function worlds of
particle_surface and quant_analyzer) + `chemlab/default_world_overrides.py` (hand, with sources), merged by make_config. Its steps
carry `"default": GROUP` (utility, accessory, consumable, standard, state). A class world that needs a different start sets
`"default_world": false` on the entry (all default steps off, e.g. "_empty" variants, `W.bare`) or adds a step
`{"replace_default": [GROUP, ...]}`; a world step identical to a default step runs once. The lab's containers (bottles, standards,
consumables, cylinders) are in `configs/inventory_default.json` (`chemlab/inventory.py`; `chemlab list --inventory [ID]`). After a
class world changes: re-run the generator, then make_config, then `tests_generic/test_default_world.py`.

## 6. Pitfalls

- The default lab is a ready world (see 5.9): a task that tests a missing fixture or an empty cell must switch the default off
  for that entry. Probe first; put the world in
  tasks, the reason in `codes`.
- Lab time moves only with `chemlab wait` (or a verb that says it waits). Clock steps are applied lazily per instrument; a read
  flushes them. A changed load or door shows up only after a clock step.
- The twin's own "stable" / "reached" flags can rise early (an older XPR twin said stable 1.5 s after a load change while 0.4 % low).
  Waiting verbs add a settle check (two readings 1 s apart within 2 d) instead of trusting one flag.
- Member-specific rules hide in the contract preconditions and the runtime (JULABO only takes set points in remote mode; KS 4000
  takes safety limits only with heating off; CellXpert set points in 0.1 °C steps; the SPX zero range counts from the calibration
  zero). Turn each into a `requires` entry or a class check with a fix.
- Composite verbs report partial progress: do the checks that can fail before the first native call (e.g. stir checks the
  fixture before setting the speed).
- Never call host methods from an adapter, never use the evaluator principal in a class, never put manual text into code or docs.
- A class module that fails to build its adapter (an exception in `__init__` / `build_verbs`) is left out of the lab and recorded
  in `fragments.LOAD_ERRORS`, so other classes keep working; the class tests build adapters with `strict=True` and fail on it.
- A handler may raise the generic `usage(...)` (e.g. from `coerce`): it stays a usage error (exit 2), not INTERNAL.
- Write fragment files atomically (`fragments.write_json_atomic(path, obj)`: temp file + rename) from your generator: a lab
  loading fragments while you rewrite one otherwise reads half a file (the loader retries once).
- Runtimes with no advance mode whose timed events run only in `host_update()` (Agilent 8697; DR6000 also syncs on access) get it
  called after every clock step by the lab backend (`inst.clock_hook`); no scene is needed for that any more.

## 7. Lowering table and acceptance tasks

`tasks/<class>_lowering.py`:

```python
from .thermal_world import config          # optional: the lab config with the class's world (else all203)
CASES = {'julabo_corio_cd200f': {'prep': None, 'steps': [
    ('set-temp 40C', []),                                              # refused by a class check: no native call
    ('remote on', [('interface.set_remote', {'enabled': True})]),
    ('set-temp 40C', [('thermal.set_temperature', {'temperature_K': 313.15})]),
    ('#wait 2', None),                                                 # lab time passes on both labs
    ('wait-temp --max 30min', []),
]}}
```

Expected calls are written from the contracts (capability, arguments in the declared unit), not derived from the mapping. For
analysis steps whose arguments are measured arrays, an expected item may be `(capability, callable(arguments) -> bool)`, or the
whole expectation a `callable(calls) -> bool`; inside expected arguments the matchers `lowering.Measured()` (a finite number or
a non-empty list / object) and `lowering.Obs(n, fixed, measured)` (n observation objects: fixed fields equal, measured fields
present) stand for measured values; `None` means not checked (use it only for state-dependent calls). `('#host what', fn)` runs
`fn(backend)` on the lab and on the twin (the lab operator swaps a sample in the middle of a case) and compares their states. The
check runs each verb on a lab with adapters, compares the native calls, replays the same calls (same request ids) and clock
advances on a twin `LabBackend` without chemlab, and compares the instrument's full evaluator view after every step; it also
checks the one-path rule (on a composite instrument, each part only on the verbs it owns). Cover every verb of every member, refusals included (`[]`), a dry run, and an unsupported verb.

`tasks/<class>_tasks.py`: `TASKS = [{'id', 'kind', 'instruments', 'config' (fn(cfg) -> cfg, optional) or 'lab_config' (fn() ->
whole config), 'world' (fn(lab), optional), 'instruction', 'gold' (bash with `chemlab run`), 'check' (fn(c))}]` and
`main(only, noact)` returning `run_suite(...)`. Checkers read `c.ev(iid)` (evaluator view, truth slots included), `c.work_file()`,
`c.number_file()`; `c.near()`, `c.equal()`, `c.add()`. Gold must pass in the sandbox, NoAct must fail; include recovery tasks (the
gold triggers the error, reads `--json` `.error.code`, and recovers as the fix says). Results go to `compile_results/<class>/`.

## 8. Tests (all must pass before reporting)

```bash
cd Chemistry-Sim/chemlab/tests_compile
PY=python3                       # Python 3.10+ with the runtime requirements (numpy < 2.4, scipy, pillow, ...)
$PY -m unittest test_units test_framework test_shared test_visibility   # framework + visibility of every adapter output
$PY -m unittest test_class_<class>                                 # mapping fits every contract, lowering, acceptance
CHEMLAB_PARITY_CLASSES=<class> $PY -m unittest test_generic_parity test_visibility   # generic sweeps + visibility on your members
cd ../tests_generic && $PY -m unittest test_generic_core test_generic_sandbox test_mcp_shell   # generic layer unchanged
cd .. && $PY -m unittest discover -s tests                         # the Flex pilot's gate and sandbox
```

`test_class_<class>.py` is two lines:

```python
from helpers import class_suite
ThermalClass = class_suite('thermal')
```

`test_generic_parity` runs `sweep.equivalence` and `sweep.ready` (with the visibility scan of describe / read output, now with
class sections) on the members of the class fragments — `CHEMLAB_PARITY_CLASSES=a,b` limits it to your classes — each run in its
own directory `build/compile_parity/<pid>-<time>/` (generic_results/ untouched), so runs at the same time never collide.
`test_visibility` takes the same filter. Lowering runs go to `build/lowering_runs/<class>/<instrument>/`. A mapping that does not
fit its contract never breaks the generic commands of that instrument (they fall back to the generic path; `chemlab run` answers
MAPPING_ERROR), but `test_class_<class>` fails on it.

## 9. Coverage and examples

All 203 instruments have `chemlab run` verbs: 198 have a primary class (3 of them also a secondary class), 5 are in the thin
class (shared verbs only, reviewed by hand) and the shared layer adds the common verbs wherever a contract has them. The 30
classes are agitation, balance, bioassay, chromatography, coating, digestion, dosing, electrical, electrochem, enclosure,
evaporation, extraction, formulation, gas_fluid, imaging, labware_automation, liquid, mechanical, particle_surface,
property_meter, quant_analyzer, reactor, sample_intro, solid_prep, spectroscopy, structure, thermal, thermal_analysis,
titration and wash_sterilize (one fragment each in `configs/adapters/`).

Small classes to read first: `balance` (3 members: power, zero, tare, weigh [--stable], unit, door, status) and `thermal`
(set-temp [--wait], heat, temp, wait-temp, stir, safety-limit, remote, power, status). A member may bring its own adapter class
(`"adapter"` in its fragment entry), as the NE-1000 syringe pump does inside `liquid` (`classes/syringe_pump.py`).
Twins refuse to stop or change another caller's run (ACCESS_DENIED): the classes say NOT_YOURS, and their BUSY fix says to
wait instead of stop when the run is not the caller's job.
