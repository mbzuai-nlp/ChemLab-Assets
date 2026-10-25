"""Extraction class acceptance tasks (10): gold solutions use `chemlab run` (and `chemlab call` where the class says so); the
checkers read the evaluator view (truth slots included) and the agent's report files."""
import json

from .common import run_suite
from .extraction_world import config as world

K0 = 273.15
MLMIN = 1e-6 / 60
SFE, SPE, FLLEX, TFF, ED, PLE = ('sft_sft110', 'biotage_extrahera_classic', 'syrris_asiafllex', 'sartorius_vivaflow200', 'pccell_ed64002',
                                 'buchi_speedextractore')


def cfg(variants=None, extra=None):
    """The task's lab config: the extraction world (+ extra operator steps per instrument)."""
    def f(c):
        c = world(c, variants=variants)
        for e in c['instruments']:
            e['setup'] = list(e.get('setup') or []) + list((extra or {}).get(e['id'], []))
        return c
    return f


def saved(c, name):
    t = c.work_file(name)
    try:
        return json.loads(t) if t else None
    except ValueError:
        return None


# ------------------------------------------------------------------ checkers
def c_sfe(c):
    e = c.ev(SFE)
    c.near('extraction pressure 220 bar (gauge)', e['pressure.target_abs_pa'], 2.2e7 + 101325.0, 1.0)
    c.equal('run finished', e['cycle.phase'], 'succeeded')
    c.add('extract collected', (e.get('truth.collected_solute_kg') or 0) > 0, e.get('truth.collected_solute_kg'))
    rec = saved(c, 'sfe.json') or {}
    c.equal('sfe.json holds the finished run record', (rec.get('result') or {}).get('phase'), 'succeeded')


def c_sfe_vent(c):
    e = c.ev(SFE)
    c.add('not extracting any more', e['cycle.phase'] != 'running', e['cycle.phase'])
    c.add('pressure released (below 10 mbar gauge)', (e.get('pressure.indicated_gauge_pa') or 0) <= 1000.0, e.get('pressure.indicated_gauge_pa'))
    c.add('it ran about 5 minutes first', (e.get('cycle.elapsed_s') or 0) >= 250, e.get('cycle.elapsed_s'))


def c_spe(c):
    e = c.ev(SPE)
    c.equal('method run completed', e['method.status'], 'completed')
    c.equal('6 strokes (3 steps x 2 repetitions)', e['method.completed_strokes'], 6)
    c.near('positive pressure 1 bar', e['pressure.target_pa'], 1e5, 1e-6)


def c_prime(c):
    e = c.ev(SPE)
    s2 = (e.get('inlets.state') or {}).get('S2') or {}
    c.equal('inlet S2 assigned methanol (sol-001)', s2.get('assigned'), 'sol-001')
    c.equal('the S2 line holds methanol', s2.get('line'), 'sol-001')


def c_fllex(c):
    e = c.ev(FLLEX)
    c.equal('run finished', e['cycle.phase'], 'succeeded')
    c.equal('no breakthrough or fault', e['cycle.fault'], '')
    c.add('residual pressure released', (e.get('pressure.bpr_gauge_pa') or 0) < 1000.0, e.get('pressure.bpr_gauge_pa'))
    c.equal('depressurized', e['cycle.stage'], 'depressurized')


def c_ed_method(c):
    e = c.ev(ED)
    c.add('method configured', e['method.configured'] is True)
    c.near('current 20 mA', e['electrical.current_target_a'], 0.02, 1e-9)
    c.near('voltage limit 15 V', e['electrical.voltage_target_v'], 15.0, 1e-9)
    c.near('loop flow 0.6 L/min', e['flow.target_m3_s'], 1e-5, 1e-12)


def c_ed_run(c):
    e = c.ev(ED)
    c.near('loop flow 1 L/min', e['flow.target_m3_s'], 1000 * MLMIN, 1e-12)
    c.equal('run finished', e['cycle.phase'], 'succeeded')
    rec = saved(c, 'ed.json') or {}
    c.equal('ed.json holds the finished run record', (rec.get('result') or {}).get('phase'), 'succeeded')


def c_ple_prepare(c):
    e = c.ev(PLE)
    m = e.get('method.current') or {}
    c.near('method 120 °C', m.get('temperature_k'), K0 + 120, 1e-6)
    c.near('method 100 bar', m.get('pressure_gauge_pa'), 1e7, 1e-3)
    c.equal('2 cycles', m.get('cycles'), 2)
    c.near('hold 5 min', m.get('hold_s'), 300.0, 1e-6)
    c.equal('solvent 1 only', m.get('solvent_fractions'), [1.0, 0.0, 0.0, 0.0])
    c.add('preheating on', e.get('thermal.preheat_enabled') is True)
    c.near('preheat to 120 °C', e.get('thermal.preheat_target_k'), K0 + 120, 1e-6)


def c_ple_run(c):
    e = c.ev(PLE)
    c.equal('run finished', e['cycle.phase'], 'succeeded')
    c.add('shield closed', (e.get('shield.lift_m') or 0) <= 1e-9, e.get('shield.lift_m'))
    c.near('cycles.txt = completed cycles', c.number_file('cycles.txt'), e['cycle.index'], 1e-9)


def c_tff(c):
    e = c.ev(TFF)
    c.equal('stopped', e['cycle.phase'], 'cancelled')
    c.add('ran about 5 minutes', (e.get('cycle.elapsed_s') or 0) >= 290, e.get('cycle.elapsed_s'))
    c.near('permeate.txt = permeate collected (mL)', c.number_file('permeate.txt'), (e.get('volume.permeate_m3') or 0) * 1e6, 0.5)


# ------------------------------------------------------------------ gold solutions
GOLD_SFE = r'''chemlab run sft_sft110 pressure 220bar
job=$(chemlab run sft_sft110 start --json | jq -r .job)
chemlab wait "$job" --max 1h
chemlab run sft_sft110 result --save-to sfe.json'''

GOLD_SFE_VENT = r'''job=$(chemlab run sft_sft110 start --json | jq -r .job)
chemlab wait "$job" --max 5min
chemlab run sft_sft110 vent --json > v.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "RUNNING"' v.json >/dev/null; then
  jq -r .error.fix v.json
  chemlab run sft_sft110 stop
  chemlab run sft_sft110 vent --json > v.json
fi
chemlab wait "$(jq -r .job v.json)" --max 30min
chemlab run sft_sft110 status'''

GOLD_SPE = r'''chemlab run biotage_extrahera_classic method load=1mL wash=1mL elute=1mL flow=6mL/min drain=3mL/min reps=2
chemlab run biotage_extrahera_classic pressure 1bar --json > p.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "MANIFOLD_UP"' p.json >/dev/null; then
  jq -r .error.fix p.json
  chemlab call biotage_extrahera_classic manifold.set_position --z_m 0
  chemlab run biotage_extrahera_classic pressure 1bar
fi
job=$(chemlab run biotage_extrahera_classic start --json | jq -r .job)
chemlab wait "$job" --max 1h
chemlab run biotage_extrahera_classic result'''

GOLD_PRIME = r'''chemlab run biotage_extrahera_classic prime --inlet S2 --json > p.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "NO_SOLVENT"' p.json >/dev/null; then
  jq -r .error.fix p.json
  chemlab run biotage_extrahera_classic prime --inlet S2 --solvent methanol
fi
chemlab run biotage_extrahera_classic prime --inlet S2'''

GOLD_FLLEX = r'''chemlab run syrris_asiafllex pressure 3bar
chemlab run syrris_asiafllex pressure 100mbar --channel cmp
job=$(chemlab run syrris_asiafllex start --json | jq -r .job)
chemlab wait "$job" --max 1h
job=$(chemlab run syrris_asiafllex vent --json | jq -r .job)
chemlab wait "$job" --max 10min
chemlab run syrris_asiafllex status'''

GOLD_ED_METHOD = 'chemlab run pccell_ed64002 method mode=constant_current current=20mA voltage=15V flow=0.6L/min time=30min timeout=40min endpoint=0.05mS/cm'

GOLD_ED_RUN = r'''chemlab run pccell_ed64002 flow 1L/min
chemlab run pccell_ed64002 start --json > s.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "POWER_OFF"' s.json >/dev/null; then
  chemlab run pccell_ed64002 power on
  chemlab run pccell_ed64002 start --json > s.json
fi
chemlab wait "$(jq -r .job s.json)" --max 1h
chemlab run pccell_ed64002 result --save-to ed.json'''

GOLD_PLE_PREP = r'''chemlab run buchi_speedextractore method temp=120C pressure=100bar cycles=2 hold=5min solvents=1,0,0,0
chemlab run buchi_speedextractore set-temp 120C
chemlab run buchi_speedextractore status'''

GOLD_PLE_RUN = r'''chemlab run buchi_speedextractore start --json > s.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "SHIELD_OPEN"' s.json >/dev/null; then
  jq -r .error.fix s.json
  chemlab run buchi_speedextractore close shield
  chemlab run buchi_speedextractore start --json > s.json
fi
chemlab wait "$(jq -r .job s.json)" --max 2h
chemlab run buchi_speedextractore result --json | jq -r .result.cycle > cycles.txt
cat cycles.txt'''

GOLD_TFF = r'''job=$(chemlab run sartorius_vivaflow200 start --json | jq -r .job)
chemlab wait "$job" --max 5min
chemlab run sartorius_vivaflow200 stop
chemlab run sartorius_vivaflow200 result --json | jq -r '.result.permeate_m3 * 1e6' > permeate.txt
cat permeate.txt'''


TASKS = [
    {'id': 'ex01_sfe_run', 'kind': 'set point + job + record file', 'instruments': [SFE], 'config': cfg(), 'check': c_sfe,
     'instruction': 'Run the supercritical CO2 extraction of the loaded sample on the SFT-110 (sft_sft110) at 220 bar, keeping the oven at its '
                    'method temperature, to the end of the run, and save the run record to sfe.json.', 'gold': GOLD_SFE},
    {'id': 'ex02_sfe_stop_vent', 'kind': 'recovery: vent refused while running; stop, then vent (job)', 'instruments': [SFE], 'config': cfg(),
     'check': c_sfe_vent,
     'instruction': 'Start the extraction on the SFT-110 (sft_sft110), let it run for about 5 minutes of lab time, then end it early and '
                    'release the pressure so the vessel can be opened.', 'gold': GOLD_SFE_VENT},
    {'id': 'ex03_spe_run', 'kind': 'recovery: manifold up; SPE method + pressure + job', 'instruments': [SPE], 'config': cfg(), 'check': c_spe,
     'instruction': 'On the Biotage Extrahera (biotage_extrahera_classic) run an SPE method: load, wash and elute 1 mL each at 6 mL/min, drain '
                    'at 3 mL/min, 2 repetitions per step, with 1 bar positive pressure. Run it to the end.', 'gold': GOLD_SPE},
    {'id': 'ex04_spe_prime', 'kind': 'recovery: no solvent on the inlet', 'instruments': [SPE], 'config': cfg(), 'check': c_prime,
     'instruction': 'Prime solvent inlet S2 of the Extrahera (biotage_extrahera_classic) with methanol until the line holds methanol '
                    '(two primes of 15 mL).', 'gold': GOLD_PRIME},
    {'id': 'ex05_fllex_release', 'kind': 'two pressure channels + job + vent job', 'instruments': [FLLEX], 'config': cfg(), 'check': c_fllex,
     'instruction': 'Run the liquid-liquid extraction on the Syrris Asia FLLEX (syrris_asiafllex) with the back pressure at 3 bar and the '
                    'cross-membrane pressure at 100 mbar, let it finish, then release the residual pressure.', 'gold': GOLD_FLLEX},
    {'id': 'ex06_ed_method', 'kind': 'single step (method with units)', 'instruments': [ED], 'config': cfg(variants={ED: 'empty'}), 'check': c_ed_method,
     'instruction': 'Program the electrodialysis stack (pccell_ed64002) for a constant-current run before the lab fills it: 20 mA with a '
                    '15 V limit, loop flow 0.6 L/min, 30 min (timeout 40 min), ending at a diluate conductivity of 0.05 mS/cm.',
     'gold': GOLD_ED_METHOD},
    {'id': 'ex07_ed_run', 'kind': 'recovery: switched off; job + record file', 'instruments': [ED], 'config': cfg(), 'check': c_ed_run,
     'instruction': 'Run the electrodialysis on pccell_ed64002 with the loop flow at 1 L/min to the end, and save the run record to ed.json.',
     'gold': GOLD_ED_RUN},
    {'id': 'ex08_ple_prepare', 'kind': 'method (kept settings) + preheat', 'instruments': [PLE], 'config': cfg(variants={PLE: 'empty'}),
     'check': c_ple_prepare,
     'instruction': 'Prepare the BUCHI SpeedExtractor E-916 (buchi_speedextractore) method: 120 °C, 100 bar, 2 cycles, 5 min hold, solvent 1 '
                    'only; and start preheating the heating block to 120 °C.', 'gold': GOLD_PLE_PREP},
    {'id': 'ex09_ple_shield', 'kind': 'recovery: shield open; job + report', 'instruments': [PLE],
     'config': cfg(extra={PLE: [{'capability': 'shield.set_lift', 'arguments': {'lift_m': 0.18}}]}), 'check': c_ple_run,
     'instruction': 'Run the pressurized solvent extraction of the loaded cells on the SpeedExtractor (buchi_speedextractore) to the end and '
                    'write the number of completed extraction cycles into cycles.txt.', 'gold': GOLD_PLE_RUN},
    {'id': 'ex10_tff_concentrate', 'kind': 'timed run, stop, report', 'instruments': [TFF], 'config': cfg(), 'check': c_tff,
     'instruction': 'Concentrate the sample on the Sartorius Vivaflow 200 (sartorius_vivaflow200) for 5 minutes of lab time, then stop and '
                    'write the permeate volume collected, in mL, into permeate.txt.', 'gold': GOLD_TFF},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'extraction'


def main(only=None, noact=True):
    return run_suite('extraction', TASKS, only=only, noact=noact)
