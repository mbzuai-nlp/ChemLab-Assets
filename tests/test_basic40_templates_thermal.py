"""Real-data templates for the basic40_batch02 thermal tier (agent key: thermal), 2026-10-14.

* Shared reaction layer (assets/reactions/reaction_templates.json + substances.json) for nabertherm_l9, carbolite_tf1,
  binder_vd56, parr_4591 and radleys_mya4: schema, mass/element balance, enthalpies re-derived from the recorded
  formation data, a pure-Python replay of Matterix ReactionKinetics, per-instrument coverage, contract registration.
* Multiwave 7101 digestion templates (scripts/basic40_templates/multiwave_7101.py hooked into
  basic40_runtime_thermal.Multiwave7101Session): template.read, digestion.load_template, digestion.evaluate.
* not_applicable registrations for julabo_corio_cd200f and miele_plw6111.
Run: python -m unittest tests/test_basic40_templates_thermal.py
"""
import copy
import hashlib
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "skills/chemistry-lab-assets/scripts"))
from asset_tool import lint_schema, validate_value  # noqa: E402
from basic40_runtime_thermal import (MULTIWAVE_7101_CAPACITY_J_K, MULTIWAVE_7101_TYPE, Multiwave7101Runtime,  # noqa: E402
                                     Parr4591Runtime, export_multiwave_7101_metadata)
from basic40_templates import multiwave_7101 as mw  # noqa: E402
from template_library import shared_ids_for  # noqa: E402

REACTIONS = ROOT / "assets/reactions/reaction_templates.json"
SUBSTANCES = ROOT / "assets/reactions/substances.json"
RX_DIR = ROOT / "outputs/basic40_batch02/t1_thermal/reaction_templates"
TIER = ROOT / "assets/basic40_batch02/t1_thermal"
REACTION_INSTRUMENTS = {"nabertherm_l9": "instrument.nabertherm_l9", "carbolite_tf1": "instrument.carbolite_tf1_12_60_300",
                        "binder_vd56": "instrument.binder_vd56", "parr_4591": "instrument.parr_4591", "radleys_mya4": "instrument.radleys_mya4"}
R = 8.314462618


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def replay(t, subs, temperature, seconds=120.0, dt=1.0 / 12.0, mass_kg=0.3, c_j_kg_k=792.0):
    """Port of matterix ReactionKinetics.step (levels 1, 2 and 3/order 1) for one template, as in its smoke test."""
    masses = {s: 0.05 * nu * subs[s]["molar_mass_kg_mol"] for s, nu in t["reactants"].items()}
    for s in t["products"]:
        masses.setdefault(s, 0.0)
    if t["conditions"].get("catalyst"):
        masses[t["conditions"]["catalyst"]] = 0.001
    if t["conditions"]["phase"] == "aqueous":
        masses.setdefault("water", 0.05)
    total0, temp, extent, start, worst = sum(masses.values()), temperature, 0.0, 0.0, 0.0
    lo, hi = t["conditions"]["temperature_K"]

    def limiting():
        return max(0.0, min(masses[s] / subs[s]["molar_mass_kg_mol"] / nu for s, nu in t["reactants"].items()))
    for _ in range(int(round(seconds / dt))):
        rate, avail = t["rate"], limiting()
        start = max(start, avail + extent)
        if rate.get("max_conversion") is not None:
            avail = min(avail, max(0.0, rate["max_conversion"] * start - extent))
        if rate["level"] == 1:
            xi = start / rate["duration_s"] * dt
        elif rate["level"] == 2:
            k = rate["q10"] ** ((temp - rate["reference_temperature_K"]) / 10.0) / rate["time_constant_s"]
            xi = limiting() * (1.0 - math.exp(-k * dt))
        else:
            assert rate.get("order", 1) == 1
            xi = limiting() * (1.0 - math.exp(-rate["A"] * math.exp(-rate["Ea_J_mol"] / (R * temp)) * dt))
        xi = min(max(xi, 0.0), avail) if lo <= temp <= hi else 0.0
        for s, nu in t["reactants"].items():
            masses[s] -= nu * subs[s]["molar_mass_kg_mol"] * xi
        for s, nu in t["products"].items():
            masses[s] += nu * subs[s]["molar_mass_kg_mol"] * xi
        extent += xi
        if t.get("enthalpy_J_per_mol") is not None:
            temp -= t["enthalpy_J_per_mol"] * xi / (mass_kg * c_j_kg_k)
        worst = max(worst, abs(sum(masses.values()) - total0))
    return {"progress": extent / start if start else 0.0, "temperature": temp, "worst": worst, "min": min(masses.values())}


class ReactionTemplateLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load(REACTIONS)
        cls.rx = cls.data["reactions"]
        cls.subs = load(SUBSTANCES)["substances"]
        cls.original = load(RX_DIR / "original_reaction_templates_2026-10-14.json")["reactions"]
        cls.log = load(RX_DIR / "thermo_data_used.json")
        cls.new_ids = cls.log["new_reaction_ids"]

    def test_schema_fields_and_source_tags(self):
        self.assertEqual(len(self.new_ids), 30)
        for rid, t in self.rx.items():
            for key in ("name_zh", "equation", "reactants", "products", "conditions", "rate", "enthalpy_J_per_mol", "observables",
                        "typical_instrument_asset_type_ids", "sources"):
                self.assertIn(key, t, rid)
            # levels 4 (photochemical) and 5 (electrochemical, Faraday) were added in phase 2 (2026-10-16, reactions_r3.py)
            need = {1: ["duration_s"], 2: ["time_constant_s", "reference_temperature_K", "q10"], 3: ["A", "Ea_J_mol", "order"],
                    4: ["quantum_yield", "quantum_yield_species", "absorber", "molar_absorptivity_m2_mol", "wavelength_band_m"],
                    5: ["n_electrons", "faradaic_efficiency", "electrode", "half_reactions"]}[t["rate"]["level"]]
            self.assertTrue(all(k in t["rate"] for k in need), rid)
            lo, hi = t["conditions"]["temperature_K"]
            self.assertLess(0, lo, rid)
            self.assertLess(lo, hi, rid)
            self.assertTrue(all(s["evidence"] in ("documented", "assumption") for s in t["sources"]), rid)
            gates = [t["conditions"].get("catalyst"), t["conditions"].get("electrolyte"), t["rate"].get("absorber")]
            species = list(t["reactants"]) + list(t["products"]) + [g for g in gates if g]
            for s in species:
                self.assertIn(s, self.subs, f"{rid}: {s}")
                self.assertIn(self.subs[s]["phase"], ("solid", "liquid", "gas", "aqueous"))
        for rid in self.new_ids:
            items = [s["item"] for s in self.rx[rid]["sources"]]
            self.assertTrue(any(i.startswith("enthalpy") for i in items), rid)

    def test_mass_and_element_balance(self):
        sys.path.insert(0, str(RX_DIR))
        from species_catalog import SPECIES
        for rid, t in self.rx.items():
            lhs = sum(nu * self.subs[s]["molar_mass_kg_mol"] for s, nu in t["reactants"].items())
            rhs = sum(nu * self.subs[s]["molar_mass_kg_mol"] for s, nu in t["products"].items())
            self.assertLess(abs(lhs - rhs) / lhs, 1e-4, rid)
        for rid in self.new_ids:
            t = self.rx[rid]
            left, right = {}, {}
            for side, acc in ((t["reactants"], left), (t["products"], right)):
                for s, nu in side.items():
                    for el, n in SPECIES[s]["elements"].items():
                        acc[el] = acc.get(el, 0) + nu * n / SPECIES[s].get("formula_units", 1)
            self.assertEqual(set(left), set(right), rid)
            for el in left:
                self.assertAlmostEqual(left[el], right[el], places=9, msg=f"{rid} {el}")

    def test_original_templates_unchanged_except_mya4_listing(self):
        # round 2 (2026-10-16) may only append the instrument ids listed in the build log (typical_id_additions)
        r2_added = self.log["round2"]["typical_id_additions"]
        for rid, old in self.original.items():
            new = copy.deepcopy(self.rx[rid])
            prefix = old["typical_instrument_asset_type_ids"] + (["instrument.radleys_mya4"] if rid == "saponification_ethyl_acetate_naoh" else [])
            ids = new["typical_instrument_asset_type_ids"]
            self.assertEqual(ids[:len(prefix)], prefix, rid)
            self.assertLessEqual(set(ids[len(prefix):]), set(r2_added.get(rid, [])), rid)
            new["typical_instrument_asset_type_ids"] = old["typical_instrument_asset_type_ids"]
            self.assertEqual(new, old, rid)
        self.assertEqual(list(self.rx)[:8], list(self.original))

    def test_enthalpies_rederived_from_recorded_formation_data(self):
        dfh = {k: v["value"] for k, v in self.log["dfh_kJ_mol"].items()}
        vap = {k: v["value"] for k, v in self.log["vaporization_kJ_mol"].items()}
        derived = 0
        for rid in self.new_ids:
            t, dh = self.rx[rid], self.rx[rid]["enthalpy_J_per_mol"]
            if dh is None:
                self.assertTrue(any(s["item"] == "enthalpy null" for s in t["sources"]), rid)
                continue
            if rid.startswith("vacuum_drying_"):
                self.assertAlmostEqual(dh / 1000.0, vap[list(t["products"])[0]], places=6)
                self.assertAlmostEqual(t["rate"]["Ea_J_mol"], dh, places=1)  # Clausius-Clapeyron: Ea = ΔvapH
                continue
            calc = sum(nu * dfh[s] for s, nu in t["products"].items()) - sum(nu * dfh[s] for s, nu in t["reactants"].items())
            self.assertAlmostEqual(calc * 1000.0, dh, delta=1.0, msg=rid)
            derived += 1
        self.assertGreaterEqual(derived, 20)
        # physical signs: dehydration/calcination endothermic; hydrogenation exothermic; Hess checks against NBS rows
        self.assertGreater(self.rx["copper_sulfate_pentahydrate_dehydration"]["enthalpy_J_per_mol"], 0)
        self.assertGreater(self.rx["magnetite_hydrogen_reduction_to_iron"]["enthalpy_J_per_mol"], 0)
        self.assertLess(self.rx["copper_oxide_hydrogen_reduction"]["enthalpy_J_per_mol"], 0)
        self.assertAlmostEqual(self.rx["copper_sulfate_pentahydrate_dehydration"]["enthalpy_J_per_mol"]
                               + self.rx["copper_sulfate_trihydrate_dehydration"]["enthalpy_J_per_mol"]
                               + self.rx["copper_sulfate_monohydrate_dehydration"]["enthalpy_J_per_mol"],
                               (-771.36 - 5 * 241.826 + 2279.65) * 1000.0, delta=1.0)
        for rid in ("nitrobenzene_hydrogenation_aniline", "styrene_hydrogenation_ethylbenzene", "cyclohexene_hydrogenation_cyclohexane"):
            self.assertLess(self.rx[rid]["enthalpy_J_per_mol"], -1e5)

    def test_documented_kinetics_numbers(self):
        cuo = self.rx["copper_oxide_hydrogen_reduction"]["rate"]
        self.assertEqual(cuo["level"], 3)
        self.assertAlmostEqual(cuo["Ea_J_mol"], 14.5 * 4184.0, places=1)
        self.assertAlmostEqual(cuo["A"] * math.exp(-cuo["Ea_J_mol"] / (R * 523.15)), 1 / 1200.0, delta=1e-9)
        hyd = self.rx["acetic_anhydride_hydrolysis"]["rate"]
        k298 = hyd["A"] * math.exp(-hyd["Ea_J_mol"] / (R * 298.15))
        self.assertAlmostEqual(hyd["A"], math.exp(12.74), delta=0.1)
        self.assertTrue(1e-3 < k298 < 4e-3, k298)  # pseudo-first-order hydrolysis at 25 °C, minutes time scale
        self.assertEqual(self.rx["hematite_hydrogen_reduction_to_magnetite"]["rate"]["Ea_J_mol"], 76000.0)
        self.assertEqual(self.rx["magnetite_hydrogen_reduction_to_iron"]["rate"]["Ea_J_mol"], 39000.0)
        self.assertEqual(self.rx["potassium_chlorate_decomposition_mno2"]["conditions"]["catalyst"], "manganese_dioxide")
        for rid in ("nitrobenzene_hydrogenation_aniline", "styrene_hydrogenation_ethylbenzene", "maleic_acid_hydrogenation_succinic_acid",
                    "cyclohexene_hydrogenation_cyclohexane", "benzaldehyde_hydrogenation_benzyl_alcohol"):
            self.assertEqual(self.rx[rid]["conditions"]["catalyst"], "palladium")
            self.assertIn("hydrogen", self.rx[rid]["reactants"])
            self.assertTrue(self.rx[rid]["conditions"]["requires_stirring"])

    def test_replay_matterix_smoke_semantics(self):
        for rid, t in self.rx.items():
            if t["rate"]["level"] == 3 and t["rate"].get("order", 1) == 2:
                continue  # second-order saponification is covered by the Matterix test itself
            if t["rate"]["level"] in (4, 5):
                continue  # light- / current-driven (phase 2): replayed by validate_r3.py and Matterix test_chemsim_reactions_drives.py
            lo, hi = t["conditions"]["temperature_K"]
            mid, cold = replay(t, self.subs, (lo + hi) / 2), replay(t, self.subs, lo - 50.0)
            self.assertGreater(mid["progress"], 0.0, rid)
            self.assertEqual(cold["progress"], 0.0, rid)
            self.assertLess(mid["worst"], 1e-6, rid)
            self.assertGreaterEqual(mid["min"], -1e-15, rid)
        # catalyst gating: KClO3 without MnO2 stays inert in the Matterix semantics (catalyst mass must be > 0)
        self.assertIsNotNone(self.rx["potassium_chlorate_decomposition_mno2"]["conditions"]["catalyst"])
        # equilibrium cap honoured by the new esterifications
        t = self.rx["fischer_esterification_isoamyl_acetate"]
        self.assertAlmostEqual(replay(t, self.subs, 393.15, seconds=12 * 3600, dt=5.0)["progress"], 0.667, places=3)

    def test_new_substances_format_and_links(self):
        new = self.log["new_substance_ids"]
        self.assertEqual(len(new), 52)
        for sid in new:
            e = self.subs[sid]
            self.assertEqual(list(e)[:6], ["name_zh", "formula", "molar_mass_kg_mol", "phase", "density_kg_m3", "fid_visible"])
            self.assertIn(e["evidence"], ("documented",))
            self.assertTrue(e["source"])
            self.assertIn("cas", e)
            if e["phase"] == "gas":
                self.assertIsNone(e["density_kg_m3"])
            self.assertEqual(shared_ids_for(e["cas"], sid).get("substances"), sid) if e["cas"] else None
        for sid in ("aniline", "ethylbenzene", "cyclohexane", "isoamyl_acetate", "methyl_salicylate", "aspirin", "methyl_benzoate"):
            self.assertEqual(self.subs[sid]["gc_7890b_fid"]["evidence"], "derived", sid)
            self.assertTrue(shared_ids_for(self.subs[sid]["cas"], sid).get("spectral_library"), sid)

    def test_validation_evidence_matches_current_files(self):
        v = load(RX_DIR / "output_template_validation.json")
        self.assertEqual(v["summary"]["status"], "passed")
        self.assertEqual(v["library_sha256"], hashlib.sha256(REACTIONS.read_bytes()).hexdigest())
        self.assertEqual(v["substances_sha256"], hashlib.sha256(SUBSTANCES.read_bytes()).hexdigest())
        self.assertTrue(all(r["status"] == "passed" for r in v["species"].values()))
        self.assertTrue(all(r["status"] == "passed" for r in v["reactions"].values()))

    def test_each_reaction_instrument_has_five_templates_and_contract_lists_them(self):
        for slug, type_id in REACTION_INSTRUMENTS.items():
            ids = sorted(rid for rid, t in self.rx.items() if type_id in t["typical_instrument_asset_type_ids"])
            self.assertGreaterEqual(len(ids), 5, slug)
            c = load(TIER / slug / "asset.contract.json")
            self.assertEqual(c["asset_type_id"], type_id)
            ext = c["extensions"]["chemistry_lab.reaction_templates"]
            self.assertEqual(ext["library"], "assets/reactions/reaction_templates.json")
            self.assertEqual(sorted(ext["reaction_ids"]), ids)
            self.assertEqual(ext["executed_by"], "Matterix ReactionKinetics (container ledger)")
            out = c["extensions"]["chemistry_lab.output_templates"]
            self.assertEqual(out["status"], "not_applicable")
            self.assertIn("chemistry_lab.reaction_templates", out["reason_zh"])
            self.assertEqual(c["asset_version"], "1.1.0")


class Multiwave7101TemplateTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.host = Multiwave7101Runtime(scene_adapter=lambda sid, updates: self.calls.append((sid, copy.deepcopy(updates))))
        self.host.create_instance("mw_a")
        self.host.create_instance("mw_b")
        self.host.register_principal("learner", visibility="agent", instance_ids=["mw_a"])
        self.host.register_principal("judge", visibility="evaluator")
        self.agent, self.judge = self.host.session("learner"), self.host.session("judge")
        self.spec = export_multiwave_7101_metadata()["asset_types"][MULTIWAVE_7101_TYPE]
        self.n = 0

    def invoke(self, cap, **args):
        self.n += 1
        return self.agent.invoke(instance_id="mw_a", capability_id=cap, arguments=args, request_id=f"tmpl_{self.n}")

    def ok(self, cap, **args):
        r = self.invoke(cap, **args)
        self.assertEqual(r["status"], "succeeded", r)
        self.assertEqual(validate_value(r["result"], self.spec["capabilities"][cap]["output_schema"]), [])
        return r["result"]

    def load_slot(self, slot=0, material="nist_srm_2711a", method="epa_3051a_hno3", mass=2e-4, volume=4e-6):
        self.ok("door.open")
        self.ok("rack.set_slot", slot_index=slot, occupied=True)
        return self.ok("digestion.load_template", slot_index=slot, material_id=material, method_id=method, sample_mass_kg=mass, acid_volume_m3=volume)

    def run_program(self, target=448.15, power=1000.0, steps=(100.0,) * 6):
        self.ok("door.close")
        self.ok("power.set", enabled=True)
        self.ok("thermal.set_temperature", temperature_K=target)
        self.ok("microwave.set_power", power_W=power)
        self.ok("microwave.set_enabled", enabled=True)
        for dt in steps:
            self.host.step(dt)

    def test_schemas_lint_and_capabilities_are_additive(self):
        for cap in mw.CAPABILITIES:
            info = self.spec["capabilities"][cap]
            self.assertEqual(lint_schema(info["input_schema"]), [])
            self.assertEqual(lint_schema(info["output_schema"]), [])
            self.assertEqual(validate_value(info["example_arguments"][0], info["input_schema"]), [])
        self.assertEqual(self.spec["declared_capabilities"]["chemistry.digest"]["availability"], "unsupported")
        for cap in ("power.set", "thermal.set_temperature", "microwave.set_enabled", "microwave.set_power", "door.open", "door.close", "rack.set_slot"):
            self.assertIn(cap, self.spec["capabilities"])
        self.assertEqual(self.spec["asset_version"], "1.1.0")

    def test_template_read_is_public_and_side_effect_free(self):
        before = self.host.debug_snapshot()
        for tid in ("epa_3051a_hno3", "epa_3052_hno3_hf", "nist_srm_2709a__pb", "nitric_acid", "water_saturation_pressure_373_573K"):
            res = self.ok("template.read", template_id=tid)
            rec = json.loads(res["record_json"])
            self.assertEqual(rec["substance_id"], tid)
            self.assertEqual(res["template_source"], mw.TEMPLATE_REL)
        self.assertEqual(json.loads(self.ok("template.read", template_id="nist_srm_2711a__pb")["record_json"])["value"], 0.0014)
        self.assertEqual(self.invoke("template.read", template_id="no_such_record")["error"]["code"], "UNKNOWN_REFERENCE")
        self.assertEqual(before, self.host.debug_snapshot())

    def test_load_preconditions_and_rejections_leave_state_unchanged(self):
        args = dict(slot_index=0, material_id="nist_srm_2711a", method_id="epa_3051a_hno3", sample_mass_kg=2e-4, acid_volume_m3=4e-6)
        self.assertEqual(self.invoke("digestion.load_template", **args)["error"]["code"], "PRECONDITION_FAILED")  # door closed
        self.ok("door.open")
        self.assertEqual(self.invoke("digestion.load_template", **args)["error"]["code"], "PRECONDITION_FAILED")  # empty slot
        self.ok("rack.set_slot", slot_index=0, occupied=True)
        before = self.host.debug_snapshot()
        cases = [({**args, "material_id": "nist_srm_9999"}, "UNKNOWN_REFERENCE"), ({**args, "method_id": "epa_3050b"}, "UNKNOWN_REFERENCE"),
                 ({**args, "method_id": "epa_3015a_hno3"}, "METHOD_MATRIX_MISMATCH"), ({**args, "sample_mass_kg": 2.1e-4}, "INVALID_ARGUMENT"),
                 ({**args, "acid_volume_m3": 5.1e-6}, "INVALID_ARGUMENT"), ({**args, "slot_index": True}, "INVALID_ARGUMENT"),
                 ({**args, "sample_mass_kg": float("nan")}, "INVALID_ARGUMENT")]
        for bad, code in cases:
            r = self.invoke("digestion.load_template", **bad)
            self.assertEqual((r["status"], r["error"]["code"], r["error"]["side_effects"]), ("rejected", code, "none"))
            self.assertEqual(before, self.host.debug_snapshot())
        self.assertEqual(self.invoke("digestion.evaluate", slot_index=0)["error"]["code"], "TEMPLATE_SAMPLE_REQUIRED")
        res = self.ok("digestion.load_template", **args)
        self.assertIn("vessel 8 mL below method minimum internal volume 45 mL (Rack 24 scale-down)", res["method_deviations"])

    def test_3051a_program_at_reference_power_completes_with_documented_ramp(self):
        self.load_slot()
        self.run_program()
        res = self.ok("digestion.evaluate", slot_index=0)
        p = res["program"]
        c, ua = MULTIWAVE_7101_CAPACITY_J_K, 1.0
        t_lo = -c / ua * math.log1p(-ua * (443.10 - 298.15) / 1000.0)  # indicated 443.15 K reached at true T >= 443.10 K
        self.assertAlmostEqual(p["entered_window_s"], t_lo, delta=0.3)
        self.assertTrue(p["ramp_within_spec"])  # EPA 3051A: 5.5 +/- 0.25 min
        self.assertAlmostEqual(p["hold_in_window_s"], 600.0 - p["entered_window_s"], delta=1e-6)
        self.assertTrue(p["program_complete"])
        self.assertFalse(p["exceeded_window"])
        self.assertTrue(p["synthetic"])
        self.assertAlmostEqual(p["water_saturation_pressure_at_target_Pa"], 892602.0, delta=2.0e3)  # IAPWS-95 at 175 °C
        self.assertIn("nitric_acid (393.65 K)", p["reagents_boiling_below_target"])
        self.assertEqual(res["status"], "complete")
        self.assertFalse(res["synthetic"])
        self.assertFalse(res["method_conformant"])  # Rack 24 vessel below EPA minimum volume: reported, not hidden
        pb = next(a for a in res["expected_analytes"] if a["analyte"] == "Pb")
        self.assertEqual(pb["certified_mass_fraction"], 0.0014)
        self.assertEqual(pb["expected_mass_fraction"], 0.0013)  # CLP leach median proxy (certificate Appendix A)
        self.assertAlmostEqual(pb["expected_mass_kg"], 0.0013 * 2e-4, places=15)
        self.assertAlmostEqual(pb["expected_concentration_kg_m3"], 0.0013 * 2e-4 / 4e-6, places=12)
        self.assertTrue(pb["basis"].startswith("leachable proxy"))
        undocumented = [a for a in res["expected_analytes"] if a["basis"] == "not_documented"]
        self.assertTrue(all(a["expected_mass_kg"] is None for a in undocumented))

    def test_cool_down_after_complete_hold_is_not_an_interruption(self):
        self.load_slot()
        self.run_program()
        self.ok("microwave.set_enabled", enabled=False)
        self.host.step(1800.0)
        res = self.ok("digestion.evaluate", slot_index=0)
        self.assertTrue(res["program"]["program_complete"])
        self.assertFalse(res["program"]["left_window_after_entry"])
        self.assertLess(self.ok("digestion.evaluate", slot_index=0)["program"]["max_indicated_temperature_K"], 453.15)
        self.assertNotIn("temperature left the window after first entry (hold interrupted)", res["method_deviations"])
        # interrupting before the hold is complete is flagged
        self.load_slot(slot=2)
        self.run_program(steps=(200.0, 200.0))
        self.ok("door.open")
        self.host.step(600.0)
        res = self.ok("digestion.evaluate", slot_index=2)
        self.assertTrue(res["program"]["left_window_after_entry"])
        self.assertFalse(res["program"]["program_complete"])

    def test_epa_3051a_measured_recovery_used_for_table_materials(self):
        self.load_slot(material="nist_srm_2704", method="epa_3051a_hno3")
        self.load_slot(slot=1, material="nist_srm_2704", method="epa_3051a_hno3_hcl")
        self.run_program()
        a = {x["analyte"]: x for x in self.ok("digestion.evaluate", slot_index=0)["expected_analytes"]}
        b = {x["analyte"]: x for x in self.ok("digestion.evaluate", slot_index=1)["expected_analytes"]}
        self.assertEqual(a["Cd"]["expected_mass_fraction"], 3.4e-06)
        self.assertEqual(b["Cd"]["expected_mass_fraction"], 3.62e-06)
        self.assertEqual(a["Cd"]["certified_mass_fraction"], 3.45e-06)
        self.assertTrue(a["Cd"]["basis"].startswith("EPA 3051A measured mean"))

    def test_3052_total_digestion_needs_its_longer_hold(self):
        self.load_slot(material="nist_srm_1515", method="epa_3052_hno3_hf", volume=4.8e-6)
        self.run_program(target=453.15)
        res = self.ok("digestion.evaluate", slot_index=0)
        self.assertFalse(res["program"]["program_complete"])  # 570 s hold not yet reached after 600 s
        self.assertTrue(all(a["expected_mass_kg"] is None for a in res["expected_analytes"]))
        self.host.step(400.0)
        res = self.ok("digestion.evaluate", slot_index=0)
        self.assertTrue(res["program"]["program_complete"])
        self.assertTrue(res["matrix_in_method_scope"])
        zn = next(a for a in res["expected_analytes"] if a["analyte"] == "Zn")
        self.assertEqual(zn["expected_mass_fraction"], zn["certified_mass_fraction"])
        self.assertTrue(zn["basis"].startswith("certified_total"))

    def test_fast_ramp_and_overshoot_are_flagged(self):
        self.load_slot()
        self.run_program(target=573.15, power=1500.0, steps=(60.0,) * 10)
        res = self.ok("digestion.evaluate", slot_index=0)
        p = res["program"]
        self.assertFalse(p["setpoint_in_window"])
        self.assertTrue(p["exceeded_window"])
        self.assertTrue(p["left_window_after_entry"])
        self.assertFalse(p["ramp_within_spec"])
        self.assertLess(p["hold_in_window_s"], 20.0)  # passes 443-453 K in a few seconds at 1500 W
        self.assertFalse(p["program_complete"])
        self.assertIn("indicated temperature exceeded the method window", res["method_deviations"])

    def test_time_in_window_independent_of_host_step_size(self):
        self.load_slot()
        self.run_program(steps=(700.0,))
        coarse = self.ok("digestion.evaluate", slot_index=0)["program"]
        other = Multiwave7101Runtime(scene_adapter=lambda *_: None)
        other.create_instance("x")
        rid = iter(range(100))
        inv = lambda cap, **a: other.invoke(instance_id="x", capability_id=cap, arguments=a, request_id=f"o{next(rid)}")
        inv("door.open"); inv("rack.set_slot", slot_index=0, occupied=True)
        inv("digestion.load_template", slot_index=0, material_id="nist_srm_2711a", method_id="epa_3051a_hno3", sample_mass_kg=2e-4, acid_volume_m3=4e-6)
        for cap, a in (("door.close", {}), ("power.set", {"enabled": True}), ("thermal.set_temperature", {"temperature_K": 448.15}),
                       ("microwave.set_power", {"power_W": 1000.0}), ("microwave.set_enabled", {"enabled": True})):
            inv(cap, **a)
        for _ in range(70):
            other.step(10.0)
        fine = inv("digestion.evaluate", slot_index=0)["result"]["program"]
        self.assertAlmostEqual(coarse["entered_window_s"], fine["entered_window_s"], delta=1e-3)
        self.assertAlmostEqual(coarse["hold_in_window_s"], fine["hold_in_window_s"], delta=1e-3)

    def test_thermal_model_and_other_instances_unchanged_by_template_contents(self):
        plain = Multiwave7101Runtime(scene_adapter=lambda *_: None)
        plain.create_instance("p")
        for cap, a in (("power.set", {"enabled": True}), ("thermal.set_temperature", {"temperature_K": 448.15}),
                       ("microwave.set_power", {"power_W": 1000.0}), ("microwave.set_enabled", {"enabled": True})):
            plain.invoke(instance_id="p", capability_id=cap, arguments=a, request_id=cap)
        self.load_slot()
        self.run_program(steps=(37.0, 250.0, 313.0))
        for _ in range(3):
            pass
        plain.step(37.0); plain.step(250.0); plain.step(313.0)
        a = self.host.debug_snapshot()["instances"]["mw_a"]["state"]
        b = plain.debug_snapshot()["instances"]["p"]["state"]
        for sid in ("thermal.temperature_k", "thermal.absorbed_energy_j", "thermal.environment_energy_j", "thermal.stored_energy_change_j"):
            self.assertEqual(a[sid], b[sid], sid)
        self.assertNotIn("template_digestion", self.host.debug_snapshot()["instances"]["mw_b"])
        self.assertEqual(self.judge.read(instance_id="mw_b", state_ids=["thermal.temperature_k"])["observations"]["thermal.temperature_k"]["value"], 298.15)

    def test_agent_payloads_hide_process_truth_and_replay_is_idempotent(self):
        self.load_slot()
        self.run_program(steps=(120.0,))
        payload = json.dumps([self.agent.describe(instance_id="mw_a"), self.ok("digestion.evaluate", slot_index=0), self.agent.logs()])
        for hidden in ("thermal.temperature_k", "thermal.absorbed_energy_j", "thermal.environment_energy_j", "thermal.stored_energy_change_j"):
            self.assertNotIn(hidden, payload)
        args = dict(instance_id="mw_a", capability_id="digestion.evaluate", arguments={"slot_index": 0}, request_id="same")
        first = self.agent.invoke(**args)
        self.host.step(50.0)
        self.assertEqual(self.agent.invoke(**args), first)
        self.assertEqual(self.agent.invoke(**{**args, "arguments": {"slot_index": 1}})["error"]["code"], "REQUEST_CONFLICT")

    def test_parr_runtime_sharing_the_host_class_is_unaffected(self):
        parr = Parr4591Runtime(scene_adapter=lambda *_: None)
        parr.create_instance("parr")
        self.assertNotIn("digestion.load_template", parr.describe(instance_id="parr")["capabilities"])
        parr.step(10.0)
        self.assertNotIn("template_digestion", parr.debug_snapshot()["instances"]["parr"])

    def test_contract_registration(self):
        c = load(TIER / "multiwave_7101/asset.contract.json")
        self.assertEqual(c["asset_version"], "1.1.0")
        ext = c["extensions"]["chemistry_lab.output_templates"]
        self.assertEqual(ext["status"], "implemented")
        self.assertEqual(ext["library"], mw.TEMPLATE_REL)
        self.assertGreaterEqual(ext["runtime_usable"], 50)
        for cap in mw.CAPABILITIES:
            self.assertEqual(c["capabilities"][cap]["availability"], "implemented")
            self.assertEqual(c["capabilities"][cap]["implementation"]["process"], "verified")
        art = c["artifacts"]["digestion_template_library"]
        self.assertEqual(art["sha256"], hashlib.sha256((ROOT / art["path"]).read_bytes()).hexdigest())
        v = load(ROOT / ext["validation"])
        self.assertEqual(v["summary"]["failed"], 0)


class NotApplicableRegistrationTests(unittest.TestCase):
    def test_circulator_and_washer_are_not_applicable(self):
        for slug in ("julabo_corio_cd200f", "miele_plw6111"):
            c = load(TIER / slug / "asset.contract.json")
            ext = c["extensions"]["chemistry_lab.output_templates"]
            self.assertEqual(set(ext), {"status", "reason_zh"})
            self.assertEqual(ext["status"], "not_applicable")
            self.assertTrue(ext["reason_zh"])
            self.assertEqual(c["asset_version"], "1.0.1")


if __name__ == "__main__":
    unittest.main()
