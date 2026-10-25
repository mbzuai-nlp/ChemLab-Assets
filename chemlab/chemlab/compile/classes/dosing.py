"""Dosing class ("dosing"): automated powder dosing into a vial on a balance (METTLER TOLEDO Quantos Q2 dosing unit on an
XPE206DR analytical balance).  The Quantos is kept out of the balance class: its job is to dose a target
quantity, not to weigh what the agent puts on the pan.  The balance class's reading helpers (readability, doors, power check,
the explanation of a bare PRECONDITION_FAILED) are reused by inheritance; the twin's balance has no stability flag (its mass
observation is ideal), so there is no stable-reading wait here.

Verbs (status from the framework; power from the shared layer):

    dose AMOUNT [--tolerance 1%|0.1mg] [--head ID] [--vial POS] [--sample-id ID] [--wait] [--max T]
         a job: select the loaded dosing head and the vial position, close the doors, set target and tolerance, tare the vial,
         start dosing (the reply names head, vial position, target in mg and tolerance)
    head [show | select ID | lock | unlock | raise | lower | remove]      the dosing head (identify = show its RFID data)
    vial [show | select POS | place | remove]                             the receiving vial (place / remove open the front door)
    open|close [front|side]                                               the draft-shield front door or the side doors
    tare                                                                  tare the vial on the pan (doors closed)
    result [--save-to FILE]                                               the last dosing: actual, target, deviation, in tolerance
    stop                                                                  abort the running dosing

Inserting and taking out a dosing head and putting a vial on the pan are the lab operator's work (no agent capability).

Manual references (references/manuals/mt_quantos_q2_xpe206dr/, internal; page numbers of the operating instructions 11781172K):
target quantity in mg and tolerance in % p.22; head un/lock and removal p.22; head RFID data p.28; preparing and inserting a
head p.29; sample container on the weighing pan and door automation p.33; GMP / USP tolerance note p.34; target quantities
and tolerances p.95; XPE206DR readability (0.01 mg, fine range 0.005 mg): powder dosing brochure 30084648A p.11.
"""
import math
import re

from .. import units
from ..base import ClassAdapter, finite, short
from ..errors import invalid, refused, unsupported
from ..jobs import OperationJob
from ..verbs import Arg, Opt, Verb, save_to_opt
from .balance import BalanceAdapter

REF = ('Quantos operating instructions 11781172K: target quantity and tolerance p.22, head un/lock p.22, head RFID data p.28, '
       'inserting a head p.29, container on the pan and doors p.33, target quantities and tolerances p.95')
HEAD_MODES = ['show', 'select', 'lock', 'unlock', 'raise', 'lower', 'remove', 'install']
VIAL_MODES = ['show', 'select', 'place', 'remove']
PARTS = ['front', 'side']


class DosingAdapter(BalanceAdapter):
    CLASS = 'dosing'
    TITLE = 'automated powder dosing'
    ABOUT = ('Powder dosing systems: dose a target quantity (mg) into the vial on the balance as a job, with the dosing head, the '
             'vial position, the doors, target, tolerance and tare handled in one verb; the dosing head and the vial as objects; '
             'the result with the deviation and whether it is within tolerance.')
    STANDARDS = ('Quantos command set (dosing head, target, tolerance, start dosing, result); GMP 1 % / USP 0.1 % dosing tolerance '
                 'practice as the manual quotes it')
    UNIT_KINDS = ('mass', 'fraction', 'duration')
    PATH_GROUPS = (('dose', 'stop', 'result'),)
    ALWAYS = ('status',)
    CODES = {
        'BUSY': ('BUSY', '{inst} is dosing.', 'wait for the job (`chemlab status`) or stop it: `chemlab run {inst} stop`.'),
        'SOURCE_DEPLETED': ('SOURCE_DEPLETED', 'the powder in the dosing head of {inst} ran out before the target was reached; the powder '
                            'already dosed stays in the vial.', 'ask the lab operator to refill or replace the dosing head, then dose '
                            'the rest (see `chemlab run {inst} result`).'),
        'UNSUPPORTED': ('NOT_SET_UP', 'that part of {inst} is not set up in this lab (no door geometry or no dosing configuration).',
                        'ask the lab operator to set the instrument up.'),
    }

    # ------------------------------------------------------------------ helpers
    def readability_kg(self):
        return float(self.param('readability_kg', 1e-8))

    def mg(self, kg, sign=False):
        """kg -> 'x.xx mg' to the balance readability."""
        if not finite(kg):
            return 'n/a'
        d = self.readability_kg() * 1e6
        dec = max(0, int(round(-math.log10(d)))) if d > 0 else 2
        return ('%+.*f mg' if sign else '%.*f mg') % (dec, kg * 1e6)

    def running(self, values):
        return self.val(values, 'status') == 'running'

    def my_dosing(self, ctx):
        """True when the running dosing is one this caller started through chemlab (only its starter may abort it)."""
        from ..jobs import latest
        job = latest(ctx.core, ctx.caller, self.instrument_id)
        return job is not None and getattr(job, 'cap', None) == (self.vmap('dose') or {}).get('cap') and job.state not in (
            'succeeded', 'failed', 'cancelled')

    def need_idle(self, ctx, doing):
        if not self.running(ctx):
            ctx.checks.append('not dosing')
            return
        inst = self.instrument_id
        fix = ('wait for your job (`chemlab status`) or stop it: `chemlab run %s stop`.' % inst if self.my_dosing(ctx) else
               'it was started by another user (only they can abort it): wait until it has ended (`chemlab run %s status`, '
               '`chemlab wait 60`), then retry.' % inst)
        ctx.fail(refused('BUSY', '%s is dosing, so it cannot %s now.' % (inst, doing), fix))

    def need_configured(self, ctx):
        s = self.slot('configured')
        if s:
            ctx.check(bool(ctx.v(s)), 'NOT_SET_UP', '%s is not set up for dosing in this lab (the lab has not configured the dosing '
                      'unit).' % self.instrument_id, 'ask the lab operator to set the dosing unit up; nothing to do from chemlab.',
                      label='dosing unit set up')

    def front_open_m(self):
        p = self.param('front_open_m')
        if p is None:                       # the contract's door_kinematics.authored_travel (0.5495 m since asset 1.2.2: arc length
            c = self.contract_param('authored_travel')      # of the top-hung door's hinge pin, lever fully lifted)
            if not c:
                raise ValueError('%s: no front-door travel in the contract (door_kinematics.authored_travel)' % self.instrument_id)
            p = c['value']
        return float(p)

    def need_front_door(self, ctx):
        s = self.slot('front_door')
        if s:
            ctx.check(ctx.v(s) is not None, 'NOT_SET_UP', 'the front door of %s is not set up in this lab (no door geometry).'
                      % self.instrument_id, 'use the side doors (`open side`), or ask the lab operator to set the front door up.',
                      label='front door set up')

    def close_doors(self, ctx, why):
        """Close the front and side doors where open (the instrument does this itself when door automation is on, p.33)."""
        m = self.vmap('open') or {}
        parts = m.get('parts') or {}
        for name in PARTS:
            spec = parts.get(name)
            slot = self.slot('%s_door' % name)
            if spec and slot and finite(ctx.v(slot)) and ctx.v(slot) > 1e-9:
                ctx.call(spec['cap'], {spec['arg']: spec['close']}, 'close the %s door (%s)' % (name, why),
                         readback=lambda v, s=slot: None if not (finite(ctx.v(s)) and ctx.v(s) > 1e-9) else '%s = %s' % (s, ctx.v(s)))

    def select_head(self, ctx, want=None):
        m = self.vmap('head') or {}
        loaded, sel = ctx.v(self.slot('head_loaded')), ctx.v(self.slot('head_selected'))
        ctx.check(loaded is not None, 'NO_HEAD', 'no dosing head is inserted in %s.' % self.instrument_id,
                  'the lab operator inserts the dosing head (manual p.29); then `chemlab run %s head select ID`.' % self.instrument_id,
                  label='dosing head inserted')
        head = want or loaded
        ctx.check(head == loaded, 'NO_SUCH_HEAD', 'the dosing head in %s is %s, not %s.' % (self.instrument_id, loaded, head),
                  'use --head %s, or ask the lab operator to insert head %s.' % (loaded, head), label='that head inserted')
        if sel != head:
            ctx.call(m['select_cap'], {m.get('select_arg', 'head_id'): head}, 'select dosing head %s' % head,
                     readback=lambda v: None if ctx.v(self.slot('head_selected')) == head else 'head.selected_id = %s' % ctx.v(self.slot('head_selected')))
        return head

    def select_vial(self, ctx, pos=None):
        m = self.vmap('vial') or {}
        ctx.check(ctx.v(self.slot('vial_loaded')) is not None, 'NO_VIAL', 'there is no vial in %s.' % self.instrument_id,
                  'put one in: `chemlab run %s vial place` opens the front door and the lab operator places the vial on the pan '
                  '(manual p.33).' % self.instrument_id, label='vial in place')
        pos = pos or self.param('vial_position', 'balance_pan')
        if ctx.v(self.slot('vial_position')) != pos:
            ctx.call(m['cap'], {m.get('arg', 'position_id'): pos}, 'select the vial at %s' % pos)
        return pos

    def explain_refusal(self, ctx, cap, args, response):
        code = (response.get('error') or {}).get('code')
        if code == 'ACCESS_DENIED' and cap == (self.vmap('stop') or {}).get('cap'):
            return refused('NOT_YOURS', 'the dosing running on %s was started by another user; only they can abort it.' % self.instrument_id,
                           'wait until it has ended (`chemlab run %s status`, `chemlab wait 60`).' % self.instrument_id)
        if code == 'PRECONDITION_FAILED' and response.get('status') == 'rejected':
            ctx.refresh()
            inst = self.instrument_id
            if not ctx.v(self.slot('power')):
                return refused('POWER_OFF', '%s is switched off.' % inst, 'switch it on: `chemlab run %s power on`.' % inst)
            vm = self.vmap('vial') or {}
            if cap == vm.get('cap'):
                return refused('NOT_AT_POSITION', 'the vial in %s is not at position %s.' % (inst, args.get(vm.get('arg', 'position_id'))),
                               'give the position it is at (--vial POS; the usual one is balance_pan), or ask the lab operator.')
            if self.doors_open(ctx):
                return refused('DOOR_OPEN', 'a draft-shield door of %s is open.' % inst, 'close it: `chemlab run %s close`.' % inst)
        return BalanceAdapter.explain_refusal(self, ctx, cap, args, response)

    # ------------------------------------------------------------------ summary
    def summary(self, values):
        on = self.val(values, 'power')
        if not on:
            return ['power off'], {'power': False}
        head, sel, locked = self.val(values, 'head_loaded'), self.val(values, 'head_selected'), self.val(values, 'head_locked')
        vial, pos = self.val(values, 'vial_loaded'), self.val(values, 'vial_position')
        st, phase = self.val(values, 'status'), self.val(values, 'phase')
        target, tol, act, err = (self.val(values, k) for k in ('target', 'tolerance', 'actual', 'error'))
        net = self.val(values, 'net')
        door = self.doors_open(values)
        lines = ['dosing %s%s · target %s ± %s · dosed %s%s' % (st, ' (%s)' % phase if phase and st == 'running' else '', self.mg(target),
                                                               self.mg(tol), self.mg(act), (' (deviation %s)' % self.mg(err, True)) if finite(err) else ''),
                 'head %s%s%s · vial %s%s · doors %s · tared %s · net %s' % (
                     head or 'none', (' selected' if sel and sel == head else ' not selected') if head else '', ' locked' if locked else '',
                     vial or 'none', (' at %s' % pos) if pos else '', 'open' if door else 'closed',
                     'yes' if self.val(values, 'tared') else 'no', self.mg(net))]
        js = {'power': True, 'status': st, 'phase': phase, 'target_kg': target, 'tolerance_kg': tol, 'dosed_kg': act, 'error_kg': err,
              'head': head, 'head_selected': sel, 'head_locked': locked, 'vial': vial, 'vial_position': pos, 'doors_open': door,
              'tared': self.val(values, 'tared'), 'net_kg': net, 'readability_kg': self.readability_kg()}
        return lines, js

    # ------------------------------------------------------------------ verbs
    def build_verbs(self):
        part = [Arg('PART', 'front (the front door, default) or side (the side doors)', required=False, choices=PARTS)]
        return {
            'dose': Verb('dose', 'dose a target quantity of powder into the vial (returns job-N; --wait waits)', self.h_dose,
                         positionals=[Arg('AMOUNT', 'target quantity, e.g. 10mg or 0.5g (a bare number is in mg, as on the '
                                          'instrument; 1 µg to 5 g)')],
                         options={'tolerance': Opt('allowed deviation: 1% (of the target) or a mass such as 0.1mg (default: the '
                                                   'instrument\'s current tolerance, else 1 %)', metavar='TOL'),
                                  'head': Opt('dosing head id (default: the inserted head)', metavar='ID'),
                                  'vial': Opt('vial position (default balance_pan)', metavar='POS'),
                                  'sample-id': Opt('sample id for the record (max. 20 characters)', metavar='ID'),
                                  'wait': Opt('wait until the dosing has ended (lab time moves)', kind='flag'),
                                  'max': Opt('longest wait with --wait (default 30min)', kind='duration', metavar='TIME')},
                         long_action=True,
                         preconditions=['power on', 'dosing unit set up by the lab', 'a dosing head inserted', 'a vial in place',
                                        'not dosing'],
                         errors=['OUT_OF_RANGE, BAD_TOLERANCE (usage)', 'POWER_OFF, NOT_SET_UP, NO_HEAD, NO_SUCH_HEAD, NO_VIAL, BUSY, '
                                 'NOT_AT_POSITION (refused)'],
                         example='chemlab run INST dose 10mg --tolerance 1% --wait', reference=REF,
                         details='Steps (each only where needed): select the head, select the vial position, close the doors, set '
                                 'the target and the tolerance, tare the vial, start dosing. A previous dosing is never counted: the '
                                 'vial is tared again before every dose.'),
            'head': Verb('head', 'the dosing head: show its data, select, lock / unlock, raise / lower, release it for removal', self.h_head,
                         positionals=[Arg('MODE', 'show (default) | select | lock | unlock | raise | lower | remove', required=False,
                                          choices=HEAD_MODES),
                                      Arg('ID', 'head id (for select)', required=False)],
                         preconditions=['power on', 'not dosing (except show)'], errors=['NO_HEAD (refused)', 'NOT_SUPPORTED (install)'],
                         example='chemlab run INST head show', reference=REF),
            'vial': Verb('vial', 'the receiving vial: show, select its position, or open the door to place / remove it', self.h_vial,
                         positionals=[Arg('MODE', 'show (default) | select | place | remove', required=False, choices=VIAL_MODES),
                                      Arg('POS', 'position (for select; usually balance_pan)', required=False)],
                         preconditions=['power on', 'not dosing (except show)'], errors=['NO_VIAL, NOT_AT_POSITION, BUSY (refused)'],
                         example='chemlab run INST vial place', reference=REF),
            'open': Verb('open', 'open the front door (default: its lever lifted fully, to the contract door travel) or the side doors of the draft shield', self.h_door('open'),
                         positionals=part, preconditions=['not dosing'], errors=['BUSY, NOT_SET_UP (refused)'],
                         example='chemlab run INST open front', reference=REF),
            'close': Verb('close', 'close the front door (default) or the side doors of the draft shield', self.h_door('close'),
                          positionals=part, preconditions=['not dosing'], errors=['BUSY, NOT_SET_UP (refused)'],
                          example='chemlab run INST close', reference=REF),
            'tare': Verb('tare', 'tare the vial on the pan (the doors must be closed)', self.h_tare_vial,
                         preconditions=['power on', 'not dosing', 'doors closed', 'a vial in place'],
                         errors=['POWER_OFF, BUSY, DOOR_OPEN, NO_VIAL (refused)'], example='chemlab run INST tare', reference=REF),
            'result': Verb('result', 'the last dosing: actual quantity, target, deviation, within tolerance, time', self.h_result,
                           readonly=True, options={'save-to': save_to_opt()}, errors=['NO_RESULT (refused)'],
                           example='chemlab run INST result --save-to dose.json', reference=REF),
            'stop': Verb('stop', 'abort the running dosing (the powder already dosed stays in the vial)', self.h_stop,
                         preconditions=['dosing', 'started by you (the instrument refuses another user\'s abort)'],
                         errors=['NOT_RUNNING, NOT_YOURS (refused)'], example='chemlab run INST stop', reference=REF),
        }

    def tolerance_args(self, ctx, text, target_kg):
        """--tolerance text -> (capability, arguments, shown) or None (keep the instrument's)."""
        m = self.vmap('dose')
        t = (text or '').strip().replace(' ', '')
        if t.endswith('%'):
            pct = units.parse(t, 'fraction', what='--tolerance') * 100.0
            lo, hi = self.arg_range(m['tol_pct_cap'], m.get('tol_pct_arg', 'percent'))
            if (lo is not None and pct < lo) or (hi is not None and pct > hi):
                ctx.fail(invalid('BAD_TOLERANCE', '--tolerance %s is outside %s..%s %%.' % (text, lo, hi), 'give e.g. --tolerance 1%.'))
            return m['tol_pct_cap'], {m.get('tol_pct_arg', 'percent'): units.sig(pct)}, '%g %%' % pct
        kg = units.parse(t, 'mass', what='--tolerance')
        if kg <= 0 or kg > target_kg:
            ctx.fail(invalid('BAD_TOLERANCE', '--tolerance %s must be more than 0 and not more than the target.' % text,
                             'give e.g. --tolerance 1% or --tolerance 0.1mg.'))
        return m['tol_cap'], {m.get('tol_arg', 'tolerance_kg'): units.sig(kg)}, self.mg(kg)

    def h_dose(self, ctx, p):
        m = self.vmap('dose')
        inst = self.instrument_id
        t = p.raw['AMOUNT'].strip()
        target = units.parse(t + 'mg' if re.fullmatch(r'[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?', t) else t, 'mass', what='AMOUNT')
        lo, hi = self.arg_range_si(m['target_cap'], m.get('target_arg', 'target_mass_kg'))
        if (lo is not None and target < lo) or (hi is not None and target > hi):
            ctx.fail(invalid('OUT_OF_RANGE', 'the target %s is outside what %s doses (%s to %s).' % (self.mg(target), inst, self.mg(lo), self.mg(hi)),
                             'give a target from %s to %s.' % (self.mg(lo), self.mg(hi))))
        sample = p.get('sample-id')
        if sample is not None and not (1 <= len(sample) <= 20):
            ctx.fail(invalid('BAD_VALUE', '--sample-id takes 1 to 20 characters.', 'shorten it.'))
        tol_text = p.get('tolerance')
        tol = self.tolerance_args(ctx, tol_text, target) if tol_text else None
        self.need_power(ctx)
        self.need_configured(ctx)
        self.need_idle(ctx, 'start a new dosing')
        head = self.select_head(ctx, p.get('head'))
        pos = self.select_vial(ctx, p.get('vial'))
        self.close_doors(ctx, 'before taring')
        if ctx.v(self.slot('target')) is None or abs(ctx.v(self.slot('target')) - target) > 1e-15:
            ctx.call(m['target_cap'], {m.get('target_arg', 'target_mass_kg'): units.sig(target)}, 'set the target to %s' % self.mg(target))
        if tol is None and ctx.v(self.slot('tolerance')) is None:
            pct = float(self.param('default_tolerance_percent', 1.0))
            tol = (m['tol_pct_cap'], {m.get('tol_pct_arg', 'percent'): pct}, '%g %% (default)' % pct)
        if tol:
            ctx.call(tol[0], tol[1], 'set the tolerance to %s' % tol[2])
        if sample is not None and m.get('ids_cap'):
            ctx.call(m['ids_cap'], {'user_id': ctx.v(self.slot('user_id')) if self.slot('user_id') else None, 'sample_id': sample},
                     'set the sample id %s' % sample)
        ctx.call(m['tare_cap'], {}, 'tare the vial', readback=lambda v: None if ctx.v(self.slot('tared')) else 'balance.tared = false')
        r = ctx.call(m['cap'], {}, 'start dosing %s' % self.mg(target))
        tol_now = ctx.v(self.slot('tolerance')) if not ctx.dry_run else None
        setup = 'head %s, vial at %s, target %s, tolerance %s' % (head, pos, self.mg(target), tol[2] if tol else self.mg(tol_now))
        if ctx.dry_run:
            return {'text': '', 'json': {'head': head, 'vial_position': pos, 'target_kg': target}}
        job = OperationJob(ctx.core, self, ctx.req, '%s dose %s' % (inst, self.mg(target)), m['cap'], r.get('operation_id'),
                           max_s=float(self.param('max_dose_s', 1800.0))) if r.get('operation_id') else None
        js = {'job': job.handle if job else None, 'head': head, 'vial_position': pos, 'target_kg': target, 'target_mg': target * 1e6,
              'tolerance_kg': tol_now}
        if not job:
            return {'text': 'ok: %s started dosing (%s)\n' % (inst, setup), 'json': js}
        if p.get('wait'):
            w = job.wait_reply(ctx.core, ctx.req, p.get('max') or float(self.param('max_dose_s', 1800.0)))
            ctx.refresh()
            res = self.result_view(ctx)
            js.update({'waited_s': w['json'].get('waited_s'), 'status': w['json'].get('status'), 'result': res['json']})
            return {'text': 'dosing on %s (%s): %s\n%s' % (inst, setup, w['json'].get('status'), res['text']), 'json': js}
        return {'text': 'started %s: dosing on %s (%s)\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s; then '
                        '`chemlab run %s result`\n' % (job.handle, inst, setup, job.handle, job.handle, job.handle, inst), 'json': js}

    def result_view(self, ctx, rec=None):
        last = ctx.v(self.slot('last')) if self.slot('last') else None
        rec = dict(rec or {})
        st = rec.get('dosing.status') or ctx.v(self.slot('status'))
        act = rec.get('measurement.actual_dosed_kg', ctx.v(self.slot('actual')))
        target = rec.get('dosing.target_mass_kg', ctx.v(self.slot('target')))
        err = rec.get('measurement.error_kg', ctx.v(self.slot('error')))
        tol = rec.get('dosing.tolerance_kg', ctx.v(self.slot('tolerance')))
        out = rec.get('measurement.out_of_tolerance', ctx.v(self.slot('out_of_tolerance')))
        el = rec.get('dosing.elapsed_s', ctx.v(self.slot('elapsed')))
        within = None if out is None else not out
        pct = (100.0 * err / target) if finite(err) and finite(target) and target else None
        text = ('  status %s · dosed %s of %s · deviation %s%s · tolerance ± %s · %s · %s\n' % (
            st, self.mg(act), self.mg(target), self.mg(err, True), (' (%+.2f %%)' % pct) if pct is not None else '', self.mg(tol),
            'within tolerance' if within else ('OUT of tolerance' if within is False else 'tolerance n/a'), units.fmt_s(el or 0)))
        js = {'status': st, 'dosed_kg': act, 'dosed_mg': None if not finite(act) else act * 1e6, 'target_kg': target, 'error_kg': err,
              'error_percent': pct, 'tolerance_kg': tol, 'within_tolerance': within, 'elapsed_s': el, 'record': last}
        return {'text': text, 'json': js}

    def h_result(self, ctx, p):
        m = self.vmap('result')
        r = ctx.call(m['cap'], {}, 'read the dosing result', query=True)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        rec = r.get('result') if isinstance(r.get('result'), dict) else {}
        ctx.check(rec.get('dosing.status', ctx.v(self.slot('status'))) not in (None, 'idle') or ctx.v(self.slot('last')),
                  'NO_RESULT', '%s has not dosed yet.' % self.instrument_id, 'dose first: `chemlab run %s dose 10mg`.' % self.instrument_id,
                  label='a dosing done')
        res = self.result_view(ctx, rec)
        return {'text': '%s dosing result:\n%s' % (self.instrument_id, res['text']), 'json': res['json']}

    def h_stop(self, ctx, p):
        m = self.vmap('stop')
        ctx.check(self.running(ctx), 'NOT_RUNNING', '%s is not dosing.' % self.instrument_id, 'nothing to stop; see `chemlab run %s result`.'
                  % self.instrument_id, label='dosing')
        ctx.call(m['cap'], {}, 'abort the dosing')
        return {'text': 'ok: dosing on %s aborted (dosed so far: %s)\n' % (self.instrument_id, self.mg(ctx.v(self.slot('actual')))),
                'json': {'dosed_kg': ctx.v(self.slot('actual')), 'status': ctx.v(self.slot('status'))}}

    def h_tare_vial(self, ctx, p):
        m = self.vmap('tare')
        self.need_power(ctx)
        self.need_idle(ctx, 'tare')
        ctx.check(not self.doors_open(ctx), 'DOOR_OPEN', 'a draft-shield door of %s is open; it tares only with the doors closed.'
                  % self.instrument_id, 'close it: `chemlab run %s close` (and `close side`), then tare.' % self.instrument_id,
                  label='doors closed')
        self.select_vial(ctx)
        ctx.call(m['cap'], {}, 'tare the vial', readback=lambda v: None if ctx.v(self.slot('tared')) else 'balance.tared = false')
        return {'text': 'ok: %s tared (net %s)\n' % (self.instrument_id, self.mg(ctx.v(self.slot('net')))),
                'json': {'tared': ctx.v(self.slot('tared')), 'net_kg': ctx.v(self.slot('net'))}}

    def h_door(self, action):
        def h(ctx, p):
            name = p.pos[0] if p.pos else 'front'
            spec = ((self.vmap('open') or {}).get('parts') or {}).get(name)
            if not spec:
                ctx.fail(unsupported('NO_PART', '%s has no %s door.' % (self.instrument_id, name), 'parts: %s.' % ', '.join(PARTS)))
            self.need_idle(ctx, '%s a door' % action)
            value = spec[action] if name != 'front' or action == 'close' else self.front_open_m()
            slot = self.slot('%s_door' % name)
            if name == 'front':
                self.need_front_door(ctx)
            ctx.call(spec['cap'], {spec['arg']: value}, '%s the %s door' % (action, name))
            note = ['opening a door cancels the tare; `dose` tares again with the doors closed.'] if action == 'open' else []
            return {'text': 'ok: %s %s door %s\n' % (self.instrument_id, name, 'opened' if action == 'open' else 'closed'),
                    'json': {'part': name, 'action': action, 'value': ctx.v(slot) if slot else None}, 'notes': note}
        return h

    def h_head(self, ctx, p):
        m = self.vmap('head')
        inst = self.instrument_id
        mode = p.pos[0] if p.pos else 'show'
        if mode == 'install':
            ctx.fail(unsupported('NOT_SUPPORTED', 'inserting a dosing head is the lab operator\'s work on %s (no instrument command; '
                                 'manual p.29).' % inst, 'ask the lab operator; then `chemlab run %s head select ID`.' % inst))
        loaded = ctx.v(self.slot('head_loaded'))
        if mode == 'show':
            data = ctx.v(self.slot('head_data')) if self.slot('head_data') else None
            js = {'head': loaded, 'selected': ctx.v(self.slot('head_selected')), 'locked': ctx.v(self.slot('head_locked')),
                  'position': ctx.v(self.slot('head_position')), 'data': data}
            text = 'dosing head of %s: %s%s%s%s\n' % (inst, loaded or 'none inserted', ' (selected)' if loaded and js['selected'] == loaded else '',
                                                    ' locked' if js['locked'] else '', (' · %s' % js['position']) if js['position'] else '')
            if isinstance(data, dict) and data:
                text += '  RFID data: %s\n' % ', '.join('%s=%s' % (k, short(v, 30)) for k, v in data.items() if v is not None)
            return {'text': text, 'json': js}
        self.need_power(ctx)
        self.need_idle(ctx, 'change the dosing head')
        if mode == 'select':
            head = self.select_head(ctx, p.pos[1] if len(p.pos) > 1 else None)
            return {'text': 'ok: dosing head %s selected on %s\n' % (head, inst), 'json': {'head': head}}
        ctx.check(loaded is not None, 'NO_HEAD', 'no dosing head is inserted in %s.' % inst, 'the lab operator inserts it (manual p.29).',
                  label='dosing head inserted')
        if mode in ('lock', 'unlock', 'remove'):
            want = mode == 'lock'
            if bool(ctx.v(self.slot('head_locked'))) != want:
                ctx.call(m['lock_cap'], {m.get('lock_arg', 'locked'): want}, '%s the dosing head' % ('lock' if want else 'unlock'))
            if mode == 'remove':
                return {'text': 'ok: dosing head %s of %s is released; the lab operator can take it out now (manual p.22)\n' % (loaded, inst),
                        'json': {'head': loaded, 'locked': False}}
            return {'text': 'ok: dosing head %s %s\n' % (loaded, 'locked' if want else 'unlocked'), 'json': {'head': loaded, 'locked': want}}
        pos = 'raised' if mode == 'raise' else 'lowered'
        ctx.call(m['height_cap'], {m.get('height_arg', 'position'): pos}, '%s the dosing head' % mode)
        return {'text': 'ok: dosing head of %s %s\n' % (inst, pos), 'json': {'position': pos}}

    def h_vial(self, ctx, p):
        inst = self.instrument_id
        mode = p.pos[0] if p.pos else 'show'
        vial, pos = ctx.v(self.slot('vial_loaded')), ctx.v(self.slot('vial_position'))
        if mode == 'show':
            js = {'vial': vial, 'position': pos, 'tared': ctx.v(self.slot('tared')), 'net_kg': ctx.v(self.slot('net'))}
            return {'text': 'vial of %s: %s%s · tared %s · net %s\n' % (inst, vial or 'none', (' at %s' % pos) if pos else '',
                                                                       'yes' if js['tared'] else 'no', self.mg(js['net_kg'])), 'json': js}
        self.need_power(ctx)
        self.need_idle(ctx, '%s the vial' % mode)
        if mode == 'select':
            where = self.select_vial(ctx, p.pos[1] if len(p.pos) > 1 else None)
            return {'text': 'ok: vial at %s selected on %s\n' % (where, inst), 'json': {'vial': vial, 'position': where}}
        if mode == 'remove':
            ctx.check(vial is not None, 'NO_VIAL', 'there is no vial in %s.' % inst, 'nothing to remove.', label='vial in place')
        spec = ((self.vmap('open') or {}).get('parts') or {}).get('front')
        self.need_front_door(ctx)
        if spec and not (finite(ctx.v(self.slot('front_door'))) and ctx.v(self.slot('front_door')) > 1e-9):
            ctx.call(spec['cap'], {spec['arg']: self.front_open_m()}, 'open the front door')
        what = 'put the vial on the weighing pan' if mode == 'place' else 'take the vial %s out' % vial
        return {'text': 'ok: the front door of %s is open; the lab operator can %s now (manual p.33). Then `chemlab run %s close`.\n' % (
            inst, what, inst), 'json': {'vial': vial, 'front_door_open': True}}

    _ = (ClassAdapter,)
