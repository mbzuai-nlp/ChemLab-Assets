"""quant_analyzer class acceptance tasks (10): gold solutions use `chemlab run`; the checkers read the evaluator view and the
agent's work files.  Truth values are the template values of the samples the operator put in (tasks/quant_analyzer_world.py).

World set-up is the operator's: samples on the nebulizer / in the carousel / in the chamber, the instrument brought to a stage
(blank and standards measured) with the instrument's own calls before the agent starts.
"""
from . import quant_analyzer_world as W
from .common import run_suite

ICPE, TOC, AA, EDX, DMA, VARIO, IRMS, SEAL = W.ICPE, W.TOC, W.AA, W.EDX, W.DMA, W.VARIO, W.IRMS, W.SEAL


# ------------------------------------------------------------------ worlds
def w_icpe_standards_sample(lab):
    W.bring_up(lab, ICPE, 'standards')
    W.load(lab, ICPE, 'sample')


def w_toc_ready(lab):
    W.bring_up(lab, TOC, 'calibrated', sample=True)


def w_icpe_just_on(lab):
    w = W.World(lab, ICPE)
    w.ag('power.set', enabled=True)
    w.ag('method.configure', **w.example('method.configure'))
    w.adv(5.0)


def w_dma_oxygen_off(lab):
    w = W.bring_up(lab, DMA, 'calibrated')
    w.ag('gas.set', enabled=False)
    w.adv(5.0)


def w_aa_ready(lab):
    W.bring_up(lab, AA, 'calibrated', sample=True)


def w_vario_standards(lab):
    W.bring_up(lab, VARIO, 'standards')


def w_irms_ready(lab):
    W.bring_up(lab, IRMS, 'ready')
    W.irms_declare(lab, 'leaf_a', {'13C': -0.0281, '15N': 0.0034})


def w_seal_ready(lab):
    W.bring_up(lab, SEAL, 'ready')


def w_edx_standards_sample(lab):
    W.bring_up(lab, EDX, 'standards')
    W.load(lab, EDX, 'sample')


def w_two_calibrated(lab):
    W.bring_up(lab, ICPE, 'calibrated', sample=True)
    W.bring_up(lab, AA, 'calibrated', sample=True)


# ------------------------------------------------------------------ checkers
def c_icpe_cu(c):
    e = c.ev(ICPE)
    c.add('a curve is selected', e['calibration.ready'])
    c.near('ICPE result Cu = 0.99 mg/L (±2 %)', e['result.cu_kg_m3'], 0.00099, 0.00099 * 0.02)
    c.near('cu_mg_l.txt = 0.99 (±0.02)', c.number_file('cu_mg_l.txt'), 0.99, 0.02)


def c_toc_tc(c):
    e = c.ev(TOC)
    c.near('TOC-L result TC = 1.898 mg/L (±2 %)', e['result.tc_kg_m3'], 0.001898, 0.001898 * 0.02)
    c.near('tc_mg_l.txt = 1.898 (±0.04)', c.number_file('tc_mg_l.txt'), 1.898, 0.04)


def c_icpe_lit(c):
    e = c.ev(ICPE)
    c.add('vacuum ready', e['vacuum.ready'], e.get('vacuum.pressure_pa'))
    c.add('plasma lit', e['plasma.lit'])


def c_dma_hg(c):
    e = c.ev(DMA)
    c.add('slot 5 entered in the sample table as soil_5', any(isinstance(x, dict) and x.get('slot_index') == 5 and x.get('name') == 'soil_5'
                                                           for x in e.get('table.entries') or []), e.get('table.entries'))
    c.near('DMA result Hg = 0.723 mg/kg (±3 %)', e['result.mass_fraction_kg_kg'], 7.23e-07, 7.23e-07 * 0.03)
    c.near('hg_mg_kg.txt = 0.723 (±0.025)', c.number_file('hg_mg_kg.txt'), 0.723, 0.025)


def c_aa_three(c):
    e = c.ev(AA)
    c.equal('three repeats measured', e['measurement.completed_repeats'], 3)
    c.add('flame lit', e['flame.lit'])
    c.near('AAS result Cu = 0.083 mg/L (±3 %)', e['result.concentration_kg_m3'], 8.3e-05, 8.3e-05 * 0.03)
    c.near('cu_aa.txt = 0.083 (±0.003)', c.number_file('cu_aa.txt'), 0.083, 0.003)


def c_vario_c(c):
    e = c.ev(VARIO)
    c.near('vario result C = 39.4 % (±1 % rel.)', e['result.c_fraction'], 0.394, 0.394 * 0.01)
    c.near('c_pct.txt = 39.4 (±0.4)', c.number_file('c_pct.txt'), 39.4, 0.4)


def c_irms(c):
    e = c.ev(IRMS)
    c.add('13C normalisation fitted', '13C' in ((e.get('calibration.state') or {}).get('normalization') or {}), e.get('calibration.state'))
    c.near('d13c.txt = -28.1 permil (±0.5)', c.number_file('d13c.txt'), -28.1, 0.5)


def c_seal(c):
    e = c.ev(SEAL)
    c.equal('the run completed', (e.get('run.status') or {}).get('state'), 'completed')
    c.add('river_1 in cup A:1', any(isinstance(v, dict) and v.get('item_id') == 'river_1' for v in (e.get('sampler.contents') or {}).values())
          or 'A:1' in (e.get('sampler.contents') or {}) or 'A1' in (e.get('sampler.contents') or {}), e.get('sampler.contents'))
    c.near('no3_n.txt = 1.80 (±0.06)', c.number_file('no3_n.txt'), 1.80, 0.06)


def c_edx(c):
    e = c.ev(EDX)
    cu = next((x.get('mass_fraction') for x in e.get('result.mass_fractions') or [] if x.get('element') == 'Cu'), None)
    c.near('EDX result Cu = 2.04 % (±3 % rel.)', cu, 0.0204, 0.0204 * 0.03)
    c.near('cu_pct.txt = 2.04 (±0.06)', c.number_file('cu_pct.txt'), 2.04, 0.06)


def c_higher(c):
    c.add('ICPE sample measured', c.ev(ICPE)['result.cu_kg_m3'] is not None)
    c.add('AAS sample measured', c.ev(AA)['result.concentration_kg_m3'] is not None)
    c.equal('higher.txt names the ICPE', c.work_file('higher.txt'), ICPE)


# ------------------------------------------------------------------ gold solutions
GOLD_01 = r'''chemlab run shimadzu_icpe9800 records
chemlab run shimadzu_icpe9800 calibrate cal_cu --from record_2,record_3,record_4 --reference shimadzu_epa_ipc_assigned --levels 100,10,1
chemlab run shimadzu_icpe9800 use-curve cal_cu
chemlab run shimadzu_icpe9800 measure --wait
chemlab run shimadzu_icpe9800 result --json | jq -r '.rows[] | select(.analyte | startswith("Cu")) | .value * 1000' > cu_mg_l.txt
cat cu_mg_l.txt'''

GOLD_02 = r'''chemlab run shimadzu_tocl status
chemlab run shimadzu_tocl measure --wait
chemlab run shimadzu_tocl result --json | jq -r '.rows[] | select(.analyte == "TC") | .value * 1000' > tc_mg_l.txt
cat tc_mg_l.txt'''

GOLD_03 = r'''chemlab run shimadzu_icpe9800 source on --wait --json > s.json; rc=$?
jq -c '{code: .error.code, fix: .error.fix}' s.json
if [ $rc -eq 3 ] && jq -e '.error.code == "VACUUM_NOT_READY"' s.json >/dev/null; then
  chemlab call shimadzu_icpe9800 vacuum.set --enabled true
  chemlab wait 60
  chemlab run shimadzu_icpe9800 source on --wait
fi
chemlab run shimadzu_icpe9800 status'''

GOLD_04 = r'''chemlab run milestone_dma80 load soil_5 --slot 5 --mass 50mg
chemlab run milestone_dma80 measure --slot 5 --wait --json > m.json; rc=$?
jq -c '{code: .error.code, fix: .error.fix}' m.json
if [ $rc -eq 3 ] && jq -e '.error.code == "NOT_READY"' m.json >/dev/null; then
  chemlab run milestone_dma80 source on --wait
  chemlab run milestone_dma80 measure --slot 5 --wait
fi
chemlab run milestone_dma80 result --json | jq -r '.rows[] | select(.analyte == "Hg mass fraction") | .value * 1000000' > hg_mg_kg.txt
cat hg_mg_kg.txt'''

GOLD_05 = r'''chemlab run jena_contraa800 method repeats=3 --json > m.json; rc=$?
jq -c '{code: .error.code, fix: .error.fix}' m.json
if [ $rc -eq 3 ] && jq -e '.error.code == "SOURCE_ON"' m.json >/dev/null; then
  chemlab run jena_contraa800 source off
  chemlab run jena_contraa800 method repeats=3
  chemlab run jena_contraa800 source on --wait
fi
chemlab run jena_contraa800 measure --wait
chemlab run jena_contraa800 result --json | jq -r '.rows[0].value * 1000' > cu_aa.txt
cat cu_aa.txt'''

GOLD_06 = r'''chemlab run elementar_varioel calibrate C --from 1,2,3 --reference oem_sulfanilic_acid
chemlab run elementar_varioel measure --slots 4 --wait
chemlab run elementar_varioel result --json | jq -r '.rows[] | select(.analyte == "slot 4 C") | .value * 100' > c_pct.txt
cat c_pct.txt'''

GOLD_07 = r'''I=elementar_isoprimeprecision
chemlab run $I measure --time 10min
chemlab run $I load usgs40 --amount 4umol --delay 100s
chemlab run $I load usgs41 --amount 4umol --delay 250s
chemlab run $I load leaf_a --amount 4umol --delay 400s
chemlab run $I wait --max 15min
chemlab run $I result
chemlab run $I calibrate 13C --from 2=usgs40,3=usgs41
chemlab run $I result --json | jq -r '.rows[] | select(.peak == 4 and .isotope == "13C") | .value * 1000' > d13c.txt
cat d13c.txt'''

GOLD_08 = r'''S=seal_aa500
chemlab run $S load river_1 --slot A:1 --volume 10mL
chemlab run $S measure --cups S:22:baseline,S:1:calibrant,S:2:calibrant,S:3:calibrant,S:4:calibrant,A:1:sample,S:22:baseline --wait --max 2h
chemlab run $S result --json | jq -r '.rows[] | select(.analyte == "A:1 ch1") | .value * 1000' > no3_n.txt
cat no3_n.txt'''

GOLD_09 = r'''chemlab run shimadzu_edx7200 calibrate bm1 --from record_2,record_3,record_4 --reference blackmass_icp_f --levels 0.25,0.5,1
chemlab run shimadzu_edx7200 use-curve bm1
chemlab run shimadzu_edx7200 measure --wait
chemlab run shimadzu_edx7200 result --json | jq -r '.rows[] | select(.analyte | startswith("Cu")) | .value * 100' > cu_pct.txt
cat cu_pct.txt'''

GOLD_10 = r'''chemlab run shimadzu_icpe9800 measure --wait
chemlab run jena_contraa800 measure --wait
a=$(chemlab run shimadzu_icpe9800 result --json | jq -r '.rows[] | select(.analyte | startswith("Cu")) | .value')
b=$(chemlab run jena_contraa800 result --json | jq -r '.rows[0].value')
echo "icpe=$a aas=$b"
if [ "$(echo "$a > $b" | sed 's/e/*10^/g' | bc -l)" = 1 ]; then echo shimadzu_icpe9800 > higher.txt; else echo jena_contraa800 > higher.txt; fi
cat higher.txt'''

TASKS = [
    {'id': 'qa01_icpe_calibrate_quantify', 'kind': 'multi-step: calibrate from records, use, measure, report', 'instruments': [ICPE],
     'world': w_icpe_standards_sample, 'check': c_icpe_cu, 'gold': GOLD_01,
     'instruction': 'The ICP-OES (shimadzu_icpe9800) is lit; a blank and three standards have been measured (records record_2, record_3, '
                    'record_4: the multi-element standard shimadzu_epa_ipc_assigned diluted 100, 10 and 1 times) and the unknown sample is '
                    'now on the nebulizer. Fit a calibration curve named cal_cu from the three standards, use it, measure the sample and '
                    'write its copper concentration in mg/L, as a plain number, into cu_mg_l.txt.'},
    {'id': 'qa02_toc_tc', 'kind': 'single measurement + report', 'instruments': [TOC], 'world': w_toc_ready, 'check': c_toc_tc, 'gold': GOLD_02,
     'instruction': 'The TOC-L (shimadzu_tocl) is calibrated for total carbon (TC) and a water sample is in place. Measure it and write its '
                    'TC in mg/L, as a plain number, into tc_mg_l.txt.'},
    {'id': 'qa03_recover_icpe_vacuum', 'kind': 'recovery: plasma refused (vacuum not ready)', 'instruments': [ICPE], 'world': w_icpe_just_on,
     'check': c_icpe_lit, 'gold': GOLD_03,
     'instruction': 'The ICP-OES (shimadzu_icpe9800) has just been switched on and its method is set. Light the plasma so that the '
                    'instrument is ready to measure.'},
    {'id': 'qa04_recover_dma_oxygen', 'kind': 'recovery: measurement refused (oxygen off) + sample table', 'instruments': [DMA],
     'world': w_dma_oxygen_off, 'check': c_dma_hg, 'gold': GOLD_04,
     'instruction': 'On the DMA-80 (milestone_dma80) the calibration is ready and the boat in carousel slot 5 holds 50.0 mg of a soil '
                    'reference material. Enter it in the sample table as soil_5 with its mass, measure it and write its mercury mass '
                    'fraction in mg/kg, as a plain number, into hg_mg_kg.txt.'},
    {'id': 'qa05_recover_aa_method_flame', 'kind': 'recovery: method change refused (flame lit)', 'instruments': [AA], 'world': w_aa_ready,
     'check': c_aa_three, 'gold': GOLD_05,
     'instruction': 'The flame AAS (jena_contraa800) is calibrated for Cu and the sample is in place. Measure it with 3 replicate readings '
                    '(3 repeats) and write its Cu concentration in mg/L, as a plain number, into cu_aa.txt. Leave the flame lit.'},
    {'id': 'qa06_vario_carbon', 'kind': 'multi-step: element curve from autosampler slots, sequence, report', 'instruments': [VARIO],
     'world': w_vario_standards, 'check': c_vario_c, 'gold': GOLD_06,
     'instruction': 'The vario EL cube (elementar_varioel) has run a blank (slot 0) and three sulfanilic acid standards (slots 1, 2 and 3; '
                    'reference oem_sulfanilic_acid). A wheat flour capsule is in slot 4. Calibrate carbon from the three standards, '
                    'measure slot 4 and write its carbon content in mass %, as a plain number, into c_pct.txt.'},
    {'id': 'qa07_irms_delta13c', 'kind': 'long action: timed acquisition job with injections, normalisation', 'instruments': [IRMS],
     'world': w_irms_ready, 'check': c_irms, 'gold': GOLD_07,
     'instruction': 'The isoprime precisION (elementar_isoprimeprecision) is tuned for CO2 and its working reference gas is assigned. Run a '
                    '10-minute acquisition; during it inject 4 µmol each of the reference materials USGS40 (100 s after the start) and '
                    'USGS41 (at 250 s), and 4 µmol of the sample leaf_a (at 400 s). Then normalise δ13C with the two reference '
                    'materials and write the normalised δ13C of leaf_a in ‰, as a plain number, into d13c.txt.'},
    {'id': 'qa08_sfa_nitrate', 'kind': 'long action: segmented-flow run job', 'instruments': [SEAL], 'world': w_seal_ready, 'check': c_seal,
     'gold': GOLD_08,
     'instruction': 'The AA500 (seal_aa500) is started up; calibrants std0..std3 are in cups S:1..S:4 and wash water in S:22. Put the '
                    'river water sample river_1 (provided by the lab) into cup A:1 (10 mL), run a baseline cup (S:22), the four '
                    'calibrants, the sample and a final baseline cup, and write the nitrate+nitrite-N result of the sample (channel '
                    'ch1) in mg/L, as a plain number, into no3_n.txt.'},
    {'id': 'qa09_xrf_copper', 'kind': 'multi-step: XRF curve from records with reference fractions', 'instruments': [EDX],
     'world': w_edx_standards_sample, 'check': c_edx, 'gold': GOLD_09,
     'instruction': 'The EDX-7200 (shimadzu_edx7200) has measured a blank and three pellets of the black-mass reference blackmass_icp_f '
                    'at reference fractions 0.25, 0.5 and 1 (records record_2, record_3, record_4). The unknown black mass sample is now '
                    'in the chamber. Fit a curve named bm1 from the three pellets, use it, measure the sample and write its Cu mass '
                    'fraction in %, as a plain number, into cu_pct.txt.'},
    {'id': 'qa10_two_instruments', 'kind': 'two instruments: measure both, compare', 'instruments': [ICPE, AA], 'world': w_two_calibrated,
     'check': c_higher, 'gold': GOLD_10,
     'instruction': 'Both the ICP-OES (shimadzu_icpe9800) and the flame AAS (jena_contraa800) are calibrated for copper, each with a '
                    'sample in place. Measure both samples and write the id of the instrument whose sample has the higher Cu '
                    'concentration into higher.txt.'},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'quant_analyzer'
    _t.setdefault('config', W.bare)                  # the class world is applied to the bare members (see W.bare)


def main(only=None, noact=True):
    return run_suite('quant_analyzer', TASKS, only=only, noact=noact)
