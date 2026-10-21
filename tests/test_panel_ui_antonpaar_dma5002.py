"""front-panel tests for CHEM-003 antonpaar_dma5002 (panel_ui.json + scripts/asset_runtime/_panel_ui.py + _antonpaar_touch.py on
scripts/asset_runtime/antonpaar_dma5002.py): python3 -m pytest tests/test_panel_ui_antonpaar_dma5002.py -q

Every gesture goes through the session API (panel.touch / panel.switch); widgets are found by id or text in the structured frame (panel.frame,
evaluator).  Themes: the six key procedures of the menu survey; the DMA M menus (checks and adjustments, auto air check, repeated mode,
temperature table scan, special and pressure adjustments, density module / camera / air pump settings, U-View, non storage mode, value
visibility, FTP, time server, service); panel = direct capability; refusals shown as the device's own message; time-outs; render determinism
and the evidence PNGs; contract entries.

The water standard of the checks and adjustments is the density library's water record: CIPM / IAPWS-95 reference values with their
temperature dependence (data fix 2026-10-19; until then the PubChem water entry was quarantined and a stand-in liquid was used).  Real water
passes the water check of a factory-adjusted cell; a drifted cell (evaluator sensor offset) gives the failed check and the 'Reject'
recommendation that the air / water adjustment then corrects."""
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import antonpaar_dma5002 as rt  # noqa: E402
from antonpaar_panel_rig import Rig as _Rig  # noqa: E402
from asset_tool import validate_value  # noqa: E402

CONTRACT = rt.ASSET_DIR / 'asset.contract.json'
SURVEY_TASKS = ['perform a measurement', 'set measuring temperature of a method', 'perform a water check', 'air/water adjustment at 20 C',
                'set a temperature scan', 'check filling with U-View camera']
DONE_TASKS = set()
WATER_20 = round(rt.water_density(20.0) / 1000.0, 7)                       # 0.9982031 g/cm3 (Spieweck / Bettin)
WATER = 'water'
DRIFT = 1.0                                                                  # kg/m3: sensor offset of a drifted cell (evaluator), +0.001 g/cm3
WATER_TEMPLATE_20 = rt.template_density(json.loads((rt.ROOT / rt.TEMPLATE_REL).read_text())['substances']['water'], 293.15)   # 998.2067 kg/m3 (CIPM)


class Rig(_Rig):
    def __init__(self, power=True):
        super().__init__(rt, 'AntonpaarDma5002Runtime', power=('power_switch', 'on') if power else None)

    def load(self, sid='ethanol', volume_m3=2e-6, bubbles=0.0):
        """The trusted host fills the U-tube with the syringe."""
        self.call(self.E, 'sample.configure', substance_id=sid, volume_m3=volume_m3, bubble_fraction=bubbles, contamination_fraction=0.0)

    def clean(self):
        self.call(self.A, 'cleaning.start', volume_m3=1e-5)
        self.step(65)

    def go(self, *path):
        """From the main screen: <Menu>, then the rows of the menu levels."""
        self.home()
        self.tap('main_menu')
        for p in path[:-1]:
            assert self.row(p), p
        self.row(path[-1])                                                       # a row of an absent device answers with a message

    def home(self):
        for _ in range(3):
            if self.overlay():
                self.ov(self.overlay()['buttons'][0]['label'])
        scr, pid = self.page()
        if (scr, pid) != ('main', 'home'):
            self.tap(f'{pid}_crumb0')
        assert self.page() == ('main', 'home'), self.page()

    def readout(self):
        return self.val('display.readout')

    def field_text(self, i):
        return self.readout()['fields'][i]['text']

    def measure(self, seconds=120):
        self.home()
        assert self.tap('main_start')
        self.step(seconds)

    def air_water(self, humidity='50', apply=True):
        """Checks/Adjustments > Air/Water Adjustment with the library water as the water standard (apply=None: stay on the result)."""
        self.clean()
        self.go('Checks/Adjustments', 'Air/Water Adjustment')
        self.enter('Air humidity', humidity)
        self.tap_text('OK')
        self.step(80)
        assert self.page()[0] == 'fill_prompt', self.page()
        self.load(WATER)
        self.tap('prompt_ok')
        self.step(100)
        assert self.page()[0] == 'adjust_result', self.page()
        if apply is not None:
            self.tap('adj_apply' if apply else 'adj_reject')


# ---------------------------------------------------------------- the key procedures of the survey
class SurveyProcedures(unittest.TestCase):
    def test_perform_a_measurement(self):
        r = Rig()
        r.tap('main_quick')
        r.enter('Sample Name', 'EtOH 1')                                         # sample name if required (dma p.41)
        r.tap_text('OK')
        r.load()                                                                 # ~1 mL, the syringe stays in the inlet (dma p.40)
        self.assertEqual(r.page(), ('main', 'home'))
        self.assertTrue(r.tap('main_start'))
        self.assertTrue(r.has_button('Stop'))
        r.step(5)
        ro = r.readout()
        self.assertEqual((ro['progress']['color'], ro['progress']['text']), ('red', 'EtOH 1: Measuring'))
        r.step(60)
        ro = r.readout()
        self.assertEqual((ro['progress']['color'], ro['progress']['text']), ('green', 'EtOH 1: Finished'))
        self.assertEqual([f['text'] for f in ro['fields'][:4]], ['0.789300', '0.790721', '20.000', 'valid'])
        rec = r.val('data.measured')[-1]
        self.assertEqual((rec['sample_name'], rec['condition'], rec['values']['density']), ('EtOH 1', 'valid', 0.7893))
        self.assertTrue(ro['frozen'])                                            # results frozen (dma p.41)
        self.assertGreater(r.val('device.system')['beeps'], 0)
        r.clean()
        self.assertEqual(r.field_text(0), '0.789300')
        DONE_TASKS.add('perform a measurement')

    def test_set_measuring_temperature_of_a_method(self):
        r = Rig()
        r.go('Methods', 'Method Settings', 'Density', 'Density Module')
        self.assertEqual(r.page(), ('main', 'density_module_method'))
        r.enter('Temperature', '25')
        r.enter('Timeout', '900')
        r.choose('FillingCheck', 'Not active')
        r.enter('Measurement delay', '10')
        self.assertFalse(r.button('OK')['enabled'])                              # the precision class follows <Next> (dma p.35)
        r.tap_text('Next')
        self.assertEqual(r.page(), ('main', 'density_module_method2'))
        r.choose('Measurement finished by', 'Equilibrium slow')
        r.tap_text('OK')
        self.assertEqual(r.page(), ('main', 'method_menu'))
        s = r.val('methods.list')[0]['settings']
        self.assertEqual((s['den.temperature'], s['den.timeout_s'], s['den.filling_check'], s['den.delay_s'], s['den.precision']),
                         (25.0, 900, 'Not active', 10, 'Equilibrium slow'))
        self.assertAlmostEqual(r.val('thermal.target_k'), 298.15)                # the current method reaches the Peltier
        r.load('octan_1_ol')
        r.measure(400)
        rec = r.val('data.measured')[-1]
        self.assertAlmostEqual(rec['values']['density_temperature'], 25.0, delta=0.006)
        self.assertAlmostEqual(rec['values']['density'], 0.8262, delta=0.00001)  # template at 25 °C (PubChem)
        DONE_TASKS.add('set measuring temperature of a method')

    def test_perform_a_water_check(self):
        r = Rig()
        r.call(r.E, 'sensor.configure_error', offset_kg_m3=DRIFT, gain=0.0, pressure_offset_hpa=0.0)    # a drifted cell (+0.001 g/cm3)
        r.load(WATER)
        r.go('Checks/Adjustments', 'Checks')
        self.assertEqual([c[0] for c in r.rows()], ['AirCheck', 'WaterCheck'])
        r.row('WaterCheck')
        r.tap_text('Start')
        self.assertEqual(r.dialog(), 'check_fill')                               # fill degassed water bubble-free (dma p.25)
        r.ov('OK')
        self.assertEqual(r.page()[0], 'running')
        self.assertEqual(r.text('running_title'), 'Checks')
        r.step(100)
        self.assertEqual(r.page()[0], 'check_result')
        res = dict(tuple(x) for x in r.rows('check_result_rows'))
        self.assertEqual((res['Result'], res['Reference value'], res['Measured value']), ('not passed', '0.99820', '0.99921'))
        self.assertEqual(r.val('data.checks')[-1]['measured'], round(round((WATER_TEMPLATE_20 + DRIFT) / 0.005) * 0.005 / 1000.0, 7))
        self.assertTrue(r.has_button('Print or Export'))
        r.tap('check_result_ok')
        self.assertEqual(r.page(), ('main', 'checks'))
        self.assertEqual(r.rows()[1], ['WaterCheck', 'Water Check', 'not passed'])
        # after an air / water adjustment with water the check passes within its +/-0.00005 g/cm3
        r.home()
        r.air_water()
        r.go('Checks/Adjustments', 'Checks')
        r.row('WaterCheck')
        r.tap_text('Start')
        r.ov('OK')
        r.step(100)
        res = dict(tuple(x) for x in r.rows('check_result_rows'))
        self.assertEqual(res['Result'], 'passed')
        self.assertEqual(r.val('data.checks')[-1]['reference'], WATER_20)
        # real water passes the check of a factory-adjusted cell: CIPM 0.9982067 vs the Spieweck / Bettin reference 0.9982031 g/cm3
        r2 = Rig()
        r2.load(WATER)
        r2.go('Checks/Adjustments', 'Checks')
        r2.row('WaterCheck')
        r2.tap_text('Start')
        r2.ov('OK')
        r2.step(100)
        res = dict(tuple(x) for x in r2.rows('check_result_rows'))
        self.assertEqual((res['Result'], res['Reference value'], res['Measured value']), ('passed', '0.99820', '0.99821'))
        DONE_TASKS.add('perform a water check')

    def test_air_water_adjustment_at_20C(self):
        r = Rig()
        r.call(r.E, 'sensor.configure_error', offset_kg_m3=DRIFT, gain=0.0, pressure_offset_hpa=0.0)    # a drifted cell (+0.001 g/cm3)
        r.load()
        r.clean()                                                                # rinse the cell
        r.go('Checks/Adjustments', 'Air/Water Adjustment')
        self.assertEqual(r.page(), ('main', 'air_water'))
        r.tap_text('Air Pump on')                                                # dry it with the air pump (dma p.28)
        self.assertTrue(r.val('air.pump')['on'])
        self.assertEqual(r.cells()['Atmospheric pressure'], '1013.25 hPa')     # measured by the built-in sensor
        r.enter('Air humidity', '40')
        r.tap_text('OK')
        self.assertEqual((r.page()[0], r.text('running_title')), ('running', 'Adjustment'))
        r.step(80)                                                               # the air adjustment runs
        self.assertEqual(r.page()[0], 'fill_prompt')
        self.assertIn('Fill "Water" bubble-free', r.text('prompt_text'))
        self.assertFalse(r.val('air.pump')['on'])                                # stopped: dry cell stable
        r.load(WATER)
        r.tap('prompt_ok')
        r.step(100)
        self.assertEqual(r.page()[0], 'adjust_result')
        a = r.val('adjustment.state')
        self.assertEqual((a['type'], a['recommend'], a['rows'][0]['new']), ('air_water', 'Reject', WATER_20))   # deviation 0.001 > 0.0005 g/cm3
        self.assertAlmostEqual(a['rows'][0]['old'], (WATER_TEMPLATE_20 + DRIFT) / 1000.0, delta=1e-7)
        self.assertAlmostEqual(a['standards'][0]['ref'], rt.air_density(20.0, 1013.25, 40.0))
        self.assertEqual(r.text('adj_temp'), 'Temperature: 20.00 °C')
        r.tap('adj_apply')                                                       # first adjustment applied by decision of the user
        self.assertEqual(r.page(), ('main', 'home'))
        self.assertFalse(r.val('adjustment.correction')['factory'])
        r.air_water(humidity='40', apply=False)                                  # repeated: now recommended
        r.clean()
        r.go('Checks/Adjustments', 'Air/Water Adjustment')
        r.enter('Air humidity', '40')
        r.tap_text('OK')
        r.step(80)
        r.load(WATER)
        r.tap('prompt_ok')
        r.step(100)
        self.assertEqual(r.val('adjustment.state')['recommend'], 'Apply')
        r.tap('adj_apply')
        adj = r.val('data.adjustments')
        self.assertEqual([x['type'] for x in adj], ['Air / Water at 20 °C'] * 2)
        r.measure(100)
        self.assertAlmostEqual(r.val('data.measured')[-1]['values']['density'], WATER_20, delta=0.000005)
        r.clean()
        r.measure(100)                                                           # the empty cell reads the air density
        self.assertAlmostEqual(r.val('data.measured')[-1]['values']['density'], rt.air_density(20.0, 1013.25, 40.0) / 1000.0, delta=0.000005)
        DONE_TASKS.add('air/water adjustment at 20 C')

    def test_set_a_temperature_scan(self):
        r = Rig()
        r.load()
        r.go('Methods', 'Method Settings', 'Density', 'Measurement Mode')
        r.choose('Measurement Mode', 'TS (Temperature Scan)')
        r.tap_text('Next')
        self.assertEqual(r.page(), ('main', 'measurement_mode2'))
        r.enter('Start', '40')                                                   # start higher than end (dma p.38)
        r.enter('Step', '10')
        r.enter('End', '20')
        r.tap_text('OK')
        m = r.val('methods.list')[0]
        self.assertEqual((m['mode'], m['ts_start'], m['ts_step'], m['ts_end']), ('TS', 40.0, 10.0, 20.0))
        r.measure(1800)
        scan = [x for x in r.val('data.measured') if x['kind'] == 'scan']
        self.assertEqual([round(x['values']['density_temperature']) for x in scan], [40, 30, 20])
        self.assertTrue(scan[0]['values']['density'] < scan[1]['values']['density'] < scan[2]['values']['density'])
        self.assertEqual(scan[2]['values']['density'], 0.7893)
        DONE_TASKS.add('set a temperature scan')

    def test_check_filling_with_uview(self):
        r = Rig()
        r.load(bubbles=0.01)
        r.tap('qa_uview')                                                        # camera icon in the quick access area (dma p.40)
        self.assertEqual(r.page(), ('main', 'uview'))
        self.assertTrue(r.text('uv_state').startswith('U-tube: filled, bubbles'))
        w = r.widgets()
        img = w['uv_image']                                                      # the camera picture: the tube strip with bright bubbles
        self.assertEqual((img['type'], img['pw'], img['ph']), ('image', 160, 60))
        self.assertEqual(img['pixels'][28][40], 240)
        self.assertEqual(img['pixels'][30][100], 105)
        r.tap_text('Zoom In')
        w = r.widgets()
        self.assertIn('uv_image_zoom', w)
        self.assertNotIn('uv_image', w)
        r.tap_text('Position up')                                                # camera position buttons (dma p.55, Fig. E-1)
        self.assertEqual(r.val('device.settings')['camera.position'], 1)
        r.tap_text('Position right')
        self.assertEqual(r.val('device.settings')['camera.position_x'], 1)
        r.tap_text('Zoom In')
        r.tap_text('Save Picture')                                               # needs a USB flash drive
        self.assertEqual(r.dialog(), 'no_storage')
        r.ov('OK')
        r.tap_text('X')
        self.assertEqual(r.page(), ('main', 'home'))
        r.measure(100)
        self.assertEqual(r.readout()['fields'][3]['text'], 'Filling Warning')    # FillingCheck (dma p.42)
        self.assertEqual(r.val('data.measured')[-1]['condition'], 'Filling Warning')
        DONE_TASKS.add('check filling with U-View camera')


# ---------------------------------------------------------------- DMA M functions through the touch screen
class Workflows(unittest.TestCase):
    def test_auto_air_check_before_each_measurement(self):
        r = Rig()
        r.go('Setup', 'Measuring System Settings', 'Density Module')
        r.choose('Auto air check before each measurement', 'On')
        r.tap_text('OK')
        self.assertTrue(r.val('device.settings')['density.auto_air_check'])
        r.home()
        r.tap('main_start')
        self.assertEqual(r.page()[0], 'fill_prompt')
        self.assertIn('Auto air check: clean and dry', r.text('prompt_text'))
        r.tap('prompt_ok')
        r.step(80)
        self.assertIn('Auto air check passed', r.text('prompt_text'))
        r.load()
        r.tap('prompt_ok')
        r.step(100)
        self.assertEqual(r.page(), ('main', 'home'))
        self.assertEqual([(x['sample_name'], x['values']['density']) for x in r.val('data.measured')], [('Sample 1', 0.7893)])
        r.tap('main_start')                                                      # the cell is still filled: the air check fails
        r.tap('prompt_ok')
        r.step(80)
        self.assertIn('Auto air check failed', r.text('prompt_text'))
        r.tap('prompt_cancel')
        self.assertEqual((r.page(), r.val('run.state')['active']), (('main', 'home'), False))

    def test_repeated_mode(self):
        r = Rig()
        r.go('Methods', 'Method Settings', 'Density', 'Repeated Mode Settings')
        r.enter('Maximum number of determinations', '4')
        r.choose('Save Mode', 'valid two only')
        r.tap_text('OK')
        r.row('Measurement Mode')
        r.choose('Measurement Mode', 'RM (Repeated Mode)')
        r.tap_text('OK')
        m = r.val('methods.list')[0]
        self.assertEqual((m['mode'], m['rm_count'], m['rm_save'], m['rm_dev']), ('RM', 4, 'valid two only', None))
        r.load()
        r.measure(100)
        self.assertEqual(r.page()[0], 'fill_prompt')
        self.assertIn('fill 2 of 4', r.text('prompt_text'))
        r.tap('prompt_ok')                                                       # same liquid again: two results agree (dma p.36)
        r.step(100)
        recs = r.val('data.measured')
        self.assertEqual([x['sample_name'] for x in recs], ['Sample 1 #1', 'Sample 1 #2', 'Sample 1'])
        self.assertEqual(recs[-1]['values']['repeat_dev'], 0.0)

    def test_temperature_table_scan(self):
        r = Rig()
        r.go('Methods', 'Method Settings', 'Density', 'Measurement Mode')
        r.choose('Measurement Mode', 'TTS (Temperature Table Scan)')
        r.tap_text('Next')
        self.assertEqual(r.page(), ('main', 'tts_table'))
        for t in ('30', '25'):
            r.tap_text('New')
            r.enter('Temperature', t)
            r.tap_text('OK')
        self.assertEqual([c[0] for c in r.rows()], ['30.00 °C', '25.00 °C'])
        r.tap_text('OK')
        m = r.val('methods.list')[0]
        self.assertEqual((m['mode'], m['tts_temps']), ('TTS', [30.0, 25.0]))
        r.load()
        r.measure(900)
        scan = [x for x in r.val('data.measured') if x['kind'] == 'scan']
        self.assertEqual([round(x['values']['density_temperature']) for x in scan], [30, 25])

    def test_special_adjustment_gives_a_new_quantity(self):
        r = Rig()
        r.go('Checks/Adjustments', 'Other Adjustments', 'Density Module', 'Special Adjustments')
        r.enter('Name', 'Brine')
        r.enter('Unit', 'pct')
        r.tap_text('Next')
        r.enter('Reference value standard 1', '0')
        r.enter('Reference value standard 2', '10')
        r.tap_text('Next')                                                       # the special adjustment routine starts (dma p.30)
        self.assertEqual(r.page()[0], 'fill_prompt')                              # fill standard 1 (dma p.30)
        self.assertIn('Brine standard 1', r.text('prompt_text'))
        r.load('ethanol')
        r.tap('prompt_ok')
        r.step(100)
        self.assertIn('Brine standard 2', r.text('prompt_text'))
        r.clean()
        r.load('ethylene_glycol')
        r.tap('prompt_ok')
        r.step(100)
        self.assertEqual(r.page()[0], 'adjust_result')
        r.tap('adj_apply')
        self.assertIn('special:1', [q['id'] for q in r.val('quantities.list')])
        r.measure(100)
        self.assertAlmostEqual(r.val('data.measured')[-1]['values']['special:1'], 10.0, delta=1e-3)

    def test_pressure_sensor_adjustment_factory_reset_and_adjustment_data(self):
        r = Rig()
        r.go('Checks/Adjustments', 'Other Adjustments', 'DCB Module', 'Atmospheric Pressure Sensor Adjustment')
        r.enter('Atmospheric pressure', '1000')
        r.enter('Barometer ID', 'B12')
        r.tap_text('OK')
        self.assertEqual(r.page()[0], 'adjust_result')
        r.tap('adj_apply')
        self.assertEqual(r.val('air.pressure_hpa'), 1000.0)
        r.air_water()
        r.go('Checks/Adjustments', 'Other Adjustments', 'Density Module', 'Reset To Factory Adjustment')
        self.assertEqual(r.dialog(), 'factory_adjust')
        r.ov('OK')
        self.assertTrue(r.val('adjustment.correction')['factory'])
        self.assertEqual(r.val('air.pressure_hpa'), 1000.0)                     # the pressure sensor adjustment is kept
        r.go('Data Memory', 'Adjustment Data', 'Density Module', 'Density Adjustment')
        self.assertEqual([c[1] for c in r.rows()], ['Air / Water at 20 °C', 'Reset To Factory Adjustment'])
        r.row('Air / Water at 20 °C', col=1)
        r.tap_text('Details')
        self.assertEqual(r.page(), ('main', 'adjustment_details'))
        r.tap_text('Back')
        self.assertEqual(r.page(), ('main', 'density_adjustment_data'))
        r.go('Data Memory', 'Adjustment Data', 'DCB Module', 'Atmospheric Pressure Sensor Adjustment')
        self.assertEqual(len(r.rows()), 1)
        r.row('Atmospheric Pressure Sensor Adjustment', col=1)
        r.tap_text('Details')
        r.tap_text('Back')
        self.assertEqual(r.page(), ('main', 'pressure_adjustment_data'))
        r.go('Data Memory', 'Adjustment Data', 'Density Module', 'Density Adjustment Trend')
        self.assertEqual(len(r.rows()), 1)

    def test_wide_range_adjustment_needs_the_spieweck_bettin_air_table(self):
        r = Rig()
        r.go('Setup', 'Measuring System Settings', 'Density Module')
        r.choose('Air Table', 'CIPM')
        r.tap_text('OK')
        r.go('Checks/Adjustments', 'Other Adjustments', 'Density Module', 'Air / Water Wide Range Adjustment')
        r.tap_text('OK')
        self.assertEqual(r.dialog(), 'err_NOT_ALLOWED')                         # dma p.28
        r.ov('OK')
        r.go('Setup', 'Measuring System Settings', 'Density Module')
        r.choose('Air Table', 'Spieweck and Bettin')
        r.tap_text('OK')
        r.go('Checks/Adjustments', 'Other Adjustments', 'Density Module', 'Air / Water Wide Range Adjustment')
        r.tap_text('OK')
        self.assertEqual(r.page()[0], 'running')
        r.step(600)
        self.assertEqual(r.page()[0], 'fill_prompt')                            # air at 40 °C measured; air at 60 °C next (Table 8-1)
        self.assertIn('air at 60 °C', r.text('prompt_text'))
        r.tap('prompt_ok')
        r.step(900)
        self.assertIn('Fill "Water"', r.text('prompt_text'))                    # then water at 60 °C and 40 °C
        self.assertIn('(60 °C)', r.text('prompt_text'))
        self.assertAlmostEqual(r.val('thermal.target_k'), 333.15)
        r.tap('prompt_cancel')
        self.assertEqual(r.page(), ('main', 'home'))
        self.assertTrue(r.val('adjustment.correction')['factory'])

    def test_check_management(self):
        r = Rig()
        r.go('Setup', 'Expert Settings', 'Check Management')
        r.row('WaterCheck')
        r.tap_text('Edit')
        r.tap_text('Next')
        r.choose('Check execution reminder', 'On')
        r.enter('Interval', '7')
        r.tap_text('Next')
        r.enter('Lower deviation', '0.02')
        r.enter('Upper deviation', '0.02')
        r.tap_text('OK')
        c = next(x for x in r.val('checks.list') if x['name'] == 'WaterCheck')
        self.assertEqual((c['reminder'], c['interval_days'], c['lower_dev'], c['upper_dev']), (True, 7, 0.02, 0.02))
        r.tap_text('New')
        r.enter('Name', 'Ethanol check')
        r.tap_text('Next')
        r.tap_text('Next')
        r.choose('Quantity 1', 'Density')
        r.enter('Lower limit', '0.789')
        r.enter('Upper limit', '0.790')
        r.tap_text('OK')
        self.assertEqual(r.val('checks.list')[-1]['tolerances'][0], dict(q='density', lower=0.789, upper=0.79))
        r.load()
        r.go('Checks/Adjustments', 'Checks')
        r.row('Ethanol check')
        r.tap_text('Start')
        r.ov('OK')
        r.step(100)
        self.assertEqual(r.val('data.checks')[-1]['result'], 'passed')
        r.tap('check_result_ok')
        r.go('Data Memory', 'Check Data')
        self.assertEqual(r.rows()[0][1:], ['Ethanol check', 'passed'])

    def test_air_pump_quick_icon_favorite_and_settings(self):
        r = Rig()
        r.load()
        r.tap('qa_air_pump')
        self.assertTrue(r.val('air.pump')['on'])
        self.assertTrue(r.widgets()['qa_air_pump']['selected'])
        r.step(700)                                                              # stops after the set 700 s (gsf p.14)
        self.assertFalse(r.val('air.pump')['on'])
        r.tap('qa_favorites')
        r.row('Air Pump')
        self.assertTrue(r.val('air.pump')['on'])
        r.tap('qa_air_pump')
        self.assertFalse(r.val('air.pump')['on'])
        r.go('Setup', 'Measuring System Settings', 'Air Pump')
        r.choose('Stop air pump automatically after', 'Off')
        r.tap_text('OK')
        r.home()
        r.tap('qa_air_pump')
        r.step(800)
        self.assertTrue(r.val('air.pump')['on'])

    def test_formula_parameters_extrapolation_camera_settings(self):
        r = Rig()
        r.go('Methods', 'Method Settings', 'Density', 'Formula Parameters')
        r.choose('Density API Product Group', 'B - Fuels')
        r.tap_text('OK')
        self.assertEqual(r.val('methods.list')[0]['api_group'], 'B - Fuels')
        r.go('Setup', 'Measuring System Settings', 'Temperature Extrapolation Settings')
        n = len(r.rows())
        r.tap_text('New')
        r.enter('Temperature value', '25')
        r.tap_text('OK')
        self.assertEqual(r.val('device.lists')['extrapolation'][-1], dict(value=25.0, unit='°C'))
        self.assertEqual(len(r.rows()), n + 1)
        r.go('Setup', 'Measuring System Settings', 'Camera')
        r.choose('Automatically save a picture', 'of each measurement')
        r.tap_text('OK')
        r.go('Setup', 'Control Panel', 'Advanced Camera Settings')
        r.enter('Auto Exposure Luminance', '70')
        r.tap_text('OK')
        self.assertEqual(r.val('device.settings')['camera.luminance'], 70)
        r.load()
        r.measure(100)
        self.assertTrue(r.val('data.measured')[-1]['camera_image'])

    def test_non_storage_mode_and_value_visibility(self):
        r = Rig()
        r.go('Setup', 'System Security', 'Value Visibility')
        r.choose('Value Visibility', 'Only when measurement is finished')
        r.choose('Show Temperature Value(s)', 'Off')
        r.tap_text('OK')
        r.load()
        r.home()
        r.tap('qa_monitor')
        self.assertEqual(r.field_text(0), '')                                   # hidden in monitor mode (gsf p.30)
        r.measure(100)
        self.assertEqual(r.field_text(0), '0.789300')
        r.go('Setup', 'System Security', 'Non Storage Mode')
        r.choose('Non storage mode', 'On')
        r.tap_text('OK')
        self.assertEqual(r.dialog(), 'confirm_non_storage')
        r.ov('OK')
        self.assertTrue(r.val('device.settings')['security.non_storage'])
        n = len(r.val('data.measured'))
        r.measure(100)
        self.assertEqual(len(r.val('data.measured')), n)                       # results are shown, not stored (gsf p.29)
        self.assertEqual(r.field_text(0), '0.789300')

    def test_operator_logon_and_hidden_density_temperature(self):
        r = Rig()
        r.tap('main_quick')
        self.assertIn('Temperature', r.cells())
        r.home()
        r.tap('main_user_icon')
        r.choose('User name', 'Operator')
        r.enter('Password', 'operator')
        r.tap_text('OK')
        self.assertEqual(r.text('main_user_icon'), 'OPR')
        r.tap('main_quick')
        self.assertNotIn('Temperature', r.cells())                               # dma p.67: not for the Operator group
        r.home()
        r.go('Setup', 'System Security', 'Security Level')
        r.tap_text('OK')
        self.assertEqual(r.dialog(), 'err_PERMISSION_DENIED')

    def test_control_panel_time_server_and_ftp(self):
        r = Rig()
        r.go('Setup', 'Control Panel', 'Time Server Settings')
        r.choose('Use Time Server', 'On')
        r.choose('Time Zone', 'Asia/Shanghai')
        r.tap_text('OK')
        self.assertEqual(r.dialog(), 'confirm_time_server')
        epoch = r.val('device.system')['rows']
        r.ov('Yes')
        s = r.val('device.settings')
        self.assertEqual((s['timeserver.use'], s['timeserver.zone']), (True, 'Asia/Shanghai'))
        self.assertIsNotNone(epoch)
        r.go('Setup', 'Control Panel', 'FTP Settings')
        r.tap_text('New')
        r.enter('Name of the connection', 'LIMS')
        r.enter('FTP server path', 'ftp1')
        r.tap_text('OK')
        self.assertEqual(r.val('device.lists')['shares'][-1]['name'], 'LIMS')
        r.go('Setup', 'Control Panel', 'Sound Settings')
        self.assertEqual(r.page(), ('main', 'beeps'))
        r.go('Setup', 'Control Panel', 'Calibrate External Touch Screen')
        self.assertEqual(r.dialog(), 'no_ext_touch')

    def test_service_pages(self):
        r = Rig()
        r.load()
        r.measure(100)
        r.go('Service', 'Live Raw Data', 'DMA Density')
        self.assertIn('Density', r.cells())
        r.go('Service', 'Live Raw Data', 'DCB - Air Pressure')
        self.assertEqual(r.cells()['Air Pressure'], '1013.25 hPa')
        r.go('Service', 'System Information')
        self.assertEqual(r.cells()['Serial number'], 'DMA5002-0001')
        r.go('Service', 'Full System Backup')
        self.assertEqual(r.dialog(), 'no_storage')
        r.go('Setup', 'System Security', 'Unlock Service')
        self.assertEqual(r.dialog(), 'unlock_service')
        r.go('Service', 'Reset to Factory Settings')
        self.assertEqual(r.dialog(), 'confirm_factory_reset')
        r.ov('OK')
        self.assertEqual(r.val('data.measured'), [])                            # all data erased (gsf p.43)
        self.assertEqual(r.page(), ('main', 'home'))


# ---------------------------------------------------------------- runs, refusals, time-outs
class RunsAndRefusals(unittest.TestCase):
    def test_multi_fill(self):
        r = Rig()
        r.load()
        r.tap('main_quick')
        r.choose('Type', 'MF (Multi Fill)')
        r.enter('Number of fills', '2')
        r.tap_text('OK')
        r.measure(100)
        self.assertEqual(r.page()[0], 'fill_prompt')
        r.clean()
        r.load('octane')
        r.tap('prompt_ok')
        r.step(100)
        recs = r.val('data.measured')
        self.assertEqual([x['sample_name'] for x in recs], ['Sample 1 #1', 'Sample 1 #2', 'Sample 1'])
        self.assertTrue(recs[-1]['avg'])

    def test_refusals_leave_the_state_unchanged(self):
        r = Rig()
        r.load()
        before = copy.deepcopy(r.h.ap)
        r.go('Checks/Adjustments', 'Checks')
        r.row('AirCheck')
        r.tap_text('Start')
        r.ov('OK')
        self.assertEqual(r.dialog(), 'err_CELL_NOT_EMPTY')                      # air needs a clean and dry cell (dma p.26)
        after = copy.deepcopy(r.h.ap)
        for k in ('last_input_s', 'beeps'):
            before.pop(k), after.pop(k)
        self.assertEqual(before, after)
        r.ov('OK')
        r.call(r.A, 'adjustment.decide', expect='NO_PROCEDURE', apply=True)
        r.call(r.A, 'measurement.continue', expect='NO_PROCEDURE')

    def test_no_saving_while_measuring(self):
        r = Rig()
        r.load()
        r.tap('main_start')
        r.tap('main_quick')
        self.assertFalse(r.button('OK')['enabled'])
        r.tap_text('Home')
        r.call(r.A, 'settings.configure', expect='BUSY', settings={'density.auto_air_check': True})
        r.tap('main_stop')
        self.assertEqual(r.val('cycle.phase'), 'cancelled')
        self.assertEqual(r.val('data.measured'), [])

    def test_timeouts(self):
        r = Rig()
        r.go('Methods', 'Method Settings', 'Density', 'Density Module')
        r.enter('Timeout', '30')
        r.tap_text('Next')
        r.choose('Measurement finished by', 'Equilibrium slow')
        r.tap_text('OK')
        r.load()
        r.measure(60)                                                            # 50 s reading > 30 s timeout after equilibrium
        self.assertIn('Density Condition: Timeout Error.', [m['text'] for m in r.val('device.messages')])
        self.assertEqual(r.text('qa_messages'), 'X')
        r.step(30 * 60)                                                          # screen saver
        self.assertEqual(r.frame()['displays']['screen']['brightness'], 0)
        self.assertTrue(r.tap('main_menu'))                                      # the first touch only wakes the display
        self.assertEqual((r.page(), r.frame()['displays']['screen']['brightness']), (('main', 'home'), 100))


class PanelEqualsDirect(unittest.TestCase):
    def test_quick_settings_and_start_equal_direct_commands(self):
        a, b = Rig(), Rig()
        a.tap('main_quick')
        a.enter('Sample Name', 'X1')
        a.enter('Temperature', '25')
        a.tap_text('OK')
        b.call(b.A, 'quick.configure', settings={'sample_name': 'X1', 'den.temperature': 25.0})
        for r in (a, b):
            r.load('octan_1_ol')
        a.tap('main_start')
        b.call(b.A, 'measurement.start')
        for r in (a, b):
            r.step(200)
        for sid in ('sample.quick_settings', 'thermal.target_k', 'cycle.phase', 'density.indicated_kg_m3', 'methods.list'):
            self.assertEqual(a.val(sid), b.val(sid), sid)
        strip = lambda recs: [{k: v for k, v in x.items() if k not in ('time',)} for x in recs]   # noqa: E731
        self.assertEqual(strip(a.val('data.measured')), strip(b.val('data.measured')))

    def test_air_water_adjustment_equals_direct_commands(self):
        a, b = Rig(), Rig()
        a.air_water(humidity='45')
        b.clean()
        b.call(b.A, 'adjustment.start', type='air_water', humidity_pct=45.0, pressure_hpa=1013.25)
        b.step(80)
        b.load(WATER)
        b.call(b.A, 'measurement.continue')
        b.step(100)
        b.call(b.A, 'adjustment.decide', apply=True)
        self.assertEqual(a.val('adjustment.correction'), b.val('adjustment.correction'))
        self.assertEqual([{k: v for k, v in x.items() if k != 'time'} for x in a.val('data.adjustments')],
                         [{k: v for k, v in x.items() if k != 'time'} for x in b.val('data.adjustments')])


class BrokenTube(unittest.TestCase):
    """Error message 'Broken U-tube.' (dma p.57, Table F-2: turn off, contact Anton Paar) by the evaluator-only fault.inject: a red message,
    the run stops, measurements are refused with a message box; reported again at power-up; fault.clear = the cell replaced."""

    def test_broken_u_tube(self):
        r = Rig()
        r.call(r.A, 'fault.inject', expect='ACCESS_DENIED', code='broken_u_tube')
        r.load()
        r.step(60)
        r.home()
        r.tap('main_start')
        r.step(2)
        r.call(r.E, 'fault.inject', code='broken_u_tube')
        msg = r.val('device.messages')[-1]
        self.assertEqual((msg['level'], msg['text'], r.val('cycle.phase')), ('error', 'Broken U-tube.', 'failed'))
        r.tap('main_start')
        self.assertEqual(r.dialog(), 'err_BROKEN_U_TUBE')
        r.ov('OK')
        r.call(r.A, 'acquisition.start', expect='BROKEN_U_TUBE')
        r.call(r.A, 'messages.acknowledge', id=msg['id'])
        self.assertFalse(any(m['text'] == 'Broken U-tube.' for m in r.val('device.messages')))
        r.call(r.A, 'power.set', enabled=False)
        r.call(r.A, 'power.set', enabled=True)
        self.assertTrue(any(m['text'] == 'Broken U-tube.' for m in r.val('device.messages')))   # found again at power-up
        r.call(r.E, 'fault.clear')
        self.assertFalse(r.val('fault.broken_u_tube'))


class CallerRules(unittest.TestCase):
    """A panel gesture acts as the gesturing session: a run started from the screen belongs to that caller; another agent can neither stop it
    (panel <Stop>, direct acquisition.stop), confirm its next fill, nor decide on its adjustment result.  The rear switch is a hard switch
    (lead rule 2026-10-19): never refused, the instrument reacts as to a power loss."""

    def rig(self):
        r = Rig()
        r.h.register_principal('other', visibility='agent')
        r.B = r.h.session('other')
        return r

    def b_tap(self, r, wid):
        w = r.widgets()[wid]
        x, y, ww, hh = w['rect']
        return r.call(r.B, 'panel.touch', display_id=r.d, x=x + ww // 2, y=y + hh // 2)['accepted']

    def test_another_agent_cannot_stop_a_panel_run_but_the_switch_is_physical(self):
        r = self.rig()
        r.load()
        r.step(60)
        r.home()
        r.tap('main_start')
        op = r.h.operation_id
        self.assertEqual(r.h._owners[op], 'agent')
        self.assertEqual(r.A.poll(op)['status'], 'running')                       # the caller that started it may poll it
        r.call(r.B, 'acquisition.stop', expect='ACCESS_DENIED')
        run = copy.deepcopy(r.h.ap['run'])
        self.assertFalse(self.b_tap(r, 'main_stop'))                              # B's <Stop>: refused, the device says why
        self.assertEqual(r.dialog(), 'err_ACCESS_DENIED')
        r.ov('OK')
        self.assertEqual((r.h.phase, r.h.powered, r.h.operation_id, r.h.ap['run']), ('running', True, op, run))
        self.assertEqual(r.A.cancel(op, 'c1')['status'], 'cancelled')            # the owner cancels its own panel run
        r.tap('main_start')                                                       # a new run of A ...
        op = r.h.operation_id
        self.assertTrue(r.call(r.B, 'panel.switch', input_id='power_switch', position='off')['accepted'])   # ... B's rear switch: physical
        self.assertFalse(r.h.powered)
        self.assertNotEqual(r.A.poll(op)['status'], 'running')                   # the run ends as on a power loss

    def test_another_agent_cannot_continue_a_fill_or_decide_an_adjustment(self):
        r = self.rig()
        r.load()
        r.tap('main_quick')
        r.choose('Type', 'MF (Multi Fill)')
        r.enter('Number of fills', '2')
        r.tap_text('OK')
        r.measure(100)
        self.assertEqual(r.page()[0], 'fill_prompt')
        self.assertFalse(self.b_tap(r, 'prompt_ok'))                              # B cannot confirm A's next fill
        r.ov('OK')
        self.assertFalse(self.b_tap(r, 'prompt_cancel'))
        r.ov('OK')
        r.call(r.B, 'cleaning.start', volume_m3=1e-5)                            # B rinses the cell, the trusted host refills it
        r.step(65)
        r.load('octane')
        self.assertFalse(self.b_tap(r, 'prompt_ok'))                              # the waiting run still belongs to A
        r.ov('OK')
        self.assertEqual(r.val('run.state')['stage'], 'await_fill')
        r.tap('prompt_cancel')                                                   # A stops its own run
        self.assertFalse(r.val('run.state')['active'])
        r.call(r.E, 'sensor.configure_error', offset_kg_m3=DRIFT, gain=0.0, pressure_offset_hpa=0.0)
        r.clean()
        r.air_water(apply=None)
        self.assertEqual(r.page()[0], 'adjust_result')
        self.assertFalse(self.b_tap(r, 'adj_apply'))                              # A's adjustment result: B may not apply it
        r.ov('OK')
        r.call(r.B, 'adjustment.decide', expect='ACCESS_DENIED', apply=False)
        self.assertEqual(r.val('adjustment.state')['kind'], 'adjust_result')
        r.tap('adj_apply')
        self.assertFalse(r.val('adjustment.correction')['factory'])


class CameraAndKeyboard(unittest.TestCase):
    def test_camera_position_and_luminance_change_the_picture(self):
        r = Rig()
        r.load()
        r.tap('qa_uview')
        px = r.widgets()['uv_image']['pixels']
        self.assertEqual((px[30][80], px[30][2], px[2][80]), (105, 70, 70))      # liquid in the tube, the cell block around it
        for _ in range(5):
            r.tap_text('Position up')
        moved = r.widgets()['uv_image']['pixels']
        self.assertEqual(r.val('device.settings')['camera.position'], 5)
        self.assertNotEqual(moved, px)
        self.assertEqual(moved[20][80], 105)                                     # the tube is seen 10 px higher
        for _ in range(10):
            r.tap_text('Position left')
        self.assertEqual(r.widgets()['uv_image']['pixels'][20][50], 70)          # 40 px to the left: the tube's end comes into view
        r.tap_text('X')
        r.go('Setup', 'Control Panel', 'Advanced Camera Settings')
        r.enter('Auto Exposure Luminance', '100')                               # dma p.55: brighter camera picture
        r.tap_text('OK')
        r.home()
        r.tap('qa_uview')
        self.assertEqual(r.widgets()['uv_image']['pixels'][20][80], 210)

    def test_keyboard_layout_setting_changes_the_on_screen_keyboard(self):
        r = Rig()
        r.tap('main_quick')
        r.row('Sample Name')
        keys = [b['label'] for b in r.overlay()['buttons'] if b['id'].startswith('key:')]
        self.assertLess(keys.index('y'), keys.index('u'))                       # US: QWERTY
        r.ov('Cancel')
        r.home()
        r.go('Setup', 'Control Panel', 'Regional Settings')
        r.choose('Keyboard Layout', 'German')
        r.tap_text('OK')
        r.ov('Yes')                                                              # the instrument reboots (gsf p.13)
        r.tap('main_quick')
        r.row('Sample Name')
        keys = [b['label'] for b in r.overlay()['buttons'] if b['id'].startswith('key:')]
        self.assertLess(keys.index('z'), keys.index('u'))                       # QWERTZ with umlauts
        self.assertIn('ü', keys)
        r.type('Öl 2')
        r.ov('OK')
        r.tap_text('OK')
        self.assertEqual(r.val('sample.quick_settings')['sample_name'], 'Öl 2')
        r.go('Setup', 'Control Panel', 'Regional Settings')
        r.choose('Keyboard Layout', 'French')
        r.tap_text('OK')
        r.ov('Yes')
        r.tap('main_quick')
        r.row('Sample Name')
        keys = [b['label'] for b in r.overlay()['buttons'] if b['id'].startswith('key:')]
        self.assertEqual(keys[:3], ['a', 'z', 'e'])                             # AZERTY


class RenderingAndContract(unittest.TestCase):
    def test_render_determinism_and_capture(self):
        r = Rig()
        r.load()
        r.measure(100)
        p1, p2 = r.png(), r.png()
        self.assertEqual(p1, p2)
        cap = r.call(r.A, 'panel.capture', display_id='screen')
        self.assertEqual((cap['image']['width'], cap['image']['height']), (800, 480))
        self.assertEqual(cap['image']['sha256'], hashlib.sha256(p1).hexdigest())
        led = r.call(r.A, 'panel.capture', display_id='power_led')
        self.assertEqual(led['image']['width'], 24)
        r.call(r.A, 'panel.switch', input_id='power_switch', position='off')
        self.assertEqual((r.page()[0], r.val('power.enabled')), ('off', False))
        self.assertFalse(r.frame()['displays']['power_led']['on'])
        r.call(r.A, 'panel.switch', input_id='power_switch', position='on')
        self.assertEqual((r.page(), r.val('device.users')['current']), (('main', 'home'), 'Administrator'))

    def test_frame_is_evaluator_only_and_gestures_validate(self):
        r = Rig()
        self.assertEqual(r.call(r.A, 'panel.frame', expect='ACCESS_DENIED')['error']['code'], 'ACCESS_DENIED')
        r.call(r.A, 'panel.touch', expect='INVALID_ARGUMENT', display_id='screen', x=900, y=10)
        r.call(r.A, 'panel.switch', expect='INVALID_ARGUMENT', input_id='power_switch', position='middle')
        caps = r.A.describe(instance_id=r.h.instance_id)['capabilities']
        self.assertIn('panel.touch', caps)
        self.assertNotIn('panel.frame', caps)
        self.assertNotIn('sensor.configure_error', caps)

    def test_sensor_error_parameters_default_zero(self):
        r = Rig()
        self.assertEqual(r.val('sensor.errors'), dict(offset_kg_m3=0.0, gain=0.0, pressure_offset_hpa=0.0))
        r.call(r.E, 'sensor.configure_error', offset_kg_m3=0.5, gain=0.0, pressure_offset_hpa=0.0)
        r.load()
        r.measure(100)
        self.assertAlmostEqual(r.val('data.measured')[-1]['values']['density'], 0.7898, delta=0.000005)

    def test_contract_entries(self):
        c = json.loads(CONTRACT.read_text())
        self.assertEqual(c['asset_version'], rt.ASSET_VERSION)
        self.assertEqual(c['extensions']['chemistry_lab.panel_ui']['artifact_id'], 'panel_ui')
        for k in list(rt.PANEL_CAPS) + list(rt.DEVICE_TUPLES) + list(rt.DMA_CAPS):
            self.assertIn(k, c['capabilities'])
        for k in ('power_switch', 'power_led', 'screen'):
            self.assertIn(k, c['components'])
        meta = rt.export_antonpaar_dma5002_contract_metadata()
        for k, v in meta['capabilities'].items():
            for key in ('input_schema', 'output_schema', 'kind', 'visibility'):
                self.assertEqual(c['capabilities'][k][key], v[key], (k, key))
        r = Rig()
        for sid, v in r.h.read('evaluator').items():
            self.assertEqual(validate_value(v, meta['state_slots'][sid]['value_schema']), [], sid)


if __name__ == '__main__':
    unittest.main()
