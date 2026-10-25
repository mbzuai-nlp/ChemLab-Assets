import json
import unittest

from helpers import class_suite, run, tmpdir

SpectroscopyClass = class_suite('spectroscopy')


class AnalysisInputs(unittest.TestCase):
    """The lowering table leaves the analysis steps whose inputs are measured arrays unchecked (None); here the native analysis
    call must carry exactly the arrays the instrument's own result holds (and, for HELIOS, the column at the asked wavelength)."""

    @classmethod
    def setUpClass(cls):
        from chemlab.compile.tasks.spectroscopy_world import config
        from chemlab.glab import GenericLab, single
        cls.lab = GenericLab(tmpdir() + '/lab', config(single(['jasco_nrs5500', 'applied_sx20', 'ultrafast_helios'])))
        for cmd in ('run jasco_nrs5500 power on', 'run jasco_nrs5500 close door', 'run jasco_nrs5500 source on',
                    'run jasco_nrs5500 method from=200cm-1 to=1100cm-1', 'run jasco_nrs5500 measure --wait',
                    'run applied_sx20 method wavelength=480nm', 'run applied_sx20 power on', 'run applied_sx20 measure --wait',
                    'run ultrafast_helios power on',
                    'run ultrafast_helios method delays=-1ps,0ps,1ps,2ps,5ps,10ps,20ps,50ps,100ps,200ps,500ps,1000ps',
                    'run ultrafast_helios measure --wait'):
            r = run(cls.lab, cmd)
            assert r.exit_code == 0, (cmd, r.out, r.err)

    @classmethod
    def tearDownClass(cls):
        cls.lab.stop()

    def calls(self, cmd):
        n = len(self.lab.core.log.entries)
        r = run(self.lab, cmd + ' --json')
        self.assertEqual(r.exit_code, 0, r.out + r.err)
        return [c for e in self.lab.core.log.entries[n:] for c in e['backend_calls'] if c.get('op') == 'invoke'], json.loads(r.out)

    def result(self, iid, cap):
        return self.lab.backend.invoke('agent', iid, cap, {}, 'test-%s-%d' % (iid, len(self.lab.core.log.entries)))['result']

    def test_raman_peaks(self):
        calls, out = self.calls('run jasco_nrs5500 analyze peaks prominence=50/s')
        self.assertEqual([c['capability'] for c in calls], ['result.read', 'spectrum.peaks'])
        spec = self.result('jasco_nrs5500', 'result.read')['spectra'][-1]
        a = calls[1]['arguments']
        self.assertEqual(a['raman_shift_m_inv'], spec['raman_shift_m_inv'])
        self.assertEqual(a['count_rate_s_inv'], spec['count_rate_s_inv'])
        self.assertEqual(a['minimum_prominence_s_inv'], 50.0)
        self.assertIn(46400.0, out['result']['peak_shift_m_inv'])            # quartz 464 cm-1

    def test_stopped_flow_kinetics(self):
        calls, out = self.calls('run applied_sx20 analyze kinetics')
        pts = self.result('applied_sx20', 'result.read')['points']
        a = calls[1]['arguments']
        self.assertEqual(a['time_s'], [p['trigger_time_s'] for p in pts])
        self.assertEqual(a['signal'], [p['signal'] for p in pts])
        self.assertGreater(out['result']['k_s_inv'], 0)

    def test_transient_absorption_column(self):
        calls, out = self.calls('run ultrafast_helios analyze kinetics at=600nm tau=150ps')
        res = self.result('ultrafast_helios', 'result.read')
        i = min(range(len(res['wavelength_m'])), key=lambda k: abs(res['wavelength_m'][k] - 6e-07))
        a = calls[1]['arguments']
        self.assertEqual(a['delay_s'], [p['actual_delay_s'] for p in res['points']])
        self.assertEqual(a['delta_absorbance'], [p['delta_absorbance'][i] for p in res['points']])
        self.assertEqual((a['initial_lifetimes_s'], a['irf_fwhm_s'], a['time_zero_s']), ([1.5e-10], 1e-13, 0.0))
        self.assertNotIn('_at', a)


class Units(unittest.TestCase):
    """The class's own units (until the framework table has them): wavenumber, magnetic field, short times, GHz."""

    def test_local_kinds(self):
        from chemlab.compile.classes.spectroscopy import fmt_q, parse_q
        self.assertEqual(parse_q('1650cm-1', 'wavenumber'), 165000.0)
        self.assertEqual(parse_q('338.4mT', 'field'), 0.3384)
        self.assertEqual(parse_q('3384G', 'field'), 0.3384)
        self.assertEqual(parse_q('150ps', 'duration'), 1.5e-10)
        self.assertEqual(parse_q('9.5GHz', 'frequency'), 9.5e9)
        self.assertEqual(parse_q('-250Hz', 'offset'), -250.0)
        self.assertAlmostEqual(parse_q('50nm/min', 'scan_speed'), 50e-9 / 60, delta=1e-24)
        self.assertEqual(parse_q('0.2mM', 'concentration'), 0.2)
        self.assertEqual(parse_q('100nm', 'wavelength'), 1e-07)
        self.assertEqual(fmt_q(165000.0, 'wavenumber'), '1650 cm-1')
        self.assertEqual(fmt_q(1.5e-10, 'duration'), '150 ps')
        for bad, kind in (('5 kelvin', 'wavenumber'), ('-5cm-1', 'wavenumber'), ('3 furlong', 'field')):
            with self.assertRaises(Exception):
                parse_q(bad, kind)


if __name__ == '__main__':
    unittest.main()
