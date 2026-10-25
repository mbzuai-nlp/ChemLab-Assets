"""Balance class acceptance tasks (10): gold solutions use `chemlab run`; the checkers read the evaluator view.

World set-up is the trusted host's: a load on the pan is the pan.set_load world call on the OHAUS balances and the contact force
on the XPR weighing cell (what the scene gives the runtime in Matterix).
"""
from .common import run_suite

G = 9.80665
XPR, PX, SPX = 'mt_xpr204e', 'ohaus_px224e', 'ohaus_spx2201'


def place(lab, iid, kg):
    """Put a load of ``kg`` on the pan (host side).  ``lab``: a GenericLab or a LabBackend."""
    backend = getattr(lab, 'backend', lab)
    inst = backend.instruments[iid]
    if 'pan.set_load' in inst.contract['capabilities']:
        r = backend.evaluator_invoke(iid, 'pan.set_load', {'mass_kg': kg}, 'world-load-%s-%d' % (iid, int(backend.now())))
        assert r.get('status') == 'succeeded', r
    else:
        inst.host.flush()
        inst.host.host._instances[inst.host.instance_id]['host_force_N'] = kg * G


def world_call(lab, iid, cap, args):
    r = lab.backend.evaluator_invoke(iid, cap, args, 'world-%s-%s-%d' % (iid, cap, int(lab.backend.now() * 10)))
    assert r.get('status') == 'succeeded', (cap, r)
    return r


def settle(lab, s=30.0):
    lab.backend.advance(s)


# ------------------------------------------------------------------ worlds
def w_beaker_on_xpr(lab):
    place(lab, XPR, 0.0523)
    settle(lab)


def w_sample_on_px(lab):
    place(lab, PX, 0.0012345)
    settle(lab)


def w_door_open_residue(lab):
    world_call(lab, XPR, 'door.operate', {'side': 'left', 'action': 'open'})
    place(lab, XPR, 0.0000008)
    settle(lab)


def w_px_doors_open_beaker(lab):
    place(lab, PX, 0.030)
    world_call(lab, PX, 'doors.set', {'open_fraction': 1.0})
    settle(lab)


def w_xpr_off(lab):
    world_call(lab, XPR, 'power.set', {'enabled': False})
    settle(lab, 5)


def w_xpr_50g(lab):
    place(lab, XPR, 0.0500)
    settle(lab)


def w_two_samples(lab):
    place(lab, XPR, 0.0025)
    place(lab, PX, 0.0024)
    settle(lab)


def w_just_loaded(lab):
    place(lab, XPR, 0.0007531)
    lab.backend.advance(0.5)


def w_px_off_open(lab):
    world_call(lab, PX, 'doors.set', {'open_fraction': 1.0})
    world_call(lab, PX, 'power.set', {'on': False})
    settle(lab, 5)


# ------------------------------------------------------------------ checkers
def c_tare_beaker(c):
    e = c.ev(XPR)
    c.near('tare = beaker mass 52.3 g (±1 mg)', e['balance.tare_kg'], 0.0523, 1e-6)
    c.near('net reads 0 (±0.3 mg)', e['balance.net_kg'], 0.0, 3e-7)
    c.add('doors closed', e['door.left.opening_m'] < 1e-6 and e['door.right.opening_m'] < 1e-6)


def c_weigh_report(c):
    c.add('the balance was read (request log)', c.looked(PX), len(c.looked(PX)))
    c.near('mass_mg.txt = 1234.5 mg (±0.3 mg)', c.number_file('mass_mg.txt'), 1234.5, 0.3)
    c.near('balance not tared away (tare 0)', c.ev(PX)['weighing.tare_kg'], 0.0, 1e-9)


def c_unit_kg(c):
    c.equal('SPX2201 shows kg', c.ev(SPX)['weighing.unit'], 'kg')
    c.add('still on', c.ev(SPX)['power.on'])


def c_close_zero(c):
    e = c.ev(XPR)
    c.add('doors closed', e['door.left.opening_m'] < 1e-6 and e['door.right.opening_m'] < 1e-6, (e['door.left.opening_m'], e['door.right.opening_m']))
    c.near('net reads 0 after zero (±0.2 mg)', e['balance.net_kg'], 0.0, 2e-7)
    c.add('zero was set (zero point = the 0.8 mg residue)', abs(e.get('balance.zero_kg', 0.0) - 8e-7) <= 2e-7, e.get('balance.zero_kg'))
    c.near('no tare', e['balance.tare_kg'], 0.0, 1e-9)


def c_px_tare_closed(c):
    e = c.ev(PX)
    c.near('tare = beaker 30 g (±0.5 mg)', e['weighing.tare_kg'], 0.030, 5e-7)
    c.near('net reads 0', e['weighing.net_kg'], 0.0, 3e-7)
    c.near('doors closed', e['doors.open_fraction'], 0.0, 1e-9)


def c_xpr_on_mg(c):
    e = c.ev(XPR)
    c.add('XPR switched on', e['power.enabled'])
    c.equal('display unit mg', e['display.unit'], 'mg')


def c_zero_range(c):
    e = c.ev(XPR)
    c.near('tare = beaker 50 g (±1 mg)', e['balance.tare_kg'], 0.0500, 1e-6)
    c.near('net reads 0 (±0.3 mg)', e['balance.net_kg'], 0.0, 3e-7)


def c_heavier(c):
    c.add('both balances were read (request log)', c.looked(XPR) and c.looked(PX), (len(c.looked(XPR)), len(c.looked(PX))))
    c.equal('heavier.txt names the XPR', c.work_file('heavier.txt'), XPR)


def c_stable_mass(c):
    c.add('the balance was read (request log)', c.looked(XPR), len(c.looked(XPR)))
    c.near('mass_g.txt = 0.7531 g (±0.5 mg)', c.number_file('mass_g.txt'), 0.7531, 5e-4)


def c_px_ready(c):
    e = c.ev(PX)
    c.add('on', e['power.on'])
    c.near('doors closed', e['doors.open_fraction'], 0.0, 1e-9)
    c.equal('unit mg', e['weighing.unit'], 'mg')
    c.add('reads zero', e['weighing.at_zero'], e['weighing.net_kg'])


GOLD_B05 = r'''chemlab run ohaus_px224e tare --json > t1.json; rc=$?
jq -c '{ok, code: .error.code, fix: .error.fix}' t1.json
if [ $rc -eq 3 ] && jq -e '.error.code == "UNSTABLE"' t1.json >/dev/null; then
  chemlab run ohaus_px224e door close
  chemlab run ohaus_px224e tare --wait 30s
fi
chemlab run ohaus_px224e status'''

GOLD_B06 = r'''chemlab run mt_xpr204e unit mg; rc=$?
if [ $rc -eq 3 ]; then
  chemlab run mt_xpr204e power on
  chemlab run mt_xpr204e weigh --stable --wait 60s
  chemlab run mt_xpr204e unit mg
fi'''

GOLD_B07 = r'''chemlab run mt_xpr204e zero --json > z.json; rc=$?
jq -r '.error.code + ": " + .error.fix' z.json
if [ $rc -eq 3 ] && jq -e '.error.code == "ZERO_RANGE"' z.json >/dev/null; then
  chemlab run mt_xpr204e tare --wait 30s
fi
chemlab run mt_xpr204e weigh'''

GOLD_B08 = r'''a=$(chemlab run mt_xpr204e weigh --stable --json | jq .net_kg)
b=$(chemlab run ohaus_px224e weigh --stable --json | jq .net_kg)
echo "xpr=$a px=$b"
if [ "$(echo "$a > $b" | bc -l)" = 1 ]; then echo mt_xpr204e > heavier.txt; else echo ohaus_px224e > heavier.txt; fi
cat heavier.txt'''

TASKS = [
    {'id': 'b01_tare_beaker', 'kind': 'single step', 'instruments': [XPR], 'world': w_beaker_on_xpr, 'check': c_tare_beaker,
     'instruction': 'An empty beaker sits on the pan of the METTLER TOLEDO XPR204E (mt_xpr204e). Tare the balance so that the beaker reads 0 g.',
     'gold': 'chemlab run mt_xpr204e tare --wait 30s && chemlab run mt_xpr204e weigh'},
    {'id': 'b02_weigh_report', 'kind': 'single step (read and report)', 'instruments': [PX], 'world': w_sample_on_px, 'check': c_weigh_report,
     'instruction': 'Weigh the sample on the OHAUS PX224E (ohaus_px224e) and write its stable net mass in milligrams, as a plain number, '
                    'into mass_mg.txt in your work directory.',
     'gold': 'chemlab run ohaus_px224e weigh --stable --json | jq -r .net_mg > mass_mg.txt && cat mass_mg.txt'},
    {'id': 'b03_unit_kg', 'kind': 'single step (setting)', 'instruments': [SPX], 'check': c_unit_kg,
     'instruction': 'Switch the display of the OHAUS Scout SPX2201 (ohaus_spx2201) to kilograms.',
     'gold': 'chemlab run ohaus_spx2201 unit && chemlab run ohaus_spx2201 unit kg'},
    {'id': 'b04_close_and_zero', 'kind': 'two steps', 'instruments': [XPR], 'world': w_door_open_residue, 'check': c_close_zero,
     'instruction': 'Close the draft shield of the XPR204E (mt_xpr204e) and zero the empty balance.',
     'gold': 'chemlab run mt_xpr204e door close && chemlab run mt_xpr204e zero --wait 30s'},
    {'id': 'b05_recover_unstable', 'kind': 'recovery: unstable reading (doors open)', 'instruments': [PX], 'world': w_px_doors_open_beaker,
     'check': c_px_tare_closed, 'instruction': 'Tare the OHAUS PX224E (ohaus_px224e) with the beaker that is on its pan.', 'gold': GOLD_B05},
    {'id': 'b06_recover_power_off', 'kind': 'recovery: balance switched off', 'instruments': [XPR], 'world': w_xpr_off, 'check': c_xpr_on_mg,
     'instruction': 'Set the display unit of the XPR204E (mt_xpr204e) to milligrams.', 'gold': GOLD_B06},
    {'id': 'b07_recover_zero_range', 'kind': 'recovery: zero out of range -> tare', 'instruments': [XPR], 'world': w_xpr_50g, 'check': c_zero_range,
     'instruction': 'An empty 50 g beaker is on the XPR204E (mt_xpr204e). Make the balance read 0.0000 g with the beaker on the pan '
                    '(a colleague says "just press zero").', 'gold': GOLD_B07},
    {'id': 'b08_compare_two', 'kind': 'two instruments', 'instruments': [XPR, PX], 'world': w_two_samples, 'check': c_heavier,
     'instruction': 'Each of the two analytical balances (mt_xpr204e, ohaus_px224e) holds a sample. Write the id of the balance with the '
                    'heavier sample into heavier.txt.', 'gold': GOLD_B08},
    {'id': 'b09_stable_after_load', 'kind': 'long action: wait for a stable reading', 'instruments': [XPR], 'world': w_just_loaded,
     'check': c_stable_mass, 'instruction': 'A sample has just been put on the XPR204E (mt_xpr204e). Write its stable net mass in grams '
                                           '(4 decimals) into mass_g.txt.',
     'gold': 'chemlab run mt_xpr204e weigh --stable --json | jq -r \'.net_g * 10000 | round / 10000\' > mass_g.txt && cat mass_g.txt'},
    {'id': 'b10_prepare_px', 'kind': 'multi-step preparation', 'instruments': [PX], 'world': w_px_off_open, 'check': c_px_ready,
     'instruction': 'Prepare the OHAUS PX224E (ohaus_px224e) for weighing: switched on, doors closed, display in milligrams, zeroed.',
     'gold': 'chemlab run ohaus_px224e power on && chemlab run ohaus_px224e door close && chemlab run ohaus_px224e unit mg && '
             'chemlab run ohaus_px224e zero --wait 30s && chemlab run ohaus_px224e status'},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'balance'


def main(only=None, noact=True):
    return run_suite('balance', TASKS, only=only, noact=noact)
