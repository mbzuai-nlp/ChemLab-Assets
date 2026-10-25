"""Spectroscopy class acceptance tasks (10): gold solutions use `chemlab run` (and `chemlab call` only for what the class does not
cover); the checkers read the evaluator view, the evaluator's own analysis calls and the agent's report files.

Worlds (trusted host, before the agent starts): the class default world (spectroscopy_world.config) plus per-task stages - a
blank recorded by the operator and the sample cell put in, the IRF record handed over as a file, a sample temperature off its
reference.  Five tasks are recoveries: the gold triggers the error, reads `--json` `.error.code` and does what the fix says.
"""
import json

from .common import run_suite
from . import spectroscopy_world as W

DR, J15, FP, IR, NRS, FLS, NMR, ESR, MQ = ('hach_dr6000', 'jasco_j1500', 'jasco_fp8350', 'jasco_ftir4700', 'jasco_nrs5500',
                                           'edinburgh_fls1000tcspc', 'magritek_spinsolve80', 'bruker_magnettechesr5000',
                                           'bruker_minispecmq20')


def cfg(c):
    return W.config(c)


def ex(lab, iid, cap):
    return json.loads(json.dumps(lab.backend.instruments[iid].contract['capabilities'][cap]['example_arguments']))


def ev_call(lab, iid, cap, args, tag):
    r = lab.backend.evaluator_invoke(iid, cap, args, 'check-%s-%s' % (iid, tag))
    return r.get('result') if r.get('status') == 'succeeded' else None


# ------------------------------------------------------------------ worlds
def w_j1500_ready(lab):
    W.j1500_blank_recorded(lab.backend, J15)


def w_fp_ready(lab):
    W.jasco_blank_recorded(lab.backend, FP, tag='fp')


def w_esr_ready(lab):
    """Blank recorded, sample tube in, lid closed; the bridge is off (it switched itself off after the blank scan)."""
    w = W.jasco_blank_recorded(lab.backend, ESR, tag='esr')
    w.op('microwave.set', {'enabled': False})


def w_mq_warm(lab):
    """The VT unit was left at 30 °C by the previous user."""
    w = W.World(lab.backend, MQ, 'mq')
    w.op('thermal.set_temperature', {'temperature_k': 303.15})
    w.wait(120)


def w_fls_irf_file(lab):
    """The operator measured the IRF (scatterer, em = ex = 405 nm, standard window / bins), saved it as irf.json in the agent's
    work dir, then put the fluorescent reference back in (cover closed, laser armed)."""
    w = W.World(lab.backend, FLS, 'fls')
    m = ex(lab, FLS, 'method.configure')
    w.op('power.set', {'enabled': False})
    w.ev('sample.cover', {'opened': True})
    w.ev('sample.load', W.FLS_SCATTERER)
    w.ev('sample.cover', {'opened': False})
    w.op('power.set', {'enabled': True})
    w.ev('source.arm', {'enabled': True})
    w.op('method.configure', dict(m, emission_wavelength_m=m['excitation_wavelength_m']))
    w.op('irf.start', {})
    w.until(lambda v: v.get('scan.phase') != 'running', 600, 0.5)
    rec = w.ev('result.read', {})['result']
    (lab.work / 'irf.json').write_text(json.dumps({'instrument': FLS, 'record': rec}) + '\n', encoding='utf-8')
    w.op('power.set', {'enabled': False})
    w.ev('sample.cover', {'opened': True})
    w.ev('sample.load', ex(lab, FLS, 'sample.load'))
    w.ev('sample.cover', {'opened': False})
    w.op('power.set', {'enabled': True})
    w.ev('source.arm', {'enabled': True})


# ------------------------------------------------------------------ checkers
def c_dr_scan(c):
    e = c.ev(DR)
    scan = e.get('dr.scan') or {}
    wl = scan.get('wavelength_m') or []
    c.add('a scan was read against a zero', bool(wl) and e.get('dr.zero') is not None, len(wl))
    c.near('scan starts at 400 nm', wl[0] if wl else None, 4e-07, 1e-12)
    c.near('scan ends at 700 nm', wl[-1] if wl else None, 7e-07, 1e-12)
    c.near('2 nm steps', (wl[1] - wl[0]) if len(wl) > 1 else None, 2e-09, 1e-13)
    c.equal('the sample cuvette is in (it was read after the zero)', e.get('sample.id'), 'sample_01')
    c.near('lambda_max.txt = 520 nm (+-2)', c.number_file('lambda_max.txt'), 520.0, 2.0)


def c_j1500_blank(c):
    e = c.ev(J15)
    c.add('purge running and the air flushed', e['purge.ready'] and e['purge.flow_std_m3_s'] > 0, e['purge.flow_std_m3_s'])
    c.add('lamp on', e['lamp.enabled'])
    c.equal('a blank scan completed', (e['scan.kind'], e['scan.phase']), ('blank', 'succeeded'))
    c.equal('46 points (235..190 nm, 1 nm)', e['scan.completed_points'], 46)
    c.add('the blank is valid for the current method', e['blank.ready'])


def c_j1500_helix(c):
    e = c.ev(J15)
    c.equal('the sample scan completed', (e['scan.kind'], e['scan.phase']), ('sample', 'succeeded'))
    fit = ev_call(c.lab, J15, 'structure.fit', {'residue_concentration_mol_m3': 0.2, 'path_length_m': 0.001}, 'fit')
    want = (fit or {}).get('fractions', {}).get('Helix')
    c.add('the evaluator can fit the measured spectrum', want is not None, fit)
    c.near('helix.txt = helix fraction of the fit (+-0.01)', c.number_file('helix.txt'), want if want is not None else -1, 0.01)


def c_ftir(c):
    e = c.ev(IR)
    c.equal('ATR accessory installed', e['accessory.mode'], 'atr')
    c.add('a background with the current method', e['background.ready'])
    c.equal('ethanol_01 measured', (e['sample.id'], e['measurement.count'] >= 1), ('ethanol_01', True))
    c.near('resolution 4 cm-1', e['method.resolution_m_inv'], 400.0, 1e-9)
    c.near('range starts at 400 cm-1', e['method.start_wavenumber_m_inv'], 40000.0, 1e-6)
    c.near('band.txt = strongest band about 1050 cm-1 (+-15)', c.number_file('band.txt'), 1050.0, 15.0)


def c_nmr(c):
    e = c.ev(NMR)
    c.equal('1H with 8 scans', (e['method.nucleus'], e['method.scans']), ('1H', 8))
    c.equal('acquisition completed, 8 scans', (e['acquisition.status'], e['acquisition.completed_scans']), ('completed', 8))
    t = c.work_file('nmr.json')
    rec = json.loads(t) if t else {}
    c.add('nmr.json holds the spectrum', rec.get('points', 0) >= 256 and rec.get('instrument') == NMR, rec.get('points'))


def c_esr(c):
    e = c.ev(ESR)
    sp = ev_call(c.lab, ESR, 'spectrum.read', {}, 'sp') or {}
    c.equal('the sample scan completed', (sp.get('kind'), e['scan.phase']), ('sample', 'succeeded'))
    c.near('g.txt = 2.0055 (+-0.0005)', c.number_file('g.txt'), 2.0055, 0.0005)


def c_raman(c):
    e = c.ev(NRS)
    c.equal('a spectrum completed', e['scan.phase'], 'succeeded')
    c.add('door closed, laser on', (not e['door.opened']) and e['laser.enabled'])
    c.near('peak.txt = quartz band at 464 cm-1 (+-3)', c.number_file('peak.txt'), 464.0, 3.0)
    jobs = [j for j in getattr(c.lab.core, 'class_jobs', {}).values() if j.instrument == NRS]
    c.add('a job was used and it succeeded', any(j.state == 'succeeded' for j in jobs), [(j.handle, j.state) for j in jobs])
    asked = [x['argv'][1:3] for x in c.lab.core.log.entries if x['argv'][1:2] == ['status'] and len(x['argv']) > 2]
    c.add('the job status was checked', any(a[1].startswith('job-') for a in asked), asked)


def c_mq(c):
    e = c.ev(MQ)
    c.near('VT back at 37 °C', e['thermal.target_k'], 310.15, 1e-6)
    c.equal('the run completed', e['scan.phase'], 'succeeded')
    fit = ev_call(c.lab, MQ, 'relaxation.fit', {}, 'fit')
    want = (fit or {}).get('time_s')
    c.equal('it was an inversion-recovery T1 run', (fit or {}).get('quantity'), 'T1')
    c.near('t1.txt = T1 of the fit in ms (+-1)', c.number_file('t1.txt'), want * 1000 if want else -1, 1.0)


def c_fp(c):
    e = c.ev(FP)
    sp = ev_call(c.lab, FP, 'spectrum.read', {}, 'sp') or {}
    c.equal('the sample scan completed', (sp.get('kind'), e['scan.phase']), ('sample', 'succeeded'))
    pts = sp.get('points') or []
    best = max(pts, key=lambda p: p.get('intensity') or -1e9) if pts else {}
    want = (best.get('emission_m') or 0) * 1e9
    c.near('em.txt = emission maximum of the measured spectrum (nm)', c.number_file('em.txt'), want, 0.6)
    c.near('the method was not changed (530..600 nm)', pts[0].get('emission_m') if pts else None, 5.3e-07, 1e-12)


def c_fls(c):
    e = c.ev(FLS)
    c.equal('a decay completed', e['scan.phase'], 'succeeded')
    rec = ev_call(c.lab, FLS, 'result.read', {}, 'res') or {}
    h = (rec.get('histograms') or [{}])[-1]
    c.equal('a decay (not an IRF) at 519 nm', (h.get('response_kind'), round((h.get('emission_wavelength_m') or 0) * 1e9, 3)), ('decay', 519.0))
    irf = json.loads(c.work_file('irf.json') or '{}').get('record', {}).get('histograms', [{}])[-1]
    fit = ev_call(c.lab, FLS, 'lifetime.fit', {'histogram': h, 'irf_histogram': irf, 'initial_lifetimes_s': [4e-09]}, 'fit') or {}
    want = (fit.get('lifetimes_s') or [None])[0]
    c.add('the evaluator can fit the decay', want is not None, fit.get('warning'))
    c.near('tau.txt = lifetime of the reconvolution fit in ns (+-0.05)', c.number_file('tau.txt'), want * 1e9 if want else -1, 0.05)


# ------------------------------------------------------------------ gold solutions
GOLD_DR = r'''I=hach_dr6000
chemlab run $I method from=400nm to=700nm step=2nm
chemlab run $I open lid && chemlab run $I load blank_01 && chemlab run $I close lid
chemlab run $I blank
chemlab run $I open lid && chemlab run $I unload && chemlab run $I load sample_01 && chemlab run $I close lid
chemlab run $I measure
chemlab run $I spectrum --json | jq -r '.max.at' > lambda_max.txt
cat lambda_max.txt'''

GOLD_J15_BLANK = r'''I=jasco_j1500
chemlab run $I power on
chemlab run $I source on --wait --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.fix' s.json
if [ $rc -eq 3 ] && jq -e '.error.code == "PURGE_NOT_READY"' s.json >/dev/null; then
  chemlab run $I purge 5L/min --wait
  chemlab run $I source on --wait
fi
chemlab run $I close lid
chemlab run $I method from=235nm to=190nm step=1nm
chemlab run $I blank --wait
chemlab run $I status'''

GOLD_J15_HELIX = r'''I=jasco_j1500
chemlab run $I status
chemlab run $I measure --wait --max 30min
chemlab run $I spectrum
chemlab run $I analyze structure conc=0.2mM path=1mm --json | jq -r '.result.fractions.Helix' > helix.txt
cat helix.txt'''

GOLD_IR = r'''I=jasco_ftir4700
chemlab run $I power on
chemlab run $I method from=400cm-1 to=4000cm-1 resolution=4cm-1
chemlab run $I open lid
chemlab run $I load ethanol_01 --json > l.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "NO_ACCESSORY"' l.json >/dev/null; then
  jq -r .error.fix l.json
  chemlab call $I accessory.install --mode atr
fi
chemlab run $I close lid
chemlab run $I blank
chemlab run $I open lid && chemlab run $I load ethanol_01 && chemlab run $I close lid
chemlab run $I measure
chemlab run $I spectrum --json | jq -r '.max.at' > band.txt
cat band.txt'''

GOLD_NMR = r'''I=magritek_spinsolve80
chemlab run $I power on
chemlab run $I load ethanol_01
chemlab run $I method nucleus=1H scans=8
chemlab run $I measure --json > m.json; rc=$?
if [ $rc -eq 3 ]; then
  jq -r '.error.code + ": " + .error.fix' m.json
  chemlab run $I method points=512
  chemlab run $I measure --json > m.json
fi
chemlab wait "$(jq -r .job m.json)" --max 10min
chemlab run $I spectrum --save-to nmr.json'''

GOLD_ESR = r'''I=bruker_magnettechesr5000
chemlab run $I measure --wait --json > m.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "SOURCE_OFF"' m.json >/dev/null; then
  chemlab run $I source on
  chemlab run $I measure --wait
fi
chemlab run $I spectrum
chemlab run $I analyze g-factor --json | jq -r '.result.g_apparent * 10000 | round / 10000' > g.txt
cat g.txt'''

GOLD_NRS = r'''I=jasco_nrs5500
chemlab run $I power on
chemlab run $I close door
chemlab run $I source on
chemlab run $I method from=200cm-1 to=1100cm-1
job=$(chemlab run $I measure --json | jq -r .job)
chemlab status "$job"
chemlab wait "$job" --max 10min
chemlab run $I spectrum --json | jq -r '.max.at' > peak.txt
cat peak.txt'''

GOLD_MQ = r'''I=bruker_minispecmq20
chemlab run $I power on
chemlab run $I close lid
chemlab run $I method sequence=inversion_recovery
chemlab run $I measure --wait --json > m.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "TEMPERATURE"' m.json >/dev/null; then
  chemlab run $I set-temp 37C --wait
  chemlab run $I measure --wait --max 10min
fi
chemlab run $I analyze relaxation --json | jq -r '.result.time_s * 1000' > t1.txt
cat t1.txt'''

GOLD_FP = r'''I=jasco_fp8350
chemlab run $I status
chemlab run $I measure --wait
chemlab run $I spectrum --json | jq -r '.max.at' > em.txt
cat em.txt'''

GOLD_FLS = r'''I=edinburgh_fls1000tcspc
chemlab run $I status
chemlab run $I method em=519nm
chemlab run $I measure --wait
chemlab run $I spectrum --save-to decay.json
chemlab run $I analyze lifetime irf=irf.json tau=4ns --json | jq -r '.result.lifetimes_s[0] * 1e9' > tau.txt
cat tau.txt'''

TASKS = [
    {'id': 'sp01_uvvis_scan', 'kind': 'multi-step (agent loads the cuvettes)', 'instruments': [DR], 'check': c_dr_scan,
     'instruction': 'On the Hach DR6000 (hach_dr6000), record the absorbance spectrum of the cuvette sample_01 from 400 to 700 nm in '
                    '2 nm steps against the blank cuvette blank_01, and write the wavelength of maximum absorbance (nm) into '
                    'lambda_max.txt.', 'gold': GOLD_DR},
    {'id': 'sp02_cd_blank_purge', 'kind': 'recovery: nitrogen purge not ready', 'instruments': [J15], 'check': c_j1500_blank,
     'instruction': 'The JASCO J-1500 CD spectrometer (jasco_j1500) is off; the solvent blank cell is already in the holder. Get it '
                    'ready and record the blank from 235 to 190 nm in 1 nm steps.', 'gold': GOLD_J15_BLANK},
    {'id': 'sp03_cd_structure', 'kind': 'long action + analysis report', 'instruments': [J15], 'world': w_j1500_ready,
     'check': c_j1500_helix,
     'instruction': 'On the JASCO J-1500 (jasco_j1500) the operator has recorded the blank with the standard method and put the '
                    'protein sample in (0.2 mM residue concentration, 1 mm cell). Measure its CD spectrum with that method and '
                    'write the helix fraction (0..1) of the secondary-structure estimate into helix.txt.', 'gold': GOLD_J15_HELIX},
    {'id': 'sp04_ftir_atr', 'kind': 'recovery: no sampling accessory', 'instruments': [IR], 'check': c_ftir,
     'instruction': 'Record the FT-IR spectrum of the sample ethanol_01 on the JASCO FT/IR-4700 (jasco_ftir4700) from 400 to '
                    '4000 cm-1 at 4 cm-1 resolution, with a background first. Use the ATR accessory. Write the wavenumber (cm-1) of '
                    'the strongest band into band.txt.', 'gold': GOLD_IR},
    {'id': 'sp05_nmr_window', 'kind': 'recovery: signals outside the spectral window', 'instruments': [NMR], 'check': c_nmr,
     'instruction': 'Insert the tube ethanol_01 into the Spinsolve 80 (magritek_spinsolve80), record a 1H spectrum with 8 scans and '
                    'save the spectrum record as nmr.json.', 'gold': GOLD_NMR},
    {'id': 'sp06_epr_g', 'kind': 'recovery: microwave bridge off', 'instruments': [ESR], 'world': w_esr_ready, 'check': c_esr,
     'instruction': 'On the Bruker ESR5000 (bruker_magnettechesr5000) the operator has recorded the blank and put the radical sample '
                    'in with the lid closed. Measure its EPR spectrum with the current method and write its g value (4 decimals) into '
                    'g.txt.', 'gold': GOLD_ESR},
    {'id': 'sp07_raman_job', 'kind': 'long action: job id (status, wait)', 'instruments': [NRS], 'check': c_raman,
     'instruction': 'Record the Raman spectrum of the mineral on the stage of the JASCO NRS-5500 (jasco_nrs5500) from 200 to '
                    '1100 cm-1. Start the measurement as a job, check its status, then wait for it. Write the Raman shift (cm-1) of '
                    'the strongest peak into peak.txt.', 'gold': GOLD_NRS},
    {'id': 'sp08_td_nmr_t1', 'kind': 'recovery: sample temperature off its reference', 'instruments': [MQ], 'world': w_mq_warm,
     'check': c_mq,
     'instruction': 'The Gd-chelate tube is in the minispec mq20 (bruker_minispecmq20); its reference temperature is 37 °C. Measure '
                    'T1 by inversion recovery (standard delays) and write T1 in ms into t1.txt.', 'gold': GOLD_MQ},
    {'id': 'sp09_fluorescence_max', 'kind': 'single long action + report', 'instruments': [FP], 'world': w_fp_ready, 'check': c_fp,
     'instruction': 'On the JASCO FP-8350 (jasco_fp8350) the blank has been recorded and the dye sample is in. Measure its emission '
                    'spectrum with the method used for the blank (do not change it) and write the emission maximum (nm) into em.txt.',
     'gold': GOLD_FP},
    {'id': 'sp10_tcspc_lifetime', 'kind': 'multi-step + analysis with a host record', 'instruments': [FLS], 'world': w_fls_irf_file,
     'check': c_fls,
     'instruction': 'The IRF of the Edinburgh FLS1000 (edinburgh_fls1000tcspc), measured with the standard method, is in irf.json. '
                    'The fluorescent reference is now in the holder. Record its decay with the standard method at 519 nm emission, '
                    'fit a single-exponential lifetime with that IRF and write the lifetime in ns into tau.txt.', 'gold': GOLD_FLS},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'spectroscopy'
    _t['config'] = cfg


def main(only=None, noact=True):
    return run_suite('spectroscopy', TASKS, only=only, noact=noact)
