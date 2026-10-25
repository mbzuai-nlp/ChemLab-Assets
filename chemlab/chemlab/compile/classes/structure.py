"""Structure class ("structure"): X-ray diffraction and scattering, photoelectron spectroscopy and ellipsometry - powder XRD
(Rigaku MiniFlex 600), single-crystal XRD (XtaLAB Synergy-R), SAXS (Anton Paar SAXSpace), XPS (Kratos AXIS Supra+) and
spectroscopic ellipsometry (J.A. Woollam alpha-SE).  Members and mappings: configs/adapters/structure.json.  The step engine is
the imaging class's SampleAdapter (classes/imaging.py): composite verbs are step lists in the fragment.

Verbs (every one also takes --dry-run):

    load SAMPLE [--holder H] [--at X,Y] [--magazine N]   mount a sample (sample slot / crystal on the goniometer / capillary holder /
                                            XPS holder through the load lock and transfer to the stage / wafer on the stage)
    unload [SAMPLE]                         take it out again
    source on|off [--kv V] [--ma I]         X-ray generator (or the XPS Al Ka source)
    vacuum on|off                           flight-path / load-lock vacuum, or the sample vacuum chuck (ellipsometer)
    align                                   centre the crystal / auto-align the capillary / XPS auto height
    scan [KEY=VALUE ...]                    configure and start the measurement: job-N (or --wait)
                                            PXRD from=10deg to=80deg step=0.02deg time=0.5s; SC-XRD exposure=1s width=0.5deg
                                            distance=45mm (pre-experiment) or strategy=ID (full data set); SAXS exposure=600s frames=3;
                                            XPS region=survey | NAME:FROM:TO:STEP:DWELL[:SWEEPS] (binding energies in eV) mode=scanned;
                                            ellipsometry mode=standard align=standard model=ID
    result [ID] [--save-to FILE]            the record (default: the last one)
    analyze [ID] [KEY=VALUE ...]            index / strategy / reduce / identify (SC-XRD), reduce (SAXS), quantify (XPS), fit (ellipsometry)
    status                                  the class view

KEY=VALUE settings take units where they are quantities (deg / rad, s / min, mm / nm, kV, mA); other values are parsed by the
argument's type.  Long actions return job-N (chemlab status|wait|cancel job-N), or block with --wait.

Vendor-neutral terms: CIF / ICDD (diffraction), USP <941> (XRPD), ISO 15472 and ISO 18115 (XPS energy scale and vocabulary),
canSAS / NXcanSAS (SAXS); there is no command standard for these instruments.
"""
from .. import units
from ..errors import invalid
from ..verbs import Arg, Opt, Verb, save_to_opt
from ..base import short
from .imaging import ON_OFF, Q, SampleAdapter, parse_q, wait_opts


class StructureAdapter(SampleAdapter):
    CLASS = 'structure'
    TITLE = 'diffraction, scattering, XPS and ellipsometry'
    ABOUT = ('Instruments that record a diffraction pattern, a scattering curve or a surface spectrum: mount the sample, switch the '
             'X-ray source, align, scan (job-N), read the record and analyse it; the same words on every member.')
    STANDARDS = ('CIF (IUCr) and ICDD PDF terms; USP <941> X-ray powder diffraction; ISO 15472 / ISO 18115 (XPS); canSAS / NXcanSAS '
                 '(SAXS); no command standard exists for these instruments')
    UNIT_KINDS = ('angle', 'duration', 'length', 'voltage', 'current')
    PATH_GROUPS = (('scan', 'result'),)
    CODES = {
        'POWER_OFF': ('POWER_OFF', '{inst} is switched off.', 'switch it on: `chemlab run {inst} power on`.'),
        'NOT_READY': ('NOT_READY', '{inst} is not ready (still starting up, or busy).', 'wait a little (`chemlab wait 30`) and retry; see `chemlab run {inst} status`.'),
        'PLANT_REQUIRED': ('NOT_SET_UP', '{inst} is not set up in this lab (no environment declared by the lab).', 'ask the lab operator.'),
        'UNKNOWN_RECORD': ('NO_SUCH_RECORD', '{inst} has no record of that id for you.', 'see your records: `chemlab call {inst} records.list`.'),
        'MOTION_IN_PROGRESS': ('BUSY', 'a stage or arm of {inst} is still moving.', 'wait a few seconds (`chemlab wait 5`) and retry.'),
        'ACQUISITION_RUNNING': ('BUSY', '{inst} is acquiring now.', 'wait for it (`chemlab status`), then retry.'),
        'TRANSFER_RUNNING': ('BUSY', 'a sample transfer of {inst} is running.', 'wait for it (`chemlab status`), then retry.'),
        'KEY_OFF': ('KEY_OFF', 'the X-ray key of {inst} is off.', '`chemlab run {inst} source on` turns it as part of switching on.'),
        'NOT_ARMED': ('NOT_ARMED', 'the safety circuit of {inst} is open (a door open, or Doors OK not pressed).', '`chemlab run {inst} source on` closes the doors first.'),
        'XRAY_NOT_STABLE': ('SOURCE_NOT_READY', 'the X-rays of {inst} are not on and stable.', '`chemlab run {inst} source on --wait`.'),
        'VACUUM_NOT_READY': ('VACUUM_NOT_READY', 'the vacuum of {inst} is not ready.', '`chemlab run {inst} vacuum on --wait`.'),
        'DOOR_OPEN': ('DOOR_OPEN', 'a door of {inst} is open.', 'close it (load / unload close it when they are done).'),
        'NO_CRYSTAL': ('NO_SAMPLE', 'there is no crystal on the goniometer of {inst}.', '`chemlab run {inst} load SAMPLE`.'),
        'NO_SAMPLE': ('NO_SAMPLE', 'there is no sample in {inst}.', '`chemlab run {inst} load SAMPLE`.'),
        'UNKNOWN_SAMPLE': ('NO_SUCH_SAMPLE', '{inst} has no sample of that name on its bench.', 'see `chemlab read {inst} sample.bench_ids`.'),
    }
    QOPTS = {}
    MADE_BY = 'scan'
    KEY_VERBS = ('scan', 'analyze')

    def build_verbs(self):
        R = None                               # manual pages are per member: the 'manual:' notes of help INST VERB
        return {
            'load': Verb('load', 'mount a sample (load lock / transfer / goniometer as needed)', self.h_generic('load', 'loaded {sample}', lambda c, p: {'sample': p.pos[0]}),
                         positionals=[Arg('SAMPLE', 'the sample id (on the bench, or the sample holder the lab prepared)')],
                         options=dict({'holder': Opt('holder / cell type or holder id (member specific)', metavar='H'),
                                       'at': Opt('where on the holder / stage, X,Y (e.g. -20mm,0mm)', metavar='X,Y'),
                                       'magazine': Opt('load-lock magazine slot (XPS)', metavar='N')}, **wait_opts('1h')),
                         preconditions=['the sample is on the bench', 'the holder / stage is free'],
                         errors=['NO_SUCH_SAMPLE, OCCUPIED (refused)', 'NOT_REACHED (refused: a door, lock or transfer did not get there in time)'],
                         example='chemlab run INST load SAMPLE', reference=R),
            'unload': Verb('unload', 'take the sample out again', self.h_generic('unload', 'unloaded {sample}', self.env_unload),
                           positionals=[Arg('SAMPLE', 'the sample id (default: the one in the instrument)', required=False)], options=wait_opts('1h'),
                           example='chemlab run INST unload', reference=R),
            'source': Verb('source', 'switch the X-ray source on (and set kV / mA) or off', self.h_onoff('source', 'X-rays'),
                           positionals=[Arg('on|off', 'on or off', choices=ON_OFF)],
                           options=dict({'kv': Opt('tube voltage, e.g. 40kV', kind='voltage', metavar='V'),
                                         'ma': Opt('tube current (XPS: emission), e.g. 15mA', kind='current', metavar='I')}, **wait_opts('10min')),
                           preconditions=['doors closed / interlocks made', 'cooling water'], errors=['OUT_OF_RANGE (usage)', 'DOOR_OPEN, NOT_ARMED (refused)'],
                           example='chemlab run INST source on --kv 40kV --ma 15mA --wait', reference=R),
            'vacuum': Verb('vacuum', 'pump the flight path / load lock, or hold the sample by vacuum (on), or vent / release (off)',
                           self.h_onoff('vacuum', 'vacuum'), positionals=[Arg('on|off', 'on or off', choices=ON_OFF)], options=wait_opts('30min'),
                           example='chemlab run INST vacuum on --wait', reference=R),
            'align': Verb('align', 'centre / auto-align the sample in the beam (job-N, or --wait)', self.h_generic('align', 'alignment'),
                          options=wait_opts('10min'), long_action=True, example='chemlab run INST align --wait', reference=R),
            'scan': Verb('scan', 'configure and start the measurement: KEY=VALUE settings (job-N, or --wait)', self.h_keys('scan', 'measurement'),
                         variadic=('KEY=VALUE', 'a measurement setting'), options=wait_opts('4h'), long_action=True,
                         preconditions=['sample loaded', 'source on and ready', 'vacuum ready where needed'],
                         errors=['UNKNOWN_SETTING, OUT_OF_RANGE (usage)', 'SOURCE_NOT_READY, NO_SAMPLE (refused)'],
                         example='chemlab run INST scan --wait', reference=R),
            'result': Verb('result', 'the measurement record (default: your last one)', self.h_result, readonly=True,
                           positionals=[Arg('ID', 'record id (default: the last one)', required=False)], options={'save-to': save_to_opt()},
                           example='chemlab run INST result --save-to pattern.json'),
            'analyze': Verb('analyze', 'analyse a record: KEY=VALUE settings (index / reduce / quantify / fit ...)', self.h_keys('analyze', 'analysis', record=True),
                            positionals=[Arg('ID', 'record id (default: the last one)', required=False)], variadic=('KEY=VALUE', 'an analysis setting'),
                            options={'save-to': save_to_opt()}, errors=['UNKNOWN_SETTING (usage)', 'NO_RECORD (refused)'],
                            example='chemlab run INST analyze', reference=R),
        }

    def env_unload(self, ctx, p):
        if p.pos:
            return {'sample': p.pos[0]}
        s = self.slot('sample')
        v = self.slot_value(ctx, s) if s else None
        return {'sample': v} if v else {}

    def env_from(self, ctx, p, extra=None):
        env = SampleAdapter.env_from(self, ctx, p, extra)
        at = p.get('at') if ctx.verb.name == 'load' else None
        if at:
            parts = [x for x in at.replace(';', ',').split(',') if x.strip()]
            if len(parts) != 2:
                ctx.fail(invalid('BAD_POSITION', '--at %s is not X,Y.' % at, 'write it like --at -20mm,0mm.'))
            env['opt:at_x'], env['opt:at_y'] = Q(units.parse(parts[0], 'length', what='X'), 'length'), Q(units.parse(parts[1], 'length', what='Y'), 'length')
            env.pop('opt:at', None)
        return env

    def h_keys(self, verb, what, record=False):
        """scan / analyze: KEY=VALUE settings become $opt:KEY inputs (kinds from the mapping's "keys"); analyze takes a record ID."""
        def h(ctx, p):
            m = self.vmap(verb)
            kinds = m.get('keys') or {}
            extra = {}
            pos = list(p.pos)
            if record:
                rid = pos[0] if pos else None
                if rid is not None and '=' in rid:                   # `analyze what=index`: no ID given, the first item is a setting
                    p.extra.insert(0, rid)
                    rid = None
                if rid is None and m.get('last_slot'):
                    rid = self.slot_value(ctx, m['last_slot'])
                    if not rid:
                        ctx.fail(invalid('NO_RECORD', '%s has no record of yours to analyse yet.' % self.instrument_id,
                                         'measure first (`chemlab run %s scan`).' % self.instrument_id))
                extra['id'] = rid
            allowed = self.options_of(m) - set(ctx.verb.options)
            for item in p.extra:
                k, eq, v = item.partition('=')
                if not eq or not k:
                    ctx.fail(invalid('BAD_SETTING', '%r is not KEY=VALUE.' % item, 'see `chemlab help %s %s`.' % (self.instrument_id, verb)))
                if k not in allowed:
                    ctx.fail(invalid('UNKNOWN_SETTING', '%s on %s has no setting %r.' % (verb, self.instrument_id, k),
                                     'settings: %s (see `chemlab help %s %s`).' % (', '.join(sorted(allowed)) or 'none', self.instrument_id, verb)))
                if k in kinds:
                    extra['opt:' + k] = parse_q(v, tuple(kinds[k]), k)
                else:
                    extra['opt:' + k] = v
            p.extra = []
            env = self.env_from(ctx, p, extra)
            lines, js, mm = self.run_mapping(ctx, p, verb, env, what)
            if ctx.dry_run:
                return {'text': '', 'json': js}
            head = 'ok: %s %s' % (self.instrument_id, self.head_text(ctx, mm, js, what, env))
            res = js.get('result')
            body = []
            if verb == 'analyze' and isinstance(res, dict):
                for k, v in list(res.items())[:16]:
                    body.append('  %-22s %s' % (k, short(v, 150)))
            text = '\n'.join([head] + self.waited_lines(ctx) + lines + body + [self.now_line(ctx)])
            if mm.get('next'):
                text += '\nnext: ' + self.fill(ctx, mm['next'], env)
            return {'text': text + '\n', 'json': js}
        return h

    def apply_defaults(self, ctx, m, env):
        kinds = m.get('keys') or {}
        for k, text in (m.get('defaults') or {}).items():
            if k in kinds and 'opt:' + k not in env and not isinstance(text, list):
                env['opt:' + k] = parse_q(str(text), tuple(kinds[k]), k)
        SampleAdapter.apply_defaults(self, ctx, m, env)


_ = (Opt,)
