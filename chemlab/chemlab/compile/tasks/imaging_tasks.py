"""Imaging class acceptance tasks (10, five of them recovery tasks): gold solutions use `chemlab run`; the checkers read the
evaluator view (truth slots included) and the agent's work files.  World: tasks/imaging_world.py plus task-specific operator
steps (a pumped SEM chamber, a lit TEM beam)."""
import json

from .common import run_suite
from .imaging_world import config as world_config

SEM, TEM, VK, BX, NX, STM, CT, IB = ('jeol_jsmit200', 'jeol_jem1400flash', 'keyence_vkx3000', 'evident_bx53p', 'park_nx10', 'nanosurf_naiostm',
                                     'bruker_x4poseidon', 'thermo_ibrightfl1500')


def with_steps(slug, steps):
    """A config function: the imaging world plus extra set-up steps (operator) for one member."""
    def cfg(c):
        c = world_config(c)
        for e in c['instruments']:
            if e['asset_type_id'].split('.', 1)[1] == slug:
                e['setup'] = list(e.get('setup') or []) + steps
        return c
    return cfg


def op(cap, args):
    return {'capability': cap, 'arguments': args}


def record_file(c, name):
    t = c.work_file(name)
    try:
        return json.loads(t) if t else None
    except ValueError:
        return None


def ev_record(c, iid, cap, args):
    """A record read by the evaluator (records like the SEM / TEM last-image slot are per caller, so the evaluator view of
    that slot is empty): the result dict, or None."""
    r = c.lab.backend.evaluator_invoke(iid, cap, args, 'check-%s-%s' % (iid, cap))
    return r.get('result') if r.get('status') == 'succeeded' else None


def job_states(c, iid):
    return [(j.handle, j.description, j.state) for j in getattr(c.lab.core, 'class_jobs', {}).values() if j.instrument == iid]


# ------------------------------------------------------------------ checkers
def c_sem_image(c):
    e = c.ev(SEM)
    c.equal('specimen fe_si_polished on the holder', e.get('specimen.loaded_id'), 'fe_si_polished')
    c.equal('chamber at high vacuum', e.get('vacuum.state'), 'hv_ready')
    c.near('accelerating voltage 10 kV', e.get('beam.accelerating_voltage_v'), 10000.0, 1.0)
    c.near('magnification 2000x', e.get('scan.magnification'), 2000.0, 1e-6)
    rec = record_file(c, 'sem.json') or {}
    rid = (rec.get('result') or {}).get('image_id')
    truth = ev_record(c, SEM, 'image.result', {'image_id': rid}) if rid else None
    c.add('sem.json holds an image record of the SEM', bool(truth) and truth.get('status') == 'completed', rid)
    c.near('the image was taken at 2000x', (truth or {}).get('magnification'), 2000.0, 1e-6)
    c.near('the image was taken at 10 kV', (truth or {}).get('accelerating_voltage_v'), 10000.0, 1.0)
    c.equal('the image is 640x480', (truth or {}).get('pixels'), '640x480')


def c_sem_pumped(c):
    e = c.ev(SEM)
    c.equal('specimen fe_si_polished on the holder', e.get('specimen.loaded_id'), 'fe_si_polished')
    c.equal('chamber pumped down again', e.get('vacuum.state'), 'hv_ready')
    c.equal('drawer closed', e.get('chamber.door_state'), 'closed')


def c_tem_image(c):
    e = c.ev(TEM)
    c.equal('holder in the column', e.get('holder.position'), 'column')
    c.equal('grid au_np_on_carbon in the quick-change holder', (e.get('holder.grids') or {}).get('quick_change'), 'au_np_on_carbon')
    c.equal('filament saturated', e.get('filament.state'), 'saturated')
    c.near('magnification 50000x', e.get('optics.magnification'), 50000.0, 1e-6)
    rid = c.work_file('tem.txt')
    truth = ev_record(c, TEM, 'image.result', {'image_id': rid}) if rid else None
    c.add('tem.txt names a completed TEM image', bool(truth) and truth.get('status') == 'completed', rid)
    c.near('that image was taken at 50000x', (truth or {}).get('magnification'), 50000.0, 1e-6)


def c_tem_loaded(c):
    e = c.ev(TEM)
    c.equal('holder in the column', e.get('holder.position'), 'column')
    c.equal('grid au_np_on_carbon in the quick-change holder', (e.get('holder.grids') or {}).get('quick_change'), 'au_np_on_carbon')


def c_vk_map(c):
    e = c.ev(VK)
    c.equal('sample on the stage', (e.get('sample.on_stage') or {}).get('sample_id'), 'sio2_step_on_si')
    c.add('a measuring objective in use', e.get('head.objective') not in (None, '2.5x'), e.get('head.objective'))
    rid = e.get('result.last_record_id')
    rec = record_file(c, 'vk.json') or {}
    c.add('a height map was measured', bool(rid), rid)
    c.equal('vk.json holds that record', rec.get('record_id'), rid)


def c_bx(c):
    e = c.ev(BX)
    c.equal('slide on the stage', e.get('sample.on_stage'), 'quartz_section')
    c.equal('40x objective', e.get('objective.index'), 2)
    c.near('lamp at 70 %', e.get('illumination.level'), 0.7, 1e-9)
    c.near('camera exposure 50 ms', e.get('camera.exposure_s'), 0.05, 1e-9)
    c.add('an image was taken', bool(e.get('result.last_record_id')), e.get('result.last_record_id'))


def c_afm(c):
    e = c.ev(NX)
    c.add('a scan record exists', bool(e.get('result.last_record_id')), e.get('result.last_record_id'))
    c.equal('tip retracted at the end', e.get('engage.state'), 'retracted')
    jobs = job_states(c, NX)
    c.add('the scan was followed as a job that completed', any('AFM scan' in d and s in ('completed', 'succeeded') for _, d, s in jobs), jobs)


def c_stm(c):
    e = c.ev(STM)
    rid = e.get('result.last_record_id')
    c.add('an STM image exists', bool(rid), rid)
    c.equal('stm.txt names it', c.work_file('stm.txt'), rid)
    c.equal('sample hopg in the head', e.get('holder.sample_id'), 'hopg')


def c_ct(c):
    e = c.ev(CT)
    rid = e.get('result.last_record_id') or ''
    c.add('a projection scan was recorded', rid.startswith('PJ'), rid)
    c.near('geometric magnification 3x', e.get('geometry.magnification'), 3.0, 0.01)
    c.near('sample raised into the beam (chuck top near 397 mm)', e.get('stage.chuck_z_m'), 0.397, 0.002)


def c_gel(c):
    e = c.ev(IB)
    c.equal('nucleic-acid gel mode', e.get('mode'), 'nucleic_acid_gels')
    rid = e.get('result.last_image_id')
    rec = record_file(c, 'gel.json') or {}
    c.add('an image was captured', bool(rid), rid)
    c.equal('gel.json holds it', rec.get('record_id'), rid)
    c.equal('the gel is in the imager', e.get('sample.on_stage'), 'gel1')


# ------------------------------------------------------------------ gold solutions
GOLD_SEM = '''chemlab run jeol_jsmit200 load fe_si_polished
chemlab run jeol_jsmit200 vacuum pump --wait
chemlab run jeol_jsmit200 source on --kv 10kV --wait
chemlab run jeol_jsmit200 focus auto
chemlab run jeol_jsmit200 zoom 2000x
chemlab run jeol_jsmit200 acquire --pixels 640x480 --dwell 20us --wait
chemlab run jeol_jsmit200 result --save-to sem.json'''

GOLD_SEM_VENT = r'''chemlab run jeol_jsmit200 load fe_si_polished --json > l.json; rc=$?
jq -r '.error.code + ": " + .error.fix' l.json
if [ $rc -eq 3 ] && jq -e '.error.code == "NOT_VENTED"' l.json >/dev/null; then
  chemlab run jeol_jsmit200 vacuum vent --wait
  chemlab run jeol_jsmit200 load fe_si_polished
fi
chemlab run jeol_jsmit200 vacuum pump --wait
chemlab run jeol_jsmit200 status'''

GOLD_TEM = '''chemlab run jeol_jem1400flash load au_np_on_carbon
chemlab run jeol_jem1400flash source on --wait
chemlab run jeol_jem1400flash zoom 50000x
chemlab run jeol_jem1400flash focus auto
chemlab run jeol_jem1400flash acquire --exposure 1s --wait
chemlab run jeol_jem1400flash result --json | jq -r .record_id > tem.txt; cat tem.txt'''

GOLD_TEM_BEAM = r'''chemlab run jeol_jem1400flash load au_np_on_carbon --json > l.json; rc=$?
jq -r '.error.code + ": " + .error.fix' l.json
if [ $rc -eq 3 ] && jq -e '.error.code == "BEAM_ON"' l.json >/dev/null; then
  chemlab run jeol_jem1400flash source off
  chemlab run jeol_jem1400flash load au_np_on_carbon
fi
chemlab run jeol_jem1400flash status'''

GOLD_VK = r'''chemlab run keyence_vkx3000 load sio2_step_on_si
chemlab run keyence_vkx3000 focus auto --wait --json > f.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "LENS_NOT_FOR_MEASURING"' f.json >/dev/null; then
  jq -r .error.fix f.json
  chemlab run keyence_vkx3000 zoom --objective 10x
  chemlab run keyence_vkx3000 focus auto --wait
fi
chemlab run keyence_vkx3000 acquire --wait --max 30min
chemlab run keyence_vkx3000 result --save-to vk.json'''

GOLD_BX = '''chemlab run evident_bx53p load quartz_section
chemlab run evident_bx53p zoom --objective 40x
chemlab run evident_bx53p source on --level 70%
chemlab run evident_bx53p acquire --exposure 50ms --wait'''

GOLD_AFM = r'''chemlab run park_nx10 load grating
chemlab run park_nx10 source on
chemlab run park_nx10 approach --wait --max 30min
job=$(chemlab run park_nx10 acquire --size 5um --pixels 128 --rate 1Hz --json | jq -r .job)
echo "job: $job"
chemlab status "$job"
chemlab wait "$job" --max 1h
chemlab run park_nx10 retract'''

GOLD_STM = '''chemlab run nanosurf_naiostm load hopg
chemlab run nanosurf_naiostm approach --wait --max 30min
chemlab run nanosurf_naiostm acquire --size 10nm --points 64 --wait
chemlab run nanosurf_naiostm result --json | jq -r .record_id > stm.txt; cat stm.txt'''

GOLD_CT = r'''chemlab run bruker_x4poseidon load alumina_pellet
chemlab run bruker_x4poseidon source on --wait
chemlab run bruker_x4poseidon zoom 3x
chemlab run bruker_x4poseidon acquire --exposure 0.1s --binning 4x4 --wait --max 2h --json > a.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "NOT_IN_FIELD"' a.json >/dev/null; then
  jq -r .error.fix a.json
  chemlab run bruker_x4poseidon move --z 397mm
  chemlab run bruker_x4poseidon acquire --exposure 0.1s --binning 4x4 --wait --max 2h
fi
chemlab run bruker_x4poseidon status'''

GOLD_GEL = r'''chemlab run thermo_ibrightfl1500 load gel1
chemlab run thermo_ibrightfl1500 acquire --wait --json > a.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "NO_MODE"' a.json >/dev/null; then
  jq -r .error.fix a.json
  chemlab run thermo_ibrightfl1500 acquire --mode nucleic_acid_gels --wait
fi
chemlab run thermo_ibrightfl1500 result --json | jq '{record_id: .record_id}' > gel.json; cat gel.json'''

SEM_PUMPED = [op('vacuum.evacuate', {}), {'advance_s': 240.0}]
TEM_BEAM_ON = [{'advance_s': 150.0}, op('ht.set_on', {'on': True}), {'advance_s': 140.0}, op('filament.set_on', {'on': True}), {'advance_s': 30.0}]

TASKS = [
    {'id': 'im01_sem_image', 'kind': 'multi-step + long actions (vacuum, beam, image)', 'instruments': [SEM], 'check': c_sem_image,
     'instruction': 'Image the polished Fe-Si specimen (fe_si_polished, on the bench) in the JEOL SEM (jeol_jsmit200) at 10 kV and 2000x: load it, '
                    'pump the chamber down, switch the beam on, autofocus, take a 640x480 image and save its record as sem.json in your work dir.',
     'gold': GOLD_SEM},
    {'id': 'im02_sem_recover_vacuum', 'kind': 'recovery: chamber under vacuum', 'instruments': [SEM], 'config': with_steps(SEM, SEM_PUMPED),
     'check': c_sem_pumped,
     'instruction': 'Load the specimen fe_si_polished into the SEM (jeol_jsmit200) and leave the chamber pumped down again, ready for imaging.',
     'gold': GOLD_SEM_VENT},
    {'id': 'im03_tem_image', 'kind': 'multi-step + long actions (airlock, HT ramp, image) + report', 'instruments': [TEM], 'check': c_tem_image,
     'instruction': 'In the TEM (jeol_jem1400flash), load the grid au_np_on_carbon, switch the beam on (120 kV as set), set 50000x, autofocus and '
                    'take a 1 s camera image. Write the image record id into tem.txt.', 'gold': GOLD_TEM},
    {'id': 'im04_tem_recover_beam', 'kind': 'recovery: beam on while loading', 'instruments': [TEM], 'config': with_steps(TEM, TEM_BEAM_ON),
     'check': c_tem_loaded,
     'instruction': 'Load the grid au_np_on_carbon into the column of the TEM (jeol_jem1400flash) with the quick-change holder.', 'gold': GOLD_TEM_BEAM},
    {'id': 'im05_vk_recover_objective', 'kind': 'recovery: viewing objective cannot measure', 'instruments': [VK], 'check': c_vk_map,
     'instruction': 'Measure the 3D height map of the sample sio2_step_on_si on the KEYENCE VK-X (keyence_vkx3000); the head is already coarse-focused '
                    'near the stage. Save the measurement record as vk.json.', 'gold': GOLD_VK},
    {'id': 'im06_bx53_polarized', 'kind': 'multi-step (objective, lamp, camera)', 'instruments': [BX], 'check': c_bx,
     'instruction': 'On the BX53-P polarizing microscope (evident_bx53p) put the thin section quartz_section on the stage, use the 40x objective, set '
                    'the lamp to 70 % and take a camera image with 50 ms exposure.', 'gold': GOLD_BX},
    {'id': 'im07_afm_scan_job', 'kind': 'long action: job id (status, wait)', 'instruments': [NX], 'check': c_afm,
     'instruction': 'Image the calibration grating (grating) with the Park NX10 AFM: load it, switch the deflection laser on and approach, then start a '
                    '5 um scan with 128 lines at 1 Hz as a job (do not block on it), check its status, wait for it, and finally retract the tip.',
     'gold': GOLD_AFM},
    {'id': 'im08_stm_report', 'kind': 'long actions + report', 'instruments': [STM], 'check': c_stm,
     'instruction': 'Take a 10 nm STM image (64 points per line) of the HOPG sample (hopg) with the NaioSTM (nanosurf_naiostm) and write the record id '
                    'into stm.txt.', 'gold': GOLD_STM},
    {'id': 'im09_ct_recover_field', 'kind': 'recovery: sample not in the beam', 'instruments': [CT], 'check': c_ct,
     'instruction': 'Take a micro-CT scan of alumina_pellet (a 6 mm high pellet) on the Bruker X4 POSEIDON (bruker_x4poseidon) at 3x geometric '
                    'magnification, 0.1 s per projection and 4x4 binning. The X-ray beam runs 400 mm above the bench.', 'gold': GOLD_CT},
    {'id': 'im10_gel_recover_mode', 'kind': 'recovery: no imaging mode', 'instruments': [IB], 'check': c_gel,
     'instruction': 'Capture an image of the agarose gel gel1 in the iBright imager (thermo_ibrightfl1500) as a nucleic-acid gel and save its record id '
                    'as gel.json ({"record_id": ...}).', 'gold': GOLD_GEL},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'imaging'
    _t.setdefault('config', world_config)


def main(only=None, noact=True):
    return run_suite('imaging', TASKS, only=only, noact=noact)
