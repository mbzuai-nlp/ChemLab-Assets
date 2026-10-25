"""The C8-C20 n-alkane retention-index standard (Supelco 04070: about 40 mg/L of each n-alkane in hexane) on the GCs
(2026-10-23).

* spectral_library/gc_retention.json holds all 13 n-alkanes: I = 100 n (Kovats definition, every column class), ECN = n, and (D-2)
  the homologous-series fit of all NIST enthalpies of vaporization for every C8-C20 alkane (gc_model.alkane_delta_vap_series);
* substances.json carries the derived 7890B fingerprints (gc_7890b_fid) of the 13 alkanes and of the hexane solvent;
* 8890B template path, isothermal method (still valid): the neat alkane mix gives 13 peaks in carbon-number order at 190 C and (D-2:
  no C11/C12 merge any more) at 210 C;
* 8890B template path with the oven program (1.6.0, chromatography.set_oven_program; same program semantics as the touch terminal);
* 7890B and 8890B (oven program 40 C 1 min, 10 C/min to 300 C, 5 min): the standard in hexane gives the solvent peak and 13 alkane
  peaks in carbon-number order (7890B species from the substances.json fingerprints, as the Matterix bridge gives them; 8890B through
  the instrument API: chromatography.set_oven_program, then chromatography.measure and the run at Start).

    python -m unittest tests/test_gc_alkane_standard.py
"""
import dataclasses
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT)]
import gc_runtime as legacy  # noqa: E402
from asset_runtime import agilent_7890b_ssl_fid as rt7890  # noqa: E402
from scripts.basic40_templates import agilent_gc8890b as gc8890b  # noqa: E402
import basic40_runtime_analytical as rt8890  # noqa: E402

ALKANES = ["octane", "nonane", "decane", "undecane", "dodecane", "tridecane", "tetradecane", "pentadecane", "hexadecane", "heptadecane",
           "octadecane", "nonadecane", "icosane"]
LIB = json.loads((ROOT / "assets/reactions/spectral_library/gc_retention.json").read_text(encoding="utf-8"))
COMPOUNDS = json.loads((ROOT / "assets/reactions/spectral_library/compounds.json").read_text(encoding="utf-8"))["compounds"]
SUBS = json.loads((ROOT / "assets/reactions/substances.json").read_text(encoding="utf-8"))["substances"]


class Library(unittest.TestCase):
    def test_all_thirteen_alkanes_with_definition_indices_and_ecn(self):
        k_prev, dh_prev = 0.0, 0.0
        for n, sid in enumerate(ALKANES, start=8):
            with self.subTest(sid=sid):
                self.assertEqual(COMPOUNDS[sid]["smiles"], "C" * n)
                rec = LIB["compounds"][sid]
                for cls, ri in rec["retention_index"].items():
                    self.assertEqual((ri["median"], ri["evidence"]), (100.0 * n, "definition"), cls)
                self.assertEqual(rec["fid"]["ecn"]["ecn"], float(n))
                fp = rec["model_fingerprint"]["nonpolar"]
                self.assertGreater(fp["k_ref"], k_prev)
                self.assertLessEqual(fp["delta_h_J_mol"], dh_prev)             # |dvapH| grows along the series (no outlier)
                k_prev, dh_prev = fp["k_ref"], fp["delta_h_J_mol"]
                self.assertTrue(rec["delta_vap_H_source"].startswith("NIST WebBook"))
        series = LIB["gc_model"]["alkane_delta_vap_series"]
        self.assertEqual(series["range_n"], [8, 20])
        a, b = series["coefficients_kJ_mol"][:2]
        for n, sid in enumerate(ALKANES, start=8):                    # D-2: every C8-C20 alkane uses the series value at 373 K
            with self.subTest(sid=sid):
                rec = LIB["compounds"][sid]
                self.assertIn("homologous-series fit", rec["delta_vap_H_source"])
                self.assertIn("own NIST value", rec["delta_vap_H_source"])          # the compound's own value stays quoted
                self.assertAlmostEqual(rec["delta_vap_H_J_mol"], 1000 * (a + b * n), delta=50)
        self.assertTrue(any(x.startswith("2026-10-23 D-2") for x in series["changes"]))

    def test_7890b_fingerprints_in_substances(self):
        for sid in ALKANES + ["hexane"]:
            with self.subTest(sid=sid):
                fp, lib = SUBS[sid]["gc_7890b_fid"], LIB["compounds"][sid]["model_fingerprint"]["nonpolar"]
                self.assertEqual(fp["evidence"], "derived")
                self.assertEqual((fp["k_ref"], fp["delta_h_J_mol"], fp["response_C_kg"]), (lib["k_ref"], lib["delta_h_J_mol"], lib["response_C_kg"]))


class Injections(unittest.TestCase):
    def test_8890b_template_neat_alkanes_isothermal(self):
        template = gc8890b.validate_sample({sid: 4e-8 for sid in ALKANES})
        for oven_K in (463.15, 483.15):                               # 210 C: C11 and C12 merged with their own NIST dvapH (before D-2)
            with self.subTest(oven_K=oven_K):
                r = gc8890b.simulate(template, oven_K=oven_K, inlet_K=523.15, fid_K=573.15)
                apex = [r["species"][sid]["apex_time_s"] for sid in ALKANES]
                self.assertTrue(all(a is not None for a in apex))
                self.assertEqual(apex, sorted(apex))
                self.assertEqual(len(r["public"]["peaks"]), 13)
                self.assertIsNone(r["public"]["oven_program"])

    def test_8890b_template_isothermal_program_is_the_old_isothermal_run(self):
        template = gc8890b.validate_sample({"octane": 1e-7, "decane": 1e-7})
        old = gc8890b.simulate(template, oven_K=393.15, inlet_K=523.15, fid_K=573.15)
        flat = gc8890b.simulate(template, oven_K=0.0, inlet_K=523.15, fid_K=573.15,
                                oven_program={"initial_temperature_K": 393.15, "initial_hold_s": 300.0, "ramps": []})
        self.assertEqual(flat["public"]["retention_time_s"], old["public"]["retention_time_s"])
        self.assertEqual(flat["public"]["peak_area_C"], old["public"]["peak_area_C"])

    def test_8890b_program_semantics_are_the_terminal_ones(self):
        prog = {"initial_temperature_K": 313.15, "initial_hold_s": 60.0,
                "ramps": [{"rate_K_s": 10 / 60, "final_temperature_K": 573.15, "final_hold_s": 300.0},
                          {"rate_K_s": 0.5, "final_temperature_K": 473.15, "final_hold_s": 30.0}]}       # a cooling ramp too
        ramps = [(r["rate_K_s"], r["final_temperature_K"], r["final_hold_s"]) for r in prog["ramps"]]
        for t in (0.0, 30.0, 60.0, 61.0, 900.0, 1620.0, 1700.0, 2000.0, 2120.0, 2200.0, 5000.0):
            with self.subTest(t=t):
                self.assertAlmostEqual(gc8890b.program_setpoint_K(prog, t), rt8890._g8_program(313.15, 60.0, ramps, t)[0], places=9)
        self.assertAlmostEqual(gc8890b.program_length_s(prog), rt8890._g8_program_length(313.15, 60.0, ramps), places=9)
        for bad in ({**prog, "ramps": [{"rate_K_s": 2.5, "final_temperature_K": 573.15, "final_hold_s": 0.0}]},     # > 120 C/min (p.91)
                    {**prog, "ramps": prog["ramps"] * 17},                                                         # > 32 ramps (p.90)
                    {**prog, "initial_temperature_K": 800.0}):
            with self.assertRaises(ValueError):
                gc8890b.validate_oven_program(bad)

    def test_8890b_programmed_run_of_the_standard_in_hexane(self):
        g = rt8890.AgilentGC8890B("gc", scene_adapter=lambda i, u: None)
        g.register_template_sample("std", composition_kg=dict({sid: 4e-8 for sid in ALKANES}, hexane=6.55e-4))
        n = [0]

        def call(cap, expect="succeeded", **args):
            n[0] += 1
            r = g.invoke(capability_id=cap, arguments=args, request_id="r%d" % n[0])
            self.assertEqual(r["status"], expect, (cap, r.get("error")))
            return r["result"] if expect == "succeeded" else r["error"]
        call("power.set", enabled=True)
        g.host_step(200.0)
        self.assertEqual(call("chromatography.set_oven_program", expect="rejected", initial_temperature_K=313.15, initial_hold_s=60.0,
                              ramps=[{"rate_K_s": 3.0, "final_temperature_K": 573.15, "final_hold_s": 0.0}])["code"], "INVALID_ARGUMENT")
        out = call("chromatography.set_oven_program", initial_temperature_K=313.15, initial_hold_s=60.0,
                   ramps=[{"rate_K_s": 10 / 60, "final_temperature_K": 573.15, "final_hold_s": 300.0}])
        self.assertEqual(out, {"initial_temperature_K": 313.15, "ramps": 1, "run_length_s": 1920.0})
        oven = g._device["method"]["oven"]                                     # the Active Method that the touchscreen shows
        self.assertEqual((oven["initial_K"], oven["initial_time_s"], oven["ramp_1_final_K"], oven["ramp_2_rate_K_s"]), (313.15, 60.0, 573.15, 0.0))
        call("lid.set_angle", angle_rad=1.4)
        call("sample.load", sample_id="std")
        call("lid.set_angle", angle_rad=0.0)
        ch = call("chromatography.measure")["chromatogram"]
        self.assertEqual(ch["run_duration_s"], 1920.0)
        self.assertAlmostEqual(ch["oven_final_setpoint_K"], 573.15)
        self.assertEqual(len(ch["peaks"]), 14)                                  # the solvent + 13 alkanes
        rt_s = ch["retention_time_s"]
        self.assertEqual(rt_s, sorted(rt_s))
        template = g.debug_snapshot()["registered_samples"]["std"]["template"]
        r = gc8890b.simulate(template, oven_K=0.0, inlet_K=g.debug_snapshot()["state"]["thermal.inlet.setpoint_k"],
                             fid_K=g.debug_snapshot()["state"]["thermal.fid.setpoint_k"], oven_program=ch["oven_program"])
        apex = [r["species"][sid]["apex_time_s"] for sid in ALKANES]
        self.assertEqual(apex, sorted(apex))                                    # carbon-number order
        self.assertLess(r["species"]["hexane"]["apex_time_s"], apex[0])         # the solvent first
        for i, sid in enumerate(["hexane"] + ALKANES):                          # each peak is that compound's apex
            self.assertAlmostEqual(rt_s[i], r["species"][sid]["apex_time_s"], delta=2 * gc8890b.SAMPLE_INTERVAL_S)
        self.assertIsNone(rt8890._g8_active_oven_program({"method": {"oven": dict(oven, ramp_1_rate_K_s=0.0)}}))   # Rate 1 = 0: isothermal

    def test_7890b_programmed_run_of_the_standard_in_hexane(self):
        ids = ALKANES + ["hexane"]
        species = [legacy.Species(s, f["k_ref"], f["delta_h_J_mol"], f["response_C_kg"], f["reference_temperature_K"])
                   for s in ids for f in [SUBS[s]["gc_7890b_fid"]]]
        h = rt7890.GC7890BRuntime("gc7890b")
        g = rt7890.FirmwareGC.__new__(rt7890.FirmwareGC)                   # the process with the substances.json species (bridge-style)
        g.fw = h
        legacy.GC.__init__(g, species=species, vial_masses_kg=dict({s: 4e-8 for s in ALKANES}, hexane=6.55e-4))
        for name, z in list(g.zones.items()):
            g.zones[name] = rt7890.SwitchableZone(**dataclasses.asdict(z))
        h.gc = g
        h.register_principal("agent", "agent")
        a = h.session("agent")
        n = [0]

        def call(cap, **args):
            n[0] += 1
            r = a.invoke(instance_id="gc7890b", capability_id=cap, arguments=args, request_id="r%d" % n[0])
            self.assertEqual(r["status"], "succeeded", (cap, r["error"]))
        call("power.connect_mains", connected=True)
        for gas in ("carrier", "hydrogen", "air", "makeup"):
            call("gas.connect", gas=gas, connected=True)
        call("power.set", on=True)
        call("thermal.set_temperature", zone="oven", temperature_k=313.15)
        call("oven.set_initial_time", time_s=60.0)
        call("oven.set_ramp", index=1, rate_k_s=10 / 60.0, final_k=573.15, final_time_s=300.0)
        h.step(1800.0)
        call("detector.set_flame", on=True)
        h.step(600.0)
        call("run.prep")
        h.step(60.0)
        call("sample.aspirate", fraction=1e-3)
        call("syringe.insert")
        call("run.start")
        call("syringe.depress")
        call("syringe.retract")
        t, trace, prev, apex = [], [], {s: 0.0 for s in ids}, {s: (0.0, None) for s in ids}
        for _ in range(2100):
            h.step(1.0)
            t.append(h.gc.run_time_s)
            trace.append(h.gc.fid_current_A)
            mb = h.gc.mass_balance()
            for s in ids:
                inc = mb[s]["eluted_kg"] - prev[s]
                prev[s] = mb[s]["eluted_kg"]
                if inc > apex[s][0]:
                    apex[s] = (inc, t[-1])
        times = [apex[s][1] for s in ALKANES]
        self.assertTrue(all(x is not None for x in times))
        self.assertEqual(times, sorted(times))                               # carbon-number order
        self.assertLess(apex["hexane"][1], times[0])                         # the solvent first
        base = min(trace)
        smallest = min(trace[t.index(x)] for x in times) - base
        peaks = [i for i in range(1, len(trace) - 1) if trace[i] - base > 0.01 * smallest and trace[i] > trace[i - 1] and trace[i] >= trace[i + 1]]
        self.assertEqual(len(peaks), 14)                                     # solvent + 13 alkanes on the FID trace


if __name__ == "__main__":
    unittest.main()
