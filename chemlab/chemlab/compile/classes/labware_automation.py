"""Labware automation class ("labware_automation"): plate sealer, tube decapper, rack barcode reader, robot arm - devices that
handle plates, tubes and racks.

Verbs (members and mappings: configs/adapters/labware_automation.json):

  configure KEY=VALUE ...           device settings (sealer: time=3s protocol=NAME; decapper: mode=decap|recap operation=release_cap|...
                                    time=1|2|3 tube=CONSUMABLE; rack reader: format=96 matching=on|off export=json|csv)
  set-temp T                        seal temperature
  start                             seal / decap or recap / read the rack (a job: chemlab wait|status|cancel job-N)
  stop                              cancel the running seal or scan
  result [--format json|csv] [--save-to F]   the seal record / cycle record / rack map / arm state
  open PART / close PART            the sealer's plate carrier, the reader's rack lift
  move POSE | z=.. shoulder=.. elbow=.. wrist=..   move the arm (named poses of the lab, or joints with units); waits until it is there
  pick NAME                         move to a container and grip it (named containers of the lab)
  place NAME                        move to a receiving nest and release the container there
  status                            the class view

Named poses, containers and nests (§5.6) come from the lab config entry's "locations": {"home": {"joints": {...}}, "plate_1":
{"container_id", "constraint_id", "joints"}, "sealer_nest": {"receiver_port_id", "joints"}} - the same registry the lab's arm scene
uses, so the names the agent types are the lab's own.

Vendor-neutral models followed: SiLA 2 (sealers, decappers, barcode readers as SiLA devices: ObservableCommand Seal / Scan),
ANSI/SLAS microplate formats, PyLabRobot resources and arms (move, pick_up_resource, drop_resource), ROS MoveIt-style named poses.
"""
import math

from .. import units
from ..base import finite, short
from ..errors import invalid, refused
from ..jobs import OperationJob, registry
from ..verbs import Arg, Opt, Verb, save_to_opt
from .assay_common import StepAdapter

REF = ('Azenta a4S operating manual (seal temperature and time, protocols, carrier); Azenta IntelliXcap S1 manual (decap / recap '
       'modes, operation modes, cycle time); Ziath DP5 Mirage user guide (rack formats, identity matching, export); Brooks PreciseFlex '
       '400 manual (joint moves, gripper)')
JOINTS = (('z', 'z_m', 'length'), ('shoulder', 'shoulder_rad', 'angle'), ('elbow', 'elbow_rad', 'angle'), ('wrist', 'wrist_rad', 'angle'))


class LabwareAdapter(StepAdapter):
    CLASS = 'labware_automation'
    TITLE = 'handle plates, tubes and racks'
    ABOUT = ('Devices that handle labware: seal a plate, decap or recap a tube, read the barcodes of a rack, move a robot arm between '
             'named places and pick or place a container. Settings with units, the long action as a job, its record, named poses.')
    STANDARDS = ('SiLA 2 (sealers, decappers, barcode readers); ANSI/SLAS microplate formats; PyLabRobot resources and arms; '
                 'ROS MoveIt-style named poses')
    ALWAYS = ('status',)
    UNIT_KINDS = ('temperature', 'duration', 'length', 'angle')
    PATH_GROUPS = (('start', 'stop', 'result'),)
    CODES = {
        'POWER_OFF': ('POWER_OFF', '{inst} is switched off.', 'switch it on: `chemlab run {inst} power on`.'),
        'BUSY': ('BUSY', '{inst} is busy with a running cycle.', 'wait for it (`chemlab run {inst} wait`) or stop it.'),
        'UNKNOWN_OPERATION': ('NOT_RUNNING', 'that operation is not known to {inst}.', 'see `chemlab run {inst} status`.'),
    }

    # ------------------------------------------------------------------ verbs
    def build_verbs(self):
        out = {
            'configure': Verb('configure', 'device settings: KEY=VALUE ... (see `chemlab help INST configure`)', self.h_configure,
                              variadic=('KEY=VALUE', 'a setting'), preconditions=['no cycle running', 'values within the contract ranges'],
                              errors=['UNKNOWN_SETTING, OUT_OF_RANGE (usage)', 'BUSY (refused)'], example='chemlab run INST configure time=3s', reference=REF),
            'set-temp': Verb('set-temp', 'set the seal temperature', self.h_set_temp, positionals=[Arg('TEMP', 'e.g. 170C', kind='temperature')],
                             preconditions=['within the instrument range (contract)', 'power on', 'no seal running'],
                             errors=['OUT_OF_RANGE (usage)', 'POWER_OFF, BUSY (refused)'], example='chemlab run INST set-temp 170C', reference=REF),
            'start': Verb('start', 'seal / decap or recap / read the rack (returns job-N)', self.h_start, long_action=True,
                          preconditions=['the labware is in place', 'settings made', 'at temperature (sealer)'],
                          errors=['NOTHING_LOADED, NOT_AT_TEMPERATURE, CARRIER_IN, RACK_RAISED (refused)'], example='chemlab run INST start', reference=REF),
            'stop': Verb('stop', 'cancel your running seal or scan', self.h_stop, preconditions=['a cycle of yours is running'],
                         errors=['NOT_RUNNING (refused)'], example='chemlab run INST stop', reference=REF),
            'result': Verb('result', 'the record of the last cycle (seal record, cap cycle, rack map, arm state)', self.h_result, readonly=True,
                           options={'format': Opt('export format of the rack map (json or csv)', metavar='FORMAT', choices=['json', 'csv']),
                                    'save-to': save_to_opt()},
                           example='chemlab run INST result --save-to rack.json', reference=REF),
            'move': Verb('move', 'move the arm to a named pose, or to joints (z=100mm shoulder=30deg elbow=-45deg wrist=0deg); waits until it is there',
                         self.h_move, positionals=[Arg('POSE', 'a named pose of the lab (see `chemlab help INST move`)', required=False)],
                         variadic=('JOINT=VALUE', 'z=, shoulder=, elbow=, wrist= with units'),
                         preconditions=['power on', 'the arm is not holding still in a fault', 'joints within the contract limits'],
                         errors=['UNKNOWN_LOCATION, OUT_OF_RANGE (usage)', 'POWER_OFF, ARM_FAULT (refused)'], example='chemlab run INST move home', reference=REF),
            'pick': Verb('pick', 'move to a container and grip it', self.h_pick, positionals=[Arg('NAME', 'a container of the lab')],
                         preconditions=['nothing gripped', 'the container is known to the lab'], errors=['UNKNOWN_LOCATION (usage)', 'HOLDING (refused)'],
                         example='chemlab run INST pick plate_1', reference=REF),
            'place': Verb('place', 'move to a receiving nest and release the gripped container there', self.h_place,
                          positionals=[Arg('NAME', 'a receiving nest of the lab')], preconditions=['a container is gripped'],
                          errors=['UNKNOWN_LOCATION (usage)', 'NOT_HOLDING (refused)'], example='chemlab run INST place sealer_nest', reference=REF),
        }
        part = [Arg('PART', 'which part (carrier, rack); optional when there is only one', required=False)]
        out['open'] = Verb('open', 'open a part: move the sealer\'s plate carrier out, lift the rack off the reader', lambda c, p: self.h_part(c, p, 'open'),
                           positionals=part, preconditions=['the part is closed', 'the device allows it now'], errors=['UNKNOWN_PART (usage)', 'NOT_NOW (refused)'],
                           example='chemlab run INST open carrier', reference=REF)
        out['close'] = Verb('close', 'close a part: take the sealer\'s empty carrier in, seat the rack on the reader', lambda c, p: self.h_part(c, p, 'close'),
                            positionals=part, preconditions=['the part is open', 'the device allows it now'], errors=['UNKNOWN_PART (usage)', 'NOT_NOW (refused)'],
                            example='chemlab run INST close carrier', reference=REF)
        return out

    def h_part(self, ctx, p, action):
        parts = (self.vmap('open') or {}).get('parts') or {}
        name = p.pos[0] if p.pos else (next(iter(parts)) if len(parts) == 1 else None)
        if name is None:
            ctx.fail(invalid('WHICH_PART', '%s has several parts: %s.' % (self.instrument_id, ', '.join(parts)), 'name one.'))
        if name not in parts:
            ctx.fail(invalid('UNKNOWN_PART', '%s has no part %r to open or close.' % (self.instrument_id, name), 'parts: %s.' % ', '.join(parts)))
        spec = parts[name]
        done = (spec.get('is') or {}).get(action)
        if done is not None and self.cond(ctx, done):
            return {'text': 'ok: the %s of %s is already %s\n' % (name, self.instrument_id, 'open' if action == 'open' else 'closed'),
                    'json': {'part': name, 'action': action, 'changed': False}}
        self.need(ctx, '%s:%s' % (action, name))
        if spec.get('%s_cap' % action):
            cap, args = spec['%s_cap' % action], dict(spec.get('args') or {})
        else:
            cap, args = spec['cap'], dict(spec.get('args') or {})
            args[spec['arg']] = spec[action]
        ctx.call(cap, args, '%s the %s' % (action, name))
        return {'text': 'ok: the %s of %s %s\n' % (name, self.instrument_id, 'opened' if action == 'open' else 'closed') if not ctx.dry_run else '',
                'json': {'part': name, 'action': action, 'changed': True}}

    # ------------------------------------------------------------------ configure / temperature
    def h_configure(self, ctx, p):
        m = self.vmap('configure')
        pairs = self.pairs(ctx, p, 'configure')
        keys = m.get('keys') or {}
        if m.get('cap'):                                                 # one capability takes every setting (rack reader)
            cap = m['cap']
            args = self.settings(ctx, cap, pairs, keys, m.get('passthrough', False), dict(m.get('args') or {}))
            kept = []
            for arg, spec in (m.get('keep') or {}).items():
                if arg not in args:
                    args[arg] = self.sv(ctx, spec['slot'])
                    kept.append(arg)
            self.need(ctx, 'configure')
            lc = m.get('len_check')
            if lc and lc['arg'] in args and self.cond(ctx, lc.get('when')):
                n = len(self.sv(ctx, lc['slot']) or [])
                ctx.check(n == args[lc['arg']], lc['code'], lc['message'].format(inst=self.instrument_id, n=n, value=args[lc['arg']]),
                          lc['fix'].format(inst=self.instrument_id, n=n, value=args[lc['arg']]), label='matches the loaded item')
            self.check_args(ctx, cap, args)
            ctx.call(cap, args, 'set %s' % ', '.join('%s=%s' % (k, short(v, 20)) for k, v in sorted(args.items())))
            return {'text': 'ok: %s configured (%s)%s\n' % (self.instrument_id, ', '.join(k for k, _ in pairs),
                                                            ('; kept as they were: %s' % ', '.join(kept)) if kept else '') if not ctx.dry_run else '',
                    'json': {'arguments': args, 'kept': kept}}
        calls = []
        for k, text in pairs:                                            # one capability per setting (sealer, decapper)
            spec = keys.get(k)
            if spec is None:
                import difflib
                close = difflib.get_close_matches(k, list(keys), 2)
                ctx.fail(invalid('UNKNOWN_SETTING', '%s has no setting %r.' % (self.instrument_id, k),
                                 ('did you mean %s? ' % ' or '.join(close) if close else '') + 'settings: %s.' % ', '.join(keys)))
            cap = spec['cap']
            args = dict(spec.get('args') or {})
            schema = self.cap_schema(cap, spec['arg'])
            args[spec['arg']] = self.value(ctx, text, schema, spec.get('kind'), k)
            if spec.get('list'):
                self.known(ctx, str(args[spec['arg']]), spec['list'], spec.get('word', k))
            self.check_args(ctx, cap, args)
            calls.append((k, cap, args))
        self.need(ctx, 'configure')
        for k, cap, args in calls:
            ctx.call(cap, args, 'set %s=%s' % (k, short(next(iter(v for a, v in args.items() if a == keys[k]['arg'])), 30)))
        return {'text': 'ok: %s configured (%s)\n' % (self.instrument_id, ', '.join(k for k, _, _ in calls)) if not ctx.dry_run else '',
                'json': {'calls': [{'setting': k, 'capability': c, 'arguments': a} for k, c, a in calls]}}

    def h_set_temp(self, ctx, p):
        m = self.vmap('set-temp')
        cap, arg = m['cap'], m['arg']
        k = p.pos[0]
        args = dict(m.get('args') or {})
        args[arg] = self.resolve(ctx, cap, arg, k)
        self.check_args(ctx, cap, args)
        self.need(ctx, 'set-temp')
        ctx.call(cap, args, 'set the seal temperature to %s' % units.fmt(k, 'temperature'))
        now = self.slot_si(ctx, self.slot('temp')) if self.slot('temp') else None
        return {'text': 'ok: %s seal temperature %s (now %s)\n' % (self.instrument_id, units.fmt(k, 'temperature'),
                                                                   units.fmt(now, 'temperature') if finite(now) else 'unknown') if not ctx.dry_run else '',
                'json': {'set_point_k': k, 'temperature_k': now}}

    # ------------------------------------------------------------------ start / stop / result
    def h_start(self, ctx, p):
        m = self.vmap('start')
        cap = m['cap']
        self.need(ctx, 'start')
        r = ctx.call(cap, dict(m.get('args') or {}), m.get('step', 'start (%s)' % cap))
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        what = '%s %s' % (self.instrument_id, m.get('word', 'cycle'))
        job = self.start_job(ctx, m, r, cap, what)
        if job is None:
            return {'text': 'ok: %s started\n' % what, 'json': {'result': r.get('result')}}
        return {'text': self.job_text(job, what), 'json': {'job': job.handle, 'kind': job.kind, 'operation_id': r.get('operation_id')}}

    def my_operation(self, ctx, cap):
        """The operation id of the caller's latest job on this instrument started with ``cap`` (None when there is none)."""
        for j in reversed(list(registry(ctx.core).values())):
            if j.caller == ctx.caller and j.instrument == self.instrument_id and isinstance(j, OperationJob) and j.cap == cap:
                return j.operation_id
        return None

    def h_stop(self, ctx, p):
        m = self.vmap('stop')
        running = self.cond(ctx, m['running']) if m.get('running') else True
        ctx.check(running, 'NOT_RUNNING', 'nothing is running on %s.' % self.instrument_id, 'nothing to stop; see `chemlab run %s status`.' % self.instrument_id,
                  label='a cycle running')
        args = dict(m.get('args') or {})
        if m.get('op_arg'):
            op = self.my_operation(ctx, (self.vmap('start') or {}).get('cap'))
            ctx.check(op is not None, 'NOT_YOURS', 'the running %s on %s was not started by you, so you cannot cancel it.' % (m.get('word', 'cycle'), self.instrument_id),
                      'wait for it to end (`chemlab run %s status`).' % self.instrument_id, label='started by you')
            args[m['op_arg']] = op
        ctx.call(m['cap'], args, 'cancel the running %s' % m.get('word', 'cycle'))
        return {'text': 'ok: %s stopped\n' % self.instrument_id if not ctx.dry_run else '', 'json': {'stopped': True}}

    def h_result(self, ctx, p):
        m = self.vmap('result')
        cap = m['cap']
        args = dict(m.get('args') or {})
        fmt = p.get('format')
        if fmt is not None and not m.get('format_arg'):
            ctx.fail(invalid('NOT_AN_OPTION_HERE', 'result takes no --format on %s.' % self.instrument_id, 'leave it out.'))
        if m.get('format_arg'):
            args[m['format_arg']] = fmt or self.sv(ctx, m.get('format_slot')) or 'json'
        self.need(ctx, 'result')
        r = ctx.call(cap, args, 'read the record (%s)' % cap, query=True)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        res = r.get('result')
        if m.get('content_json') and isinstance(res, dict) and isinstance(res.get('content'), str) and res.get('media_type') == 'application/json':
            import json
            try:
                res = dict(res, content=json.loads(res['content']))
            except ValueError:
                pass
        lines = ['%s record:' % self.instrument_id]
        body = res.get('content') if isinstance(res, dict) and isinstance(res.get('content'), dict) else None
        if body is not None:
            for k in m.get('headline') or []:
                if k in body:
                    lines.append('  %s = %s' % (k, short(body[k], 200)))
        if isinstance(res, dict):
            for k, v in list(res.items())[:20]:
                lines.append('  %s = %s' % (k, short(v, 200) if not isinstance(v, str) else (v if len(v) < 400 else v[:400] + ' ... (use --json)')))
        return {'text': '\n'.join(lines) + '\n', 'json': {'record': res}}

    # ------------------------------------------------------------------ the arm
    def joints_from(self, ctx, pairs):
        """z=100mm shoulder=30deg ... -> joints in the declared units (all four; the ones not given stay where the arm is)."""
        current = self.sv(ctx, self.slot('joints')) or {}
        out = {native: current.get(native) for _, native, _ in JOINTS}
        table = {}
        for n, native, kind in JOINTS:
            table[n] = table[native] = (native, kind)
        for k, text in pairs:
            if k not in table:
                ctx.fail(invalid('UNKNOWN_JOINT', '%s has no joint %r.' % (self.instrument_id, k), 'joints: z, shoulder, elbow, wrist.'))
            native, kind = table[k]
            out[native] = units.to_declared(units.parse(text, kind, what=k), units.declared(self.joint_schema(native)))
        missing = [n for n, native, _ in JOINTS if out.get(native) is None]
        if missing:
            ctx.fail(invalid('MISSING_JOINT', 'the arm has no known pose yet, so give every joint (%s missing).' % ', '.join(missing),
                             'e.g. move z=100mm shoulder=0deg elbow=0deg wrist=0deg, or a named pose.'))
        return {k: float(v) for k, v in out.items()}

    def joint_schema(self, native):
        return ((self.cap_schema((self.vmap('move') or {}).get('cap'), 'joints').get('properties') or {}).get(native)) or {}

    def at_pose(self, values, joints):
        now = self.sv(values, self.slot('joints')) or {}
        status = self.sv(values, self.slot('motion'))
        return status == self.param('ready_value', 'ready') and all(finite(now.get(k)) and abs(now[k] - v) <= (3e-7 if k == 'z_m' else 2e-6) for k, v in joints.items())

    def go(self, ctx, joints, what):
        m = self.vmap('move')
        if self.at_pose(ctx, joints):
            ctx.checks.append('already at %s' % what)
            return 0.0
        self.check_args(ctx, m['cap'], {'joints': joints})
        ctx.call(m['cap'], {'joints': joints}, 'move the arm to %s' % what)
        return self.wait_until(ctx, lambda v: self.at_pose(ctx, joints), float(self.param('move_max_s', 60.0)), 'wait until the arm is at %s' % what,
                               ('MOVE_FAILED', 'the arm of {inst} did not reach %s.' % what, 'check `chemlab run {inst} status` (a fault needs the lab operator).'))

    def arm_checks(self, ctx, verb):
        self.need(ctx, verb)

    def h_move(self, ctx, p):
        name = p.pos[0] if p.pos else None
        pairs = self.pairs(ctx, p, 'move', required=False)
        if name and '=' in name:
            pairs = [tuple(name.split('=', 1))] + pairs
            name = None
        if (name is None) == (not pairs):
            ctx.fail(invalid('MOVE_TARGET', 'move needs a named pose or joints, not %s.' % ('both' if name else 'neither'),
                             'e.g. `chemlab run %s move home` or `... move z=100mm shoulder=30deg elbow=-45deg wrist=0deg`.' % self.instrument_id))
        if name:
            loc = self.location(ctx, name)
            if 'joints' not in loc:
                ctx.fail(invalid('NOT_A_POSE', '%s is not a pose of %s.' % (name, self.instrument_id), 'pick / place it, or name a pose.'))
            joints = {k: float(v) for k, v in loc['joints'].items()}
            what = name
        else:
            joints = self.joints_from(ctx, pairs)
            what = ', '.join('%s=%s' % (n, units.fmt(units.from_declared(joints[native], units.declared(self.joint_schema(native))), kind))
                             for n, native, kind in JOINTS)
        self.arm_checks(ctx, 'move')
        waited = self.go(ctx, joints, what)
        tcp = self.sv(ctx, self.slot('tcp')) or {}
        return {'text': 'ok: arm of %s at %s (after %s)\n' % (self.instrument_id, what, units.fmt_s(waited)) if not ctx.dry_run else '',
                'json': {'joints': joints, 'tcp_position_m': tcp, 'waited_s': waited}}

    def h_pick(self, ctx, p):
        m = self.vmap('pick')
        name = p.pos[0]
        loc = self.location(ctx, name)
        if 'container_id' not in loc:
            ctx.fail(invalid('NOT_A_CONTAINER', '%s is not a container of the lab.' % name, 'name a container (see `chemlab help %s pick`).' % self.instrument_id))
        self.arm_checks(ctx, 'pick')
        self.go(ctx, {k: float(v) for k, v in loc['joints'].items()}, name)
        ctx.call(m['cap'], {'container_id': loc['container_id'], 'constraint_id': loc['constraint_id']}, 'grip %s' % name)
        return {'text': 'ok: %s holds %s\n' % (self.instrument_id, name) if not ctx.dry_run else '', 'json': {'holding': loc['container_id']}}

    def h_place(self, ctx, p):
        m = self.vmap('place')
        name = p.pos[0]
        loc = self.location(ctx, name)
        if 'receiver_port_id' not in loc:
            ctx.fail(invalid('NOT_A_NEST', '%s is not a receiving nest of the lab.' % name, 'name a nest (see `chemlab help %s place`).' % self.instrument_id))
        self.arm_checks(ctx, 'place')
        held = self.sv(ctx, self.slot('holding'))
        self.go(ctx, {k: float(v) for k, v in loc['joints'].items()}, name)
        ctx.call(m['cap'], {'receiver_port_id': loc['receiver_port_id']}, 'release %s at %s' % (held, name))
        return {'text': 'ok: %s placed %s at %s\n' % (self.instrument_id, held, name) if not ctx.dry_run else '', 'json': {'placed': held, 'at': name}}

    def verb_help(self, name):
        r = super().verb_help(name)
        if name in ('move', 'pick', 'place') and self.supported(name):
            locs = dict(self.map.get('locations') or {})
            locs.update(self.config_entry.get('locations') or {})
            kinds = {'move': 'joints', 'pick': 'container_id', 'place': 'receiver_port_id'}
            names = sorted(n for n, l in locs.items() if kinds[name] in l)
            r['text'] += 'named %s in this lab: %s\n' % ({'move': 'poses', 'pick': 'containers', 'place': 'nests'}[name], ', '.join(names) or 'none')
            r['json']['locations'] = names
        return r


_ = (math, refused)
