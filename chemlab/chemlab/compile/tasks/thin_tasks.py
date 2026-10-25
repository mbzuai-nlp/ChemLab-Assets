"""Thin class acceptance tasks: the shared verbs on the five one-of-a-kind instruments (gold with `chemlab run`, NoAct fails)."""
from .common import run_suite
from .thin_world import config as world_config

VAI, OXI, PSV, CEM, OLI = 'vaisala_dmt152', 'wtw_oxitopi', 'inert_puresolvmd', 'cem_libertyblue', 'cytiva_ktaoligosynt'


def c_dewpoint(c):
    e = c.ev(VAI)
    c.add('transmitter on', e['power.on'])
    tdf = e.get('reading.tdf_k')
    c.near('dewpoint.txt = the dew point in °C (±0.5 K)', c.number_file('dewpoint.txt'), (tdf or 0) - 273.15, 0.5)


def c_reading_file(c):
    import json
    e = c.ev(VAI)
    c.add('transmitter on', e['power.on'])
    t = c.work_file('reading.json')
    try:
        rec = json.loads(t) if t else {}
    except ValueError:
        rec = {}
    c.add('reading.json holds a SEND reading with a dew point', isinstance((rec.get('result') or {}).get('tdf_k'), (int, float)), str(rec)[:120])


def c_head_started(c):
    st = (c.ev(OXI).get('heads.status') or {}).get('h3') or {}
    c.add('head h3 is measuring (not idle)', st.get('state') not in (None, 'idle'), st.get('state'))
    c.add('the other heads are untouched', all(((c.ev(OXI)['heads.status'].get(h) or {}).get('state') == 'idle') for h in ('h1', 'h2', 'h4', 'h5', 'h6')))


def c_door_platform(c):
    e = c.ev(OXI)
    c.add('incubator door open', e['incubator.door_open'])
    c.add('stirring platform on', e['platform.powered'])


def c_puresolv(c):
    e = c.ev(PSV)
    c.add('gas isolation valve open', e['gas.isolation_open'])
    c.add('left cabinet door open', e['cabinet.left_angle_rad'] >= 1.6, e['cabinet.left_angle_rad'])
    c.add('right cabinet door still closed', e['cabinet.right_angle_rad'] < 1e-6, e['cabinet.right_angle_rad'])


def c_synthesis(iid, need_cover):
    def check(c):
        e = c.ev(iid)
        c.equal('the synthesis run finished', e.get('cycle.phase'), 'succeeded')
        t = c.work_file('synthesis.json') or ''
        c.add('synthesis.json saved by the agent', '"result"' in t, t[:80])
    return check


GOLD_VAI = r'''chemlab run vaisala_dmt152 power on
chemlab wait 60
chemlab run vaisala_dmt152 result --json | jq -r '.result.tdf_k - 273.15' > dewpoint.txt
cat dewpoint.txt'''

GOLD_VAI_REC = r'''chemlab run vaisala_dmt152 result --save-to reading.json --json > r.json; rc=$?
jq -r '.error.code // "ok"' r.json
if [ $rc -eq 3 ]; then
  chemlab run vaisala_dmt152 power on
  chemlab wait 60
  chemlab run vaisala_dmt152 result --save-to reading.json
fi'''

GOLD_SYN = r'''chemlab run {inst} start --json > s.json; rc=$?
jq -r '.error.code // "started"' s.json
if [ $rc -ne 0 ]; then
  chemlab run {inst} status
  chemlab run {inst} power on
  {close}
  chemlab run {inst} start
fi
chemlab run {inst} wait --max 4h
chemlab run {inst} result --save-to synthesis.json'''


def cfg(c):
    return world_config(c)


TASKS = [
    {'id': 'thin01_dmt152_dewpoint', 'kind': 'two steps + report', 'instruments': [VAI], 'check': c_dewpoint,
     'instruction': 'Switch the Vaisala DMT152 transmitter (vaisala_dmt152) on, let it settle for a minute, and write its dew point in °C '
                    'into dewpoint.txt.', 'gold': GOLD_VAI},
    {'id': 'thin02_dmt152_recover_off', 'kind': 'recovery: instrument switched off', 'instruments': [VAI], 'check': c_reading_file,
     'instruction': 'Save one reading of the Vaisala DMT152 (vaisala_dmt152) into reading.json in your work directory.', 'gold': GOLD_VAI_REC},
    {'id': 'thin03_oxitop_start_head', 'kind': 'single step with --head', 'instruments': [OXI], 'check': c_head_started,
     'instruction': 'Start the BOD measurement on measuring head h3 of the OxiTop-i system (wtw_oxitopi); leave the other heads alone.',
     'gold': 'chemlab run wtw_oxitopi start --head h3 && chemlab run wtw_oxitopi result --head h3'},
    {'id': 'thin04_oxitop_door_platform', 'kind': 'two steps (part + power)', 'instruments': [OXI], 'check': c_door_platform,
     'instruction': 'Open the incubator door of the OxiTop-i system (wtw_oxitopi) and switch its stirring platform on.',
     'gold': 'chemlab run wtw_oxitopi open incubator_door && chemlab run wtw_oxitopi power on && chemlab run wtw_oxitopi status'},
    {'id': 'thin05_puresolv_valve_door', 'kind': 'named parts', 'instruments': [PSV], 'check': c_puresolv,
     'instruction': 'On the PureSolv MD 7 (inert_puresolvmd), open the gas isolation valve and the left cabinet door.',
     'gold': 'chemlab run inert_puresolvmd open gas_isolation_valve && chemlab run inert_puresolvmd open cabinet_left_door'},
    {'id': 'thin06_libertyblue_run', 'kind': 'recovery: off, cover and clamp open; long action (job)', 'instruments': [CEM],
     'check': c_synthesis(CEM, True), 'config': cfg,
     'instruction': 'Run the queued peptide synthesis on the CEM Liberty Blue (cem_libertyblue) to the end and save its result into '
                    'synthesis.json.',
     'gold': GOLD_SYN.replace('{inst}', CEM).replace('{close}', 'chemlab run %s close cover; chemlab run %s close clamp' % (CEM, CEM))},
    {'id': 'thin07_oligosynt_run', 'kind': 'recovery: off, clamp open; long action (job)', 'instruments': [OLI],
     'check': c_synthesis(OLI, False), 'config': cfg,
     'instruction': 'Run the loaded oligonucleotide synthesis on the ÄKTA oligosynt (cytiva_ktaoligosynt) to the end and save its result '
                    'into synthesis.json.',
     'gold': GOLD_SYN.replace('{inst}', OLI).replace('{close}', 'chemlab run %s close clamp' % OLI)},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'thin'


def main(only=None, noact=True):
    return run_suite('thin', TASKS, only=only, noact=noact)
