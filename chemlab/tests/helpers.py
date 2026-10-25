import json
import os
import shlex
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from chemlab.backend import FastSimFlexBackend          # noqa: E402
from chemlab.core import CoreService                    # noqa: E402
from chemlab.flex_tasks import _proto_fill, deck        # noqa: E402
from chemlab.flex_verbs import FlexInstrument           # noqa: E402

CONTENTS = {'reservoir:A1': ('buffer', 10000), 'reservoir:A3': ('buffer', 400), 'tuberack:A1': ('dye', 1000), 'plate:H12': ('buffer', 350)}


def make_core(contents=None, power=True, faults=None, protocols=(), lease_s=120.0, log_path=None, labware_override=None):
    d = deck(contents or CONTENTS, power=power)
    if labware_override:
        for lw in d['labware']:
            lw.update(labware_override.get(lw['name'], {}))
    backend = FastSimFlexBackend(d, faults=faults, protocols=protocols)
    return backend, CoreService(backend, FlexInstrument, lease_s=lease_s, log_path=log_path)


def run(core, cmd, caller='agent'):
    return core.handle(['chemlab'] + shlex.split(cmd), caller)


def run_json(core, cmd, caller='agent'):
    r = run(core, cmd + ' --json', caller)
    return r.exit_code, json.loads(r.out)


def snapshot(backend):
    s = backend.evaluator_state()
    s.pop('time_s', None)
    return json.dumps(s, sort_keys=True)


PLATE_PREP = _proto_fill('plate_prep', 'Plate prep', 'tiprack1000_H12', 'reservoir_A1', ['plate_C%d' % i for i in range(1, 4)], None, 60, params=True)
