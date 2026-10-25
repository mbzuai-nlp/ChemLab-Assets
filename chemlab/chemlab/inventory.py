"""The lab's physical inventory: one record per container (bottle, vial, box, plate, specimen holder, gas cylinder, ...) at a named
storage place and position, with its contents (substance id, amount, concentration or purity, lot / certificate) and a storage
class.

The default lab's inventory is configs/inventory_default.json, written by chemlab/make_config.py (build()) from
  * STOCK below: what a well-run lab keeps on its shelves and in its cabinets at the start of a working day - common solvents
    and reagents, check standards and certified reference materials with their certificate values, consumables, gas cylinders
    (with the instruments they feed);
  * the default lab's set-up (chemlab/default_world.py + default_world_overrides.py): the reference samples, consumables and
    liquids the lab has already put into the instruments - listed apart, in "instrument_contents" (the instruments' own state,
    composition defined by each runtime; Matterix does not spawn them).
Content rules (check()): every substance id is in substances.json, every amount is physical (AMOUNT_UNITS), a solution lists its
solvent and each solute with its mass and concentration; packs use count / per_unit or units (see CONVENTIONS).
Matterix can spawn the same containers at the same places from the same file.  Chemlab reads it (lab config "inventory": the
file name, or the records inline) and shows it to the agent (`chemlab list --inventory [ID]`, `chemlab describe ID`).  Moving a
container between storage and an instrument is a host or robot action: the agent cannot teleport containers.

Substance ids are Chemistry-Sim's shared vocabulary: assets/reactions/substances.json, the spectral compound table
(assets/reactions/spectral_library/compounds.json) and their crosswalk (assets/reactions/shared_id_crosswalk.json).  Ids used here
that are in none of them are listed in NEW_SUBSTANCES (and in the file's "new_substances") for the materials owner to add; this
module never edits the shared tables.  Container type ids follow the labware catalog (assets/labware/catalog.json); until it
exists the ids here are provisional (PROVISIONAL_TYPES, also written to the file).

Every stock record has a "source": a manual page (file and page number, no copied text) or a typical value with its reason.
"""
import json
from functools import lru_cache
from pathlib import Path

from .hosts import CHEMSIM_ROOT

STORAGE_CLASSES = ('flammable', 'acid', 'base', 'oxidizer', 'toxic', 'cold', 'compressed_gas', 'general')

# Storage places of the default lab: named places with numbered positions (shelf-row, rack slot ...).  Sizes are typical lab
# furniture (a 1.2 m four-shelf rack, a 90 cm safety cabinet, a 600 L lab fridge, a 6-cylinder wall rack).
PLACES = [
    {'id': 'bench_shelf', 'access': 'top_down', 'type': 'stepped two-tier shelf on the robot bench (T1 front/low, T2 rear/high), all positions within the '
                                  'fixed arm\'s reach: the containers the robot handles (common solvents, pH buffers, the smoke '
                                  'workflows\' standards)', 'storage_class': 'general',
     'positions': ['T%d-%02d' % (t, p) for t in (1, 2) for p in range(1, 6)]},
    {'id': 'reagent_shelf_a', 'type': 'open room reagent rack, 4 shelves x 8 positions (out of the fixed arm\'s reach)',
     'storage_class': 'general',
     'positions': ['S%d-%02d' % (s, p) for s in range(1, 5) for p in range(1, 9)]},
    {'id': 'standards_cabinet', 'type': 'closed cabinet for check standards and certified reference materials, 3 shelves x 8',
     'storage_class': 'general', 'positions': ['S%d-%02d' % (s, p) for s in range(1, 4) for p in range(1, 9)]},
    {'id': 'flammables_cabinet', 'type': '90 cm safety cabinet for flammable liquids (EN 14470-1 type 90), 3 shelves x 6',
     'storage_class': 'flammable', 'positions': ['S%d-%02d' % (s, p) for s in range(1, 4) for p in range(1, 7)]},
    {'id': 'acids_cabinet', 'type': 'ventilated corrosives cabinet, acid compartment, 2 shelves x 6', 'storage_class': 'acid',
     'positions': ['A:S%d-%02d' % (s, p) for s in range(1, 3) for p in range(1, 7)]},
    {'id': 'bases_cabinet', 'type': 'ventilated corrosives cabinet, base compartment, 2 shelves x 6', 'storage_class': 'base',
     'positions': ['B:S%d-%02d' % (s, p) for s in range(1, 3) for p in range(1, 7)]},
    {'id': 'toxics_cabinet', 'type': 'ventilated, lockable cabinet for toxic substances, 2 shelves x 6', 'storage_class': 'toxic',
     'positions': ['S%d-%02d' % (s, p) for s in range(1, 3) for p in range(1, 7)]},
    {'id': 'oxidizers_cabinet', 'type': 'cabinet for oxidizers, 1 shelf x 6', 'storage_class': 'oxidizer',
     'positions': ['S1-%02d' % p for p in range(1, 7)]},
    {'id': 'fridge_4c', 'type': 'lab refrigerator 2-8 °C, 4 shelves x 8', 'storage_class': 'cold',
     'positions': ['S%d-%02d' % (s, p) for s in range(1, 5) for p in range(1, 9)]},
    {'id': 'freezer_m20c', 'type': 'lab freezer -20 °C, 3 drawers x 8', 'storage_class': 'cold',
     'positions': ['D%d-%02d' % (s, p) for s in range(1, 4) for p in range(1, 9)]},
    {'id': 'desiccator', 'type': 'benchtop desiccator cabinet with a hinged door on the robot bench, silica gel, 2 shelves x 6; side '
                                 'access only for the fixed arm (161 mm headroom, the door sweep blocks the route: measured in the '
                                 'Isaac Sim storage scene), so not for top-down robot picks', 'storage_class': 'general', 'access': 'side_only',
     'positions': ['S%d-%02d' % (s, p) for s in range(1, 3) for p in range(1, 7)]},
    {'id': 'vial_rack_bench', 'type': 'MACHEREY-NAGEL 702503 rack for 20 mL headspace vials (3 rows x 12) on the robot bench next to '
                                      'the bench shelf, >= 0.25 m free above the vial tops; the robot uses rows A and C only (fingers '
                                      'close along Y: a vial in row B would overlap them by 11 mm), so row B stays empty',
     'storage_class': 'general', 'access': 'top_down', 'rack_type': 'vial_rack_20ml', 'robot_forbidden_prefixes': ['B'],
     'positions': ['%s%02d' % (r, p) for r in 'ABC' for p in range(1, 13)]},
    {'id': 'consumables_cabinet', 'type': 'cabinet for labware consumables, 4 shelves x 8', 'storage_class': 'general',
     'positions': ['S%d-%02d' % (s, p) for s in range(1, 5) for p in range(1, 9)]},
    {'id': 'cylinder_rack', 'type': 'wall rack for gas cylinders with chains, 8 slots (inert and oxidizing gases)',
     'storage_class': 'compressed_gas', 'positions': ['C%d' % p for p in range(1, 9)]},
    {'id': 'gas_cabinet_flammable', 'type': 'ventilated gas cabinet for flammable gases, 4 slots', 'storage_class': 'compressed_gas',
     'positions': ['G%d' % p for p in range(1, 5)]},
    {'id': 'instrument', 'type': 'inside an instrument (position named per instrument)', 'storage_class': 'general', 'positions': []},
]



# ------------------------------------------------------------------ container types (provisional until assets/labware/catalog.json)
PROVISIONAL_TYPES = {
    'bottle_gl45_1000ml_clear': 'borosilicate media bottle GL45, 1000 mL, clear, screw cap',
    'bottle_gl45_500ml_clear': 'borosilicate media bottle GL45, 500 mL, clear, screw cap',
    'bottle_gl45_2500ml_amber': 'solvent bottle GL45, 2.5 L, amber glass (supplier bottle)',
    'bottle_hdpe_500ml': 'HDPE bottle 500 mL, screw cap (buffers, standards)',
    'bottle_hdpe_1000ml': 'HDPE bottle 1 L, screw cap (bases, aqueous reagents)',
    'jar_hdpe_500g': 'wide-neck HDPE jar for 500 g solids',
    'bottle_glass_crm_unit': 'small glass bottle of a certified reference material unit (5-20 g), with certificate',
    'ampoule_box_crm': 'box of sealed glass ampoules / vials of a certified solution standard',
    'vial_crm_metal_shot': 'small vial with metal shot or foil of a calibration metal, with certificate',
    'xrd_holder_zero_background': 'XRD specimen holder (glass / zero-background), powder packed, in a closed box',
    'ir_film_card': 'IR standard film mounted in a card holder',
    'case_reference_plate': 'storage case with a certified reference plate / disc',
    'box_microplates_96_flat_ps': 'box of 96-well flat-bottom polystyrene plates (SBS)',
    'plate_96_flat_ps': '96-well flat-bottom polystyrene plate (SBS footprint)',
    'box_pcr_plates_96_skirted': 'box of skirted 96-well PCR plates (SBS)',
    'bag_tubes_1p5ml': 'bag of 1.5 mL microcentrifuge tubes',
    'bag_tubes_conical_15ml': 'bag of 15 mL conical tubes',
    'bag_tubes_conical_50ml': 'bag of 50 mL conical tubes',
    'bag_tubes_12x75mm_ps': 'bag of 12 x 75 mm polystyrene cytometry tubes',
    'box_vials_2ml_crimp': 'box of 2 mL crimp vials with caps (GC)',
    'box_vials_2ml_screw': 'box of 2 mL screw vials with caps (HPLC)',
    'box_vials_20ml_headspace': 'box of 20 mL headspace vials with crimp caps',
    'tiprack_flex_1000ul': 'Opentrons Flex tip rack, 96 x 1000 uL (SBS)',
    'tiprack_flex_200ul': 'Opentrons Flex tip rack, 96 x 200 uL (SBS)',
    'box_dsc_pans_tzero': 'box of aluminium DSC pans and lids',
    'box_tin_capsules': 'box of tin capsules for CHNS combustion',
    'box_cuvettes_ps_10mm': 'box of disposable 10 mm polystyrene cuvettes',
    'cuvette_quartz_10mm_case': 'case with 10 mm quartz cuvettes',
    'box_nmr_tubes_5mm': 'box of 5 mm NMR tubes',
    'box_gels_precast_10well': 'box of precast mini polyacrylamide gels, 10 wells',
    'roll_heat_seal_film': 'roll of peelable heat-seal film for microplates',
    'box_sensor_chips_spr': 'pack of SPR sensor chips',
    'vial_bead_standard': 'dropper vial of a fluorescent bead standard',
    'kit_ce_reagents': 'capillary electrophoresis reagent kit (box)',
    'vial_protein_ladder': 'vial of a prestained protein ladder',
    'cylinder_50l_200bar': 'steel gas cylinder 50 L water capacity, 200 bar, with regulator',
    'cylinder_10l_200bar': 'steel gas cylinder 10 L water capacity, 200 bar, with regulator',
    'cylinder_10l_acetylene': 'acetylene cylinder 10 L (dissolved in acetone), with regulator',
    'cylinder_10l_co2_liquefied': 'CO2 cylinder 10 L, liquefied, with regulator',
    'in_instrument_item': 'an item already inside an instrument (its own holder: cell, cup, vial, cartridge, tank ...)',
    'quantos_dosing_head': 'METTLER TOLEDO QH016-BNMW powder dosing head, 16 mL, 0.1-250 mg per dose (part 11141503)',
    'quantos_head_container': 'plastic storage container supplied with each Quantos dosing head (holds one head upright)',
    'quantos_target_vial': 'empty 20 mL glass target vial for the Quantos weighing pan',
}

# heights (m, with cap / lid) of provisional types the catalog has not built yet - typical, for the bench-shelf rule below
PROVISIONAL_HEIGHTS_M = {'xrd_holder_zero_background': 0.03, 'ir_film_card': 0.01, 'case_reference_plate': 0.04, 'ampoule_box_crm': 0.06,
                         'quantos_dosing_head': 0.1123, 'quantos_head_container': 0.06, 'quantos_target_vial': 0.0775}
# bench shelf, tier T1: the robot grips a bottle with the fingers closing along the row, so the hand overhangs both neighbours at
# about 0.19-0.26 m: both T1 neighbours of a container must be at most this tall (robot grasp constraint)
BENCH_T1_NEIGHBOUR_MAX_M = 0.185


# ------------------------------------------------------------------ substances not (yet) in the shared tables
NEW_SUBSTANCES = {
    'argon': {'formula': 'Ar', 'cas': '7440-37-1', 'why': 'plasma / carrier / inert gas cylinders'},
    'helium': {'formula': 'He', 'cas': '7440-59-7', 'why': 'GC carrier, pycnometer and combustion-analyzer carrier gas'},
    'silicon': {'formula': 'Si', 'cas': '7440-21-3', 'why': 'XRD line-position standard (SRM 640 type)'},
    'aluminium_oxide': {'formula': 'Al2O3', 'cas': '1344-28-1', 'why': 'corundum XRD intensity standard (SRM 676 type); sapphire DSC disc'},
    'indium': {'formula': 'In', 'cas': '7440-74-6', 'why': 'DSC / TMA temperature and enthalpy calibration metal'},
    'potassium_hydrogen_phthalate': {'formula': 'C8H5KO4', 'cas': '877-24-7', 'why': 'TOC calibration standard, pH 4.01 buffer, titration primary standard'},
    'sodium_dihydrogen_phosphate': {'formula': 'NaH2PO4', 'cas': '7558-80-7', 'why': 'phosphate buffers (pH 7.00); the pH 6.86 standard is KH2PO4 + Na2HPO4'},
    'sodium_tetraborate_decahydrate': {'formula': 'Na2B4O7.10H2O', 'cas': '1303-96-4', 'why': 'pH 9.18 borax buffer'},
    'lithium_chloride': {'formula': 'LiCl', 'cas': '7447-41-8', 'why': 'water-activity verification standards (0.250 / 0.500 aw)'},
    'formazin': {'formula': '(C2H4N2)n', 'cas': '9004-77-7', 'why': 'stabilized formazin turbidity standards'},
    'sulfanilic_acid': {'formula': 'C6H7NO3S', 'cas': '121-57-3', 'why': 'CHNS calibration standard'},
    'ferrocene': {'formula': 'C10H10Fe', 'cas': '102-54-5', 'why': 'internal potential reference for non-aqueous voltammetry'},
    'polystyrene_latex': {'formula': '(C8H8)n dispersion', 'cas': '9003-53-6', 'why': 'DLS size standard (100 nm latex)'},
    'calibration_beads_fluorescent': {'formula': 'polystyrene beads, fluorescent', 'cas': '', 'why': 'cytometer setup and tracking beads'},
    'protein_ladder_prestained': {'formula': 'protein mixture', 'cas': '', 'why': 'SDS-PAGE molecular-weight marker'},
    'hbs_ep_plus_10x': {'formula': 'HEPES/NaCl/EDTA/P20 buffer concentrate', 'cas': '', 'why': 'SPR running buffer'},
}

# ------------------------------------------------------------------ the stock (hand-written, each with its source)
# R(id, type, place, position, contents, storage_class, for_, source, extra)
#   contents: [(substance, amount, unit, grade / concentration, lot / certificate dict or None)]


def R(cid, ctype, place, pos, contents, sclass, for_, source, **extra):
    rec = {'id': cid, 'type': ctype, 'contents': [_c(*c) for c in contents], 'location': {'place': place, 'position': pos},
           'storage_class': sclass, 'for': list(for_), 'source': source}
    if 'instrument' in extra:                            # a container the default set-up has put into an instrument
        rec['location']['instrument'] = extra.pop('instrument')
    rec.update(extra)
    return rec


def _c(sub, amount, unit, grade=None, cert=None, conc=None):
    """One content: a shared substance id, a physical amount (AMOUNT_UNITS), optional grade text, certificate and concentration
    (value, unit) - solutes carry their mass and their concentration in the solution."""
    out = {'substance': sub, 'amount': {'value': amount, 'unit': unit}}
    if grade:
        out['grade'] = grade
    if cert:
        out['certificate'] = cert
    if conc:
        out['concentration'] = {'value': conc[0], 'unit': conc[1]}
    return out


AMOUNT_UNITS = ('g', 'kg', 'mg', 'mL', 'L', 'uL', 'mol', 'mmol')
VOLUME_UNITS = ('mL', 'L', 'uL')


PH = ('metrohm_914', 'metrohm_omnis', 'antonpaar_surpass3')
ALKANES_C8_C20 = [(8, 'octane'), (9, 'nonane'), (10, 'decane'), (11, 'undecane'), (12, 'dodecane'), (13, 'tridecane'),
                  (14, 'tetradecane'), (15, 'pentadecane'), (16, 'hexadecane'), (17, 'heptadecane'), (18, 'octadecane'),
                  (19, 'nonadecane'), (20, 'icosane')]
XRD = ('rigaku_miniflex600_external_cooling',)
TYPICAL_CYL = 'typical: a 50 L cylinder at 200 bar holds about 410 mol of a permanent gas (pV/RT at 20 °C)'

STOCK = [
    # ---- bench shelf (within the robot's reach): what the robot handles most
    R('water_ultrapure_1l', 'bottle_gl45_1000ml_clear', 'bench_shelf', 'T2-02', [('water', 1.0, 'L', 'ASTM type I, 18.2 MOhm cm')], 'general',
      [], 'typical: filled each morning from the lab water system (elga_purelab_flex3)'),
    R('ethanol_500ml', 'bottle_gl45_500ml_clear', 'bench_shelf', 'T1-02', [('ethanol', 0.5, 'L', '>= 99.8 %, absolute')], 'flammable',
      [], 'typical: a 500 mL working bottle for rinsing and cleaning; the stock bottle stays in the flammables cabinet'),
    R('acetone_500ml', 'bottle_gl45_500ml_clear', 'bench_shelf', 'T1-03', [('acetone', 0.5, 'L', '>= 99.5 %')], 'flammable',
      [], 'typical: a 500 mL wash bottle for rinsing glassware'),
    R('buffer_ph4_01', 'bottle_hdpe_500ml', 'bench_shelf', 'T1-04', [
        ('potassium_hydrogen_phthalate', 5.106, 'g', 'primary standard buffer salt', {'value': 'pH 4.005 at 25 °C',
         'standard': 'DIN 19266 / NIST SRM 185 type (KHP 0.05 mol/kg)'}, (0.05, 'mol/kg')),
        ('water', 0.5, 'L', 'solvent (about 0.5 kg)')], 'general', PH,
      'manual: 914_ph_do_conductometer_manual p.55, p.78 (buffer series for calibration); composition: DIN 19266 KHP standard, '
      '0.05 mol/kg x 0.5 kg x 204.22 g/mol = 5.106 g'),
    R('buffer_ph6_86', 'bottle_hdpe_500ml', 'bench_shelf', 'T1-05', [
        ('potassium_dihydrogen_phosphate', 1.701, 'g', '0.025 mol/kg (phosphate pH 6.86 standard, with Na2HPO4)',
         {'value': 'pH 6.865 at 25 °C', 'standard': 'DIN 19266 phosphate standard (KH2PO4 + Na2HPO4, each 0.025 mol/kg; NIST SRM 186 type)'}),
        ('disodium_hydrogen_phosphate', 1.774, 'g', '0.025 mol/kg (phosphate pH 6.86 standard, with KH2PO4)'),
        ('water', 0.5, 'L', 'solvent (about 0.5 kg)')], 'general', PH,
      'manual: 914_ph_do_conductometer_manual p.55, p.78; composition: DIN 19266 / NIST equimolal phosphate standard; the 914 class world '
      'uses the same buffer (template nist_srm_186g_phosphate_equimolal_buffer, pH 6.864)'),
    R('buffer_ph9_18', 'bottle_hdpe_500ml', 'bench_shelf', 'T2-01', [
        ('sodium_tetraborate_decahydrate', 1.907, 'g', 'borax', {'value': 'pH 9.180 at 25 °C', 'standard': 'DIN 19266 / NIST SRM 187 type '
         '(borax 0.01 mol/kg)'}, (0.01, 'mol/kg')),
        ('water', 0.5, 'L', 'solvent (about 0.5 kg)')], 'general', PH,
      'manual: 914_ph_do_conductometer_manual p.55, p.78; composition: 0.01 mol/kg x 0.5 kg x 381.37 g/mol = 1.907 g',
      keep_class='a 0.01 mol/kg borax solution (about 0.4 %) is below the concentration limit at which borax is classified toxic '
      'for reproduction, so it is stored as a general reagent'),
    R('xrd_si_srm640_holder', 'xrd_holder_zero_background', 'bench_shelf', 'T1-01', [('silicon', 0.5, 'g', 'NIST SRM 640 type line-position standard',
      {'lattice_a_nm': 0.5431, 'note': 'certified lattice parameter of the SRM 640 series'})], 'general', XRD,
      'typical: a Si powder standard checks the 2-theta zero of a powder diffractometer (manual: miniflex brochure p.3 lists '
      'qualitative / quantitative use); load it with `chemlab run rigaku_miniflex600_external_cooling load xrd_si_srm640_holder`',
      sample_id='si_srm640_holder'),
    R('xrd_quartz_holder', 'xrd_holder_zero_background', 'standards_cabinet', 'S3-07', [('silica', 0.5, 'g', 'alpha-quartz powder, check sample')],
      'general', XRD, 'the structure class world\'s quartz specimen (sample id quartz_holder)', sample_id='quartz_holder'),
    R('ir_polystyrene_film', 'ir_film_card', 'standards_cabinet', 'S3-06', [('polystyrene', 0.05, 'g', 'film about 38 um',
      {'bands_cm-1': [3060.0, 2849.5, 1601.2, 1028.3, 906.7], 'note': 'wavenumber check bands of polystyrene film'})], 'general',
      ('jasco_ftir4700', 'thermo_nicoletis50'), 'manual: ftir4000_6000_series_brochure p.4 (standard sample: polystyrene film); '
      'registered with the FT/IR-4700 as sample ir_polystyrene_film (template: NIST WebBook spectrum + SRM 1921b certified bands)',
      sample_id='ir_polystyrene_film'),
    R('plate_96_flat_ready', 'plate_96_flat_ps', 'bench_shelf', 'T2-05', [], 'general',
      ('biotek_405ts', 'bmg_clariostarplus', 'opentrons_flex_single_5_1000ul'),
      'typical: one empty plate staged for the plate washer / reader / robot', on_carrier='labware.sbs_plate_carrier'),
    # ---- standards cabinet: check standards and certified reference materials
    R('xrd_calcite_holder', 'xrd_holder_zero_background', 'standards_cabinet', 'S1-01', [('calcium_carbonate', 0.5, 'g', 'calcite powder')],
      'general', XRD, 'the structure class world\'s calcite specimen (sample id calcite_holder)', sample_id='calcite_holder'),
    R('xrd_corundum_srm676_holder', 'xrd_holder_zero_background', 'standards_cabinet', 'S1-02', [('aluminium_oxide', 0.5, 'g',
      'corundum, SRM 676 type intensity standard')], 'general', XRD,
      'typical: corundum is the reference intensity (RIR) standard of powder XRD; phase "corundum" is in the MiniFlex phase list',
      sample_id='corundum_srm676_holder'),
    R('crm_si_powder_srm640', 'bottle_glass_crm_unit', 'standards_cabinet', 'S1-03', [('silicon', 7.5, 'g', 'SRM 640 type unit',
      {'lattice_a_nm': 0.5431})], 'general', XRD, 'typical: the unit bottle the holder above is packed from'),
    R('aw_standard_0_760', 'ampoule_box_crm', 'standards_cabinet', 'S1-04', [
        ('sodium_chloride', 0.7013, 'g', 'NaCl', {'aw_25C': 0.760, 'tolerance': 0.003}, (6.0, 'mol/kg')),
        ('water', 2.0, 'mL', 'solvent (about 2 g per vial)')], 'general', ('aqualab_4te',),
      'manual: aqualab4_manual p.8, p.44-45, p.48 (verification standards; the whole vial is emptied into a sample cup); per vial '
      '(typical: about 2 mL, enough to cover a sample cup bottom): 6.0 mol/kg x 0.002 kg x 58.44 g/mol = 0.701 g NaCl',
      count=5, per_unit=True),
    R('aw_standard_0_250', 'ampoule_box_crm', 'standards_cabinet', 'S1-05', [
        ('lithium_chloride', 1.1369, 'g', 'LiCl', {'aw_25C': 0.250, 'tolerance': 0.003}, (13.41, 'mol/kg')),
        ('water', 2.0, 'mL', 'solvent (about 2 g per vial)')], 'general', ('aqualab_4te',),
      'manual: aqualab4_manual p.44-45, p.49 (0.25 aw standard, whole vial into a sample cup); per vial (typical about 2 mL): '
      '13.41 mol/kg x 0.002 kg x 42.39 g/mol = 1.137 g LiCl', count=5, per_unit=True),
    R('turbidity_stablcal_kit', 'ampoule_box_crm', 'standards_cabinet', 'S1-06', [], 'general', ('hach_tl2300epa',),
      'manual: tl23_series datasheet p.4 (StablCal calibration kit); tl2300 user manual p.16 (StablCal sealed vial standards); '
      'per sealed vial about 25 mL (typical fill of a 25 mm sample cell)',
      units=[{'name': 'StablCal sealed vial %s NTU' % v, 'count': 1,
              'contents': [_c('formazin', 25.0, 'mL', 'stabilized formazin in water', {'turbidity_NTU': v}, (v, 'NTU'))]}
             for v in (0.1, 20, 200, 1000, 4000, 7500)]),
    R('osmolality_cal_400', 'ampoule_box_crm', 'standards_cabinet', 'S1-07', [
        ('sodium_chloride', 0.01266, 'g', 'NaCl', {'osmolality_mOsmol_kg': 400}, (0.2166, 'mol/kg')),
        ('water', 1.0, 'mL', 'solvent (about 1 g per vial)')], 'general', ('knauer_k7400s',),
      'manual: k7400s brochure p.11 (400 mOsmol/kg calibration solution, 12 vials); 0.2166 mol/kg NaCl is the class world\'s '
      '400 mOsmol/kg standard; per vial typical 1 mL (the osmometer uses 50-150 uL per measurement)', count=12, per_unit=True),
    R('conductivity_std_1413', 'bottle_hdpe_500ml', 'standards_cabinet', 'S1-08', [
        ('potassium_chloride', 0.3728, 'g', 'KCl', {'conductivity_uS_cm_25C': 1413}, (0.01, 'mol/L')),
        ('water', 0.5, 'L', 'solvent')], 'general', PH + ('metrohm_856',),
      'typical: 0.01 mol/L KCl is the usual conductivity cell standard (1413 uS/cm at 25 C); 0.01 mol/L x 0.5 L x 74.55 g/mol = '
      '0.373 g; the SurPASS class workflow calibrates with 1.413 mS/cm'),
    R('dsc_indium_crm', 'vial_crm_metal_shot', 'standards_cabinet', 'S2-01', [('indium', 1.0, 'g', '99.999 %',
      {'onset_C': 156.6, 'enthalpy_J_g': 28.71})], 'general', ('ta_dsc2500', 'ta_tma450', 'netzsch_dsc214'),
      'manual: dsc_q_series_getting_started_guide p.49-50 (cell constant / temperature calibration with indium)'),
    R('dsc_zinc_crm', 'vial_crm_metal_shot', 'standards_cabinet', 'S2-02', [('zinc', 1.0, 'g', '99.999 %', {'onset_C': 419.5,
      'enthalpy_J_g': 108.6})], 'general', ('ta_dsc2500',), 'typical: second temperature point next to indium'),
    R('dsc_sapphire_disc', 'case_reference_plate', 'standards_cabinet', 'S2-03', [('aluminium_oxide', 0.025, 'g', 'sapphire disc, Cp reference')],
      'general', ('ta_dsc2500',), 'manual: dsc_q_series_getting_started_guide p.65 (heat-capacity calibration with sapphire)'),
    R('tga_nickel_curie', 'vial_crm_metal_shot', 'standards_cabinet', 'S2-04', [('nickel', 0.5, 'g', 'Curie-point standard',
      {'curie_C': 358.0})], 'general', ('ta_tga5500',), 'manual: discovery_tga_brochure p.7, p.29 (Curie-point temperature calibration)'),
    R('tga_calcium_oxalate', 'bottle_glass_crm_unit', 'standards_cabinet', 'S2-05', [('calcium_oxalate_monohydrate', 10.0, 'g', 'reference grade')],
      'general', ('ta_tga5500',), 'typical: three-step mass-loss check sample of thermogravimetry'),
    R('calorimetry_benzoic_acid', 'bottle_glass_crm_unit', 'desiccator', 'S1-01', [('benzoic_acid', 50.0, 'g', 'calorimetric standard, NBS 39 type',
      {'gross_heat_J_g': 26434})], 'general', ('ika_c6000',), 'manual: c6000_isoperibol_package_1_10 datasheet p.1 (1 g benzoic acid NBS39i)'),
    R('hplc_caffeine_std', 'box_vials_2ml_screw', 'standards_cabinet', 'S2-06', [
        ('caffeine', 0.00015, 'g', 'caffeine', None, (100.0, 'mg/L')),
        ('water', 1.5, 'mL', 'solvent')], 'general',
      ('agilent_hplc1260', 'agilent_1290infinity', 'agilent_1260infinity'),
      'manual: infinitylab_lc_series_specification_compendium p.13, p.16 (water / caffeine tracer for flow and delay checks); '
      'per vial 1.5 mL of 100 mg/L (typical 2 mL vial fill)', count=10, per_unit=True),
    R('gc_alkane_mix', 'box_vials_2ml_crimp', 'flammables_cabinet', 'S2-03',
      [(a, 4.0e-05, 'g', 'n-%s (C%d)' % (a, n), None, (40.0, 'mg/L')) for n, a in ALKANES_C8_C20] + [('hexane', 1.0, 'mL', 'solvent')],
      'flammable', ('agilent_7890b_ssl_fid', 'agilent_gc8890b'),
      'product: Supelco (Sigma-Aldrich) 04070, alkane standard solution C8-C20, about 40 mg/L of each n-alkane C8...C20 in hexane, '
      'analytical standard sold as 1 mL (product page sigmaaldrich.com/US/en/product/sial/04070: retention-index standard for GC); '
      'per 1 mL unit 40 ug of each alkane; two units in stock (typical); a 2 mL GC vial stands in for the ampule (catalog substitute)',
      count=2, per_unit=True, product={'maker': 'Supelco (Sigma-Aldrich)', 'catalogue_number': '04070',
                                       'name': 'Alkane standard solution C8-C20, ~40 mg/L each component in hexane',
                                       'pack': '1 mL', 'url': 'https://www.sigmaaldrich.com/US/en/product/sial/04070'},
      description='Supelco 04070 alkane standard C8-C20 in hexane (1 mL units)'),
    R('chns_sulfanilic_acid', 'bottle_glass_crm_unit', 'desiccator', 'S1-02', [('sulfanilic_acid', 10.0, 'g', 'CHNS standard',
      {'C_pct': 41.61, 'H_pct': 4.07, 'N_pct': 8.09, 'S_pct': 18.51})], 'general', ('elementar_varioel',),
      'quant_analyzer class world (the vario EL standards are sulfanilic acid; theoretical composition)'),
    R('toc_khp_1000', 'bottle_hdpe_500ml', 'standards_cabinet', 'S2-08', [
        ('potassium_hydrogen_phthalate', 1.0627, 'g', 'KHP', {'TC_mg_L': 1000}, (2.1254, 'g/L')),
        ('water', 0.5, 'L', 'solvent')], 'general', ('shimadzu_tocl',),
      'quant_analyzer class world (TOC calibration with a KHP CRM); 1000 mg C/L = 2.1254 g KHP per L (8 C of 204.22 g/mol)'),
    R('ce_sds_mw_kit', 'kit_ce_reagents', 'fridge_4c', 'S1-01', [], 'cold', ('sciex_pa800',),
      'manual: pa800plus_brochure p.10 (SDS-MW analysis kit 390953); sealed kit, its proprietary gel buffer, sample buffer and size '
      'standards are not itemised (no amounts in the documents at hand)',
      description='sealed SDS-MW analysis kit (proprietary reagents, not itemised)'),
    R('ferrocene_ref', 'bottle_glass_crm_unit', 'flammables_cabinet', 'S2-04', [('ferrocene', 5.0, 'g', '98 %')], 'general',
      ('ch_chi440c', 'ch_chi920d', 'gamry_reference620'), 'manual: chi_brochure p.20 (ferrocene oxidation as the example redox couple)'),
    R('xps_ag_au_foils', 'case_reference_plate', 'standards_cabinet', 'S3-02', [('silver', 0.1, 'g', 'Ag foil, sputter-clean'),
      ('copper', 0.1, 'g', 'Cu foil')], 'general', ('kratos_axissupra',),
      'manual: axis_supra_getting_started_guide p.74 (Au 4f images); typical: Ag 3d5/2 and Cu 2p3/2 set the binding-energy scale'),
    R('quartz_control_plate', 'case_reference_plate', 'standards_cabinet', 'S3-03', [('silica', 1.0, 'g', 'quartz control plate',
      {'rotation_deg_589nm': 10.0})], 'general', ('jasco_p4200',),
      'manual: p4000_instrument_management_appnote p.1-2 (calibrated quartz control plate); the plate in use sits in the instrument'),
    R('cell_dummy_gamry', 'case_reference_plate', 'standards_cabinet', 'S3-04', [], 'general', ('gamry_reference620',),
      'manual: reference620_operators_manual p.40, p.44 (calibration with the resistive dummy cell)'),
    R('upyc_calibration_sphere', 'case_reference_plate', 'standards_cabinet', 'S3-05', [('iron', 80.0, 'g', 'stainless-steel sphere',
      {'volume_cm3': 10.0})], 'general', ('antonpaar_ultrapyc5000',),
      'the class workflow calibrates with a 10 cm3 sphere (sphere_volume_m3=1e-5)',
      keep_class='a solid stainless-steel sphere, not the flammable iron powder of the substance table'),
    R('dls_latex_100nm', 'vial_bead_standard', 'fridge_4c', 'S1-02', [('polystyrene_latex', 15, 'mL', '100 nm, 1 % solids',
      {'diameter_nm': 100})], 'cold', ('antonpaar_litesizerdls',), 'the class workflow calibrates size with a 100 nm reference'),
    R('cst_beads', 'vial_bead_standard', 'fridge_4c', 'S1-03', [('calibration_beads_fluorescent', 5, 'mL', 'CS&T research beads (bright, mid, '
      'dim)')], 'cold', ('bd_facsariaiii',), 'manual: facsaria_iii_users_guide p.138 (preparing the CS&T beads); the runtime\'s bead library '
      'has cst_bead_bright / mid / dim'),
    R('hbs_ep_plus_10x', 'bottle_hdpe_500ml', 'fridge_4c', 'S1-04', [('hbs_ep_plus_10x', 0.5, 'L', '10x concentrate')], 'cold',
      ('cytiva_biacoret200',), 'manual: biacore_t200_getting_started p.6 (10X HBS-EP+ buffer)'),
    R('spr_chips_cm5', 'box_sensor_chips_spr', 'fridge_4c', 'S1-05', [], 'cold', ('cytiva_biacoret200',),
      'typical: CM5 is the general-purpose sensor chip; the class workflow docks one (load chip1 --type CM5)'),
    R('gels_tgx_4_20', 'box_gels_precast_10well', 'fridge_4c', 'S1-06', [], 'cold', ('biorad_miniprotean',),
      'the chromatography class world installs a 4-20 % TGX 10-well gel; precast gels are stored at 2-8 °C'),
    R('protein_ladder', 'vial_protein_ladder', 'freezer_m20c', 'D1-01', [('protein_ladder_prestained', 0.5, 'mL', 'prestained, 10-250 kD')],
      'cold', ('biorad_miniprotean',), 'the default gel has ladder_1 (Precision Plus prestained); the ladder is stored at -20 °C'),
    # ---- Quantos powder dosing: heads stand in their own storage containers on the bench shelf's rear tier (top-down grip); the
    # CaCO3 check head is in the Quantos (its empty container waits at T2-03); empty target vials in the bench vial rack
    R('qh_kbr_1', 'quantos_dosing_head', 'bench_shelf', 'T2-04', [('potassium_bromide', 11.4, 'g', 'for IR spectroscopy (Merck 104907)')],
      'general', ('mt_quantos_q2_xpe206dr',),
      'head: METTLER TOLEDO QH016-BNMW (part 11141503), 16 mL, 0.1-250 mg per dose (manual: quantos_operating_instructions p.102, '
      'p.107); fill: 75 % of the 16 mL volume (12 mL, headspace left for the dosing mechanism: assumption) x KBr bulk density '
      '950 kg/m3 (Merck 104907 product page: 900-1000 kg/m3) = 11.4 g. Stands in its storage container (manual p.27). KBr is '
      'hygroscopic: in its closed head on the bench for a working day is acceptable (it takes up water only slowly below about 60 % '
      'RH and deliquesces near 80 %); between working days the head belongs in the desiccator. A host or the robot inserts it; the '
      'Quantos registers it as head qh_kbr_1 with 0.0114 kg of free-flowing powder',
      head_id='qh_kbr_1', free_flowing=True, stored_in='quantos_head_container', product={'maker': 'METTLER TOLEDO', 'catalogue_number': '11141503', 'name': 'QH016-BNMW powder dosing head, 16 ml', 'source': 'Quantos operating instructions p.102 (16 ml, 0.1-250 mg) and p.107 (part number)'}),
    R('qh_caco3_1', 'quantos_dosing_head', 'instrument', 'head_slot', [('calcium_carbonate', 4.0, 'g', 'Sigma-Aldrich 21061 (BioUltra, '
      'precipitated)')], 'general', ('mt_quantos_q2_xpe206dr',),
      'the lab\'s check head, inserted in the Quantos by the default set-up: QH016-BNMW (part 11141503, manual p.102/p.107) with '
      'calcium carbonate Sigma-Aldrich 21061, the powder the dosing specification is given for (manual p.102). Fill 4.0 g: no bulk '
      'density is published for 21061; precipitated CaCO3 is a light powder (assumption 0.3-0.6 g/mL), so 4 g takes 7-13 mL of '
      'the 16 mL head',
      instrument='mt_quantos_q2_xpe206dr', sample_id='qh_caco3_1', head_id='qh_caco3_1', free_flowing=True, product={'maker': 'METTLER TOLEDO', 'catalogue_number': '11141503', 'name': 'QH016-BNMW powder dosing head, 16 ml', 'source': 'Quantos operating instructions p.102 (16 ml, 0.1-250 mg) and p.107 (part number)'}),
    R('qh_caco3_1_container', 'quantos_head_container', 'bench_shelf', 'T2-03', [], 'general', ('mt_quantos_q2_xpe206dr',),
      'the empty storage container of the CaCO3 head (manual p.27: heads are stored in the plastic container they are supplied '
      'in); the head goes back here when it is taken out of the Quantos', description='empty Quantos head storage container (lid off)'),
    R('quantos_vial_pan', 'quantos_target_vial', 'instrument', 'balance_pan', [], 'general', ('mt_quantos_q2_xpe206dr',),
      'empty target vial on the Quantos weighing pan (default set-up): Agilent 5188-2753 20 mL vial, within the sample-container '
      'limits (31-178 mm high, opening >= 6 mm, manual p.103)', instrument='mt_quantos_q2_xpe206dr', sample_id='quantos_vial_pan'),
    R('quantos_vial_1', 'quantos_target_vial', 'vial_rack_bench', 'A01', [], 'general', ('mt_quantos_q2_xpe206dr',),
      'empty target vial for Quantos dosing (Agilent 5188-2753 20 mL vial, fits the pan: manual p.103), in the bench vial rack '
      '(top-down grip)'),
    R('quantos_vial_2', 'quantos_target_vial', 'vial_rack_bench', 'A02', [], 'general', ('mt_quantos_q2_xpe206dr',),
      'empty target vial for Quantos dosing (Agilent 5188-2753 20 mL vial, fits the pan: manual p.103), in the bench vial rack '
      '(top-down grip)'),
    R('quantos_vial_3', 'quantos_target_vial', 'vial_rack_bench', 'A03', [], 'general', ('mt_quantos_q2_xpe206dr',),
      'empty target vial for Quantos dosing (Agilent 5188-2753 20 mL vial, fits the pan: manual p.103), in the bench vial rack '
      '(top-down grip)'),
    # ---- flammables cabinet: stock solvents
    R('ethanol_2_5l', 'bottle_gl45_2500ml_amber', 'flammables_cabinet', 'S1-01', [('ethanol', 2.5, 'L', '>= 99.8 %')], 'flammable', [],
      'typical stock bottle'),
    R('acetone_2_5l', 'bottle_gl45_2500ml_amber', 'flammables_cabinet', 'S1-02', [('acetone', 2.5, 'L', '>= 99.5 %')], 'flammable', [],
      'typical stock bottle'),
    R('isopropanol_2_5l', 'bottle_gl45_2500ml_amber', 'flammables_cabinet', 'S1-03', [('propan_2_ol', 2.5, 'L', '>= 99.5 %')], 'flammable', [],
      'typical stock bottle (cleaning optics, wipes)'),
    R('methanol_hplc_2_5l', 'bottle_gl45_2500ml_amber', 'flammables_cabinet', 'S1-04', [('methanol', 2.5, 'L', 'HPLC grade')], 'flammable',
      ('agilent_hplc1260', 'agilent_1290infinity', 'agilent_1260infinity'), 'typical HPLC mobile-phase solvent'),
    R('acetonitrile_hplc_2_5l', 'bottle_gl45_2500ml_amber', 'flammables_cabinet', 'S1-05', [('acetonitrile', 2.5, 'L', 'HPLC grade')],
      'flammable', ('agilent_hplc1260', 'agilent_1290infinity'), 'typical HPLC mobile-phase solvent'),
    R('hexane_2_5l', 'bottle_gl45_2500ml_amber', 'flammables_cabinet', 'S1-06', [('hexane', 2.5, 'L', '>= 97 %')], 'flammable', [],
      'typical stock bottle (GC dilutions, extraction)'),
    R('toluene_2_5l', 'bottle_gl45_2500ml_amber', 'flammables_cabinet', 'S2-01', [('toluene', 2.5, 'L', '>= 99.5 %')], 'flammable', [],
      'typical stock bottle'),
    R('ethyl_acetate_2_5l', 'bottle_gl45_2500ml_amber', 'flammables_cabinet', 'S2-02', [('ethyl_acetate', 2.5, 'L', '>= 99.5 %')], 'flammable',
      [], 'typical stock bottle'),
    # ---- acids / bases / oxidizers / toxics
    R('hcl_37_1l', 'bottle_gl45_1000ml_clear', 'acids_cabinet', 'S1-01', [
        ('hydrochloric_acid', 438.1, 'g', 'HCl', None, (37.0, '% w/w')), ('water', 745.9, 'g', 'solvent')], 'acid', [],
      'typical stock: 1 L of 37 % w/w hydrochloric acid, density 1.184 g/mL at 20 C (1184 g: 438.1 g HCl + 745.9 g water)'),
    R('hno3_65_1l', 'bottle_gl45_1000ml_clear', 'acids_cabinet', 'S1-02', [
        ('nitric_acid', 904.2, 'g', 'HNO3, trace-metal grade', None, (65.0, '% w/w')), ('water', 486.9, 'g', 'solvent')], 'acid',
      ('milestone_ethosup', 'cem_mars6', 'shimadzu_icpe9800', 'shimadzu_icpms2030'),
      'typical: digestion and ICP sample acid; 1 L of 65 % w/w nitric acid, density 1.391 g/mL at 20 C (904.2 g HNO3 + 486.9 g water)'),
    R('h2so4_96_1l', 'bottle_gl45_1000ml_clear', 'acids_cabinet', 'S1-03', [
        ('sulfuric_acid', 1761.6, 'g', 'H2SO4', None, (96.0, '% w/w')), ('water', 73.4, 'g', 'solvent')], 'acid', [],
      'typical stock: 1 L of 96 % w/w sulfuric acid, density 1.835 g/mL at 20 C (1761.6 g H2SO4 + 73.4 g water)'),
    R('acetic_acid_1l', 'bottle_gl45_1000ml_clear', 'acids_cabinet', 'S1-04', [('acetic_acid', 1.0, 'L', 'glacial')], 'acid', [], 'typical stock'),
    R('naoh_pellets_1kg', 'jar_hdpe_500g', 'bases_cabinet', 'S1-01', [('sodium_hydroxide', 500.0, 'g', 'pellets >= 98 %')], 'base', [],
      'typical stock'),
    R('naoh_0_1n_1l', 'bottle_hdpe_1000ml', 'bases_cabinet', 'S1-02', [
        ('sodium_hydroxide', 4.000, 'g', 'NaOH', {'titer': '0.1000 mol/L +- 0.2 %'}, (0.1, 'mol/L')), ('water', 1.0, 'L', 'solvent')],
      'base', ('metrohm_omnis', 'metrohm_899'), 'typical titrant: 0.1 mol/L x 1 L x 40.00 g/mol = 4.000 g NaOH'),
    R('ammonia_25_1l', 'bottle_hdpe_1000ml', 'bases_cabinet', 'S1-03', [
        ('ammonia', 226.8, 'g', 'NH3', None, (25.0, '% w/w')), ('water', 680.2, 'g', 'solvent')], 'base', [],
      'typical stock: 1 L of 25 % w/w ammonia solution, density 0.907 g/mL at 20 C (226.8 g NH3 + 680.2 g water)'),
    R('h2o2_30_500ml', 'bottle_hdpe_500ml', 'oxidizers_cabinet', 'S1-01', [
        ('hydrogen_peroxide', 166.5, 'g', 'H2O2', None, (30.0, '% w/w')), ('water', 388.5, 'g', 'solvent')], 'oxidizer',
      ('milestone_ethosup', 'cem_mars6'),
      'typical: digestion oxidant; 0.5 L of 30 % w/w hydrogen peroxide, density 1.11 g/mL at 20 C (166.5 g H2O2 + 388.5 g water)'),
    R('silver_nitrate_100g', 'bottle_glass_crm_unit', 'oxidizers_cabinet', 'S1-02', [('silver_nitrate', 100.0, 'g', '>= 99 %')], 'oxidizer', [],
      'typical stock (chloride titrations)'),
    R('phenol_100g', 'bottle_glass_crm_unit', 'toxics_cabinet', 'S1-01', [('phenol', 100.0, 'g', '>= 99 %')], 'toxic', [], 'typical stock'),
    R('aniline_100ml', 'bottle_gl45_500ml_clear', 'toxics_cabinet', 'S1-02', [('aniline', 0.1, 'L', '>= 99 %')], 'toxic', [], 'typical stock'),
    R('nitrobenzene_100ml', 'bottle_gl45_500ml_clear', 'toxics_cabinet', 'S1-03', [('nitrobenzene', 0.1, 'L', '>= 99 %')], 'toxic', [],
      'typical stock'),
    # ---- desiccator: hygroscopic primary standards
    R('kbr_ir_grade', 'bottle_glass_crm_unit', 'desiccator', 'S1-03', [('potassium_bromide', 88.6, 'g', 'for IR spectroscopy (Uvasol)')],
      'general', ('jasco_ftir4700', 'mt_quantos_q2_xpe206dr'),
      'product: Merck 104907 potassium bromide for IR spectroscopy Uvasol (100 g pack; bulk density 900-1000 kg/m3, product page); '
      '11.4 g of it filled the Quantos head qh_kbr_1; kept in the desiccator (hygroscopic); manual: ftir4600_kbr_procedure quick '
      'guide (KBr pellet method)',
      product={'maker': 'Merck', 'catalogue_number': '104907', 'name': 'Potassium bromide for IR spectroscopy Uvasol', 'pack': '100 g',
               'bulk_density_kg_m3': [900, 1000], 'url': 'https://www.merckmillipore.com/product/Potassium-bromide,MDA_CHEM-104907'}),
    R('khp_primary_std', 'bottle_glass_crm_unit', 'desiccator', 'S2-01', [('potassium_hydrogen_phthalate', 50.0, 'g', 'primary standard, dried')],
      'general', ('metrohm_omnis', 'metrohm_899'), 'typical: titer determination of NaOH'),
    R('sodium_carbonate_primary', 'bottle_glass_crm_unit', 'desiccator', 'S2-02', [('sodium_carbonate', 50.0, 'g', 'primary standard, dried')],
      'general', ('metrohm_omnis',), 'typical: titer determination of acids'),
    # ---- consumables cabinet
    R('cons_plates_96_flat', 'box_microplates_96_flat_ps', 'consumables_cabinet', 'S1-01', [], 'general',
      ('biotek_405ts', 'bmg_clariostarplus', 'opentrons_flex_single_5_1000ul'), 'typical', count=50),
    R('cons_pcr_plates', 'box_pcr_plates_96_skirted', 'consumables_cabinet', 'S1-02', [], 'general',
      ('biorad_cfxopus', 'azenta_automated_roll_heat_sealer_a4s'), 'typical', count=25),
    R('cons_tubes_1p5ml', 'bag_tubes_1p5ml', 'consumables_cabinet', 'S1-03', [], 'general', [], 'typical', count=500),
    R('cons_tubes_15ml', 'bag_tubes_conical_15ml', 'consumables_cabinet', 'S1-04', [], 'general', [], 'typical', count=50),
    R('cons_tubes_50ml', 'bag_tubes_conical_50ml', 'consumables_cabinet', 'S1-05', [], 'general', [], 'typical', count=25),
    R('cons_tubes_12x75', 'bag_tubes_12x75mm_ps', 'consumables_cabinet', 'S1-06', [], 'general', ('bd_facsariaiii',),
      'manual: facsaria_iii_users_guide (12 x 75 mm sample tubes); the runtime\'s tube types include 12x75', count=125),
    R('cons_vials_gc', 'box_vials_2ml_crimp', 'consumables_cabinet', 'S1-07', [], 'general', ('agilent_7890b_ssl_fid', 'agilent_gc8890b'),
      'typical', count=100),
    R('cons_vials_hplc', 'box_vials_2ml_screw', 'consumables_cabinet', 'S1-08', [], 'general',
      ('agilent_hplc1260', 'agilent_1290infinity', 'agilent_1260infinity'), 'typical', count=100),
    R('cons_vials_hs', 'box_vials_20ml_headspace', 'consumables_cabinet', 'S2-01', [], 'general', ('agilent_hs8697',), 'typical', count=100),
    R('cons_tips_1000', 'tiprack_flex_1000ul', 'consumables_cabinet', 'S2-02', [], 'general', ('opentrons_flex_single_5_1000ul',),
      'typical spare rack (one is on the deck)', count=96),
    R('cons_tips_200', 'tiprack_flex_200ul', 'consumables_cabinet', 'S2-03', [], 'general', ('opentrons_flex_single_5_1000ul',),
      'typical spare rack (one is on the deck)', count=96),
    R('cons_dsc_pans', 'box_dsc_pans_tzero', 'consumables_cabinet', 'S2-04', [], 'general', ('ta_dsc2500',), 'typical', count=100),
    R('cons_tin_capsules', 'box_tin_capsules', 'consumables_cabinet', 'S2-05', [], 'general', ('elementar_varioel',), 'typical', count=100),
    R('cons_cuvettes_ps', 'box_cuvettes_ps_10mm', 'consumables_cabinet', 'S2-06', [], 'general', [], 'typical', count=100),
    R('cons_cuvettes_quartz', 'cuvette_quartz_10mm_case', 'consumables_cabinet', 'S2-07', [], 'general', [], 'typical', count=4),
    R('cons_nmr_tubes', 'box_nmr_tubes_5mm', 'consumables_cabinet', 'S2-08', [], 'general', ('magritek_spinsolve80',), 'typical', count=25),
    R('cons_seal_film', 'roll_heat_seal_film', 'consumables_cabinet', 'S3-01', [], 'general', ('azenta_automated_roll_heat_sealer_a4s',),
      'typical spare roll (one roll is threaded in the sealer)', count=1),
]

# ---- gas cylinders: (id, gas, cylinder type, amount mol, grade, place, position, instruments fed, source)
CYLINDERS = [
    ('cyl_n2_1', 'nitrogen', 'cylinder_50l_200bar', 410.0, '5.0 (99.999 %)', 'cylinder_rack', 'C1',
     ('ta_dsc2500', 'ta_tga5500', 'ta_tma450', 'ta_dma850', 'netzsch_lfa467', 'surface_dvsresolution', 'porometer_poroluxrevo')),
    ('cyl_n2_2', 'nitrogen', 'cylinder_50l_200bar', 400.1, '5.0 (99.999 %)', 'cylinder_rack', 'C2',
     ('laboao_cvdltf12p50', 'bronkhorst_cemw', 'inert_puresolvmd', 'gas_flavourspec25gc', 'teledyne_atomxxyz', 'bronkhorst_p702cv_5bar_gauge_n2')),
    ('cyl_ar_1', 'argon', 'cylinder_50l_200bar', 447.7, '5.0 (99.999 %)', 'cylinder_rack', 'C3',
     ('shimadzu_icpe9800', 'shimadzu_icpms2030', 'laboao_cvdltf12p50', 'inert_purelab_he_2gb')),
    ('cyl_he_1', 'helium', 'cylinder_50l_200bar', 410.0, '5.0 (99.999 %)', 'cylinder_rack', 'C4',
     ('agilent_7890b_ssl_fid', 'agilent_gc8890b', 'antonpaar_ultrapyc5000', 'elementar_varioel', 'leco_onh836', 'markes_unityxr')),
    ('cyl_o2_1', 'oxygen', 'cylinder_50l_200bar', 410.0, '4.8 (99.998 %)', 'cylinder_rack', 'C5',
     ('ta_tga5500', 'elementar_varioel', 'shimadzu_tocl', 'ika_c6000')),
    ('cyl_air_1', [('nitrogen', 0.795), ('oxygen', 0.205)], 'cylinder_50l_200bar', 410.0, 'synthetic air, hydrocarbon-free', 'cylinder_rack', 'C6',
     ('agilent_7890b_ssl_fid', 'ta_dma850', 'jena_contraa800')),
    ('cyl_co2_ref', 'carbon_dioxide', 'cylinder_10l_co2_liquefied', 170.0, '4.5, isotope reference gas', 'cylinder_rack', 'C7',
     ('elementar_isoprimeprecision',)),
    ('cyl_h2_1', 'hydrogen', 'cylinder_50l_200bar', 360.0, '5.0 (99.999 %)', 'gas_cabinet_flammable', 'G1', ('agilent_7890b_ssl_fid', 'agilent_gc8890b')),
    ('cyl_ch4_1', 'methane', 'cylinder_50l_200bar', 525.1, '4.5 (99.995 %)', 'gas_cabinet_flammable', 'G2', ('laboao_cvdltf12p50',)),
    ('cyl_c2h2_1', 'acetylene', 'cylinder_10l_acetylene', 77.0, '2.6, dissolved in acetone', 'gas_cabinet_flammable', 'G3', ('jena_contraa800',)),
]
CYL_SOURCE = {
    'cylinder_50l_200bar': TYPICAL_CYL + ' (hydrogen about 360 mol: compressibility about 1.13 at 200 bar)',
    'cylinder_10l_200bar': 'typical: 10 L water capacity at 200 bar',
    'cylinder_10l_acetylene': 'typical: a 10 L acetylene cylinder holds about 2 kg (77 mol) dissolved in acetone',
    'cylinder_10l_co2_liquefied': 'typical: a 10 L CO2 cylinder holds 7.5 kg (170 mol) liquefied',
}
# cylinders whose content is computed from tabulated real-gas densities (the laboao gas panel)
_NIST_CYL = ('NIST Chemistry WebBook SRD 69 density at 288.15 K and 201.01 bar abs (200 bar gauge fill at 15 C), x water capacity; table and '
             'query URLs: outputs/expansion_batch04/t5_automation/laboao_cvdltf12p50/sources/nist_fluid_288K_201bar.json')
CYL_SOURCE_BY_ID = {
    'cyl_n2_2': 'full 50 L cylinder: 8.0018 mol/L x 50 L = 400.1 mol (' + _NIST_CYL + ')',
    'cyl_ar_1': 'full 50 L cylinder: 8.9545 mol/L x 50 L = 447.7 mol (' + _NIST_CYL + ')',
    'cyl_ch4_1': 'full 50 L cylinder: 10.502 mol/L x 50 L = 525.1 mol (' + _NIST_CYL + '); a 50 L cylinder like the hydrogen one (G1), was 10 L / 95 mol',
}


# ------------------------------------------------------------------ task-world variants of the inventory
# name -> {"move": {record id: new location (+ "stored_in")}, "drop": [record ids], "entry_steps": {instrument: [set-up steps]}}
# make_config writes configs/inventory_task_<name>.json and configs/all203_<name>.json (the lab config of that task world: same
# default lab plus the entry steps, inventory = the variant file); chemlab and Matterix load the same pair.
VARIANTS = {
    # robot powder dosing: the Quantos starts empty (no head, no vial); the CaCO3 check head stands in its storage container on
    # the bench shelf T2-03 and the pan vial waits in the vial rack; the robot inserts the KBr head and a vial itself
    'quantos_powder': {
        'move': {'qh_caco3_1': {'location': {'place': 'bench_shelf', 'position': 'T2-03'}, 'stored_in': 'quantos_head_container'},
                 'quantos_vial_pan': {'location': {'place': 'vial_rack_bench', 'position': 'A04'}}},
        'drop': ['qh_caco3_1_container'],              # the cup is the head's stored_in now
        'set': {v: {'cap': 'off'} for v in ('quantos_vial_1', 'quantos_vial_2', 'quantos_vial_3', 'quantos_vial_pan')},   # opened for dosing
        'entry_steps': {'mt_quantos_q2_xpe206dr': [{'replace_default': ['consumable']}]},
        'about': 'robot powder dosing task (BRIEF_QUANTOS): Quantos empty at the start; heads in their containers on the bench '
                 'shelf T2-03 (CaCO3) / T2-04 (KBr); empty 20 mL vials in the bench vial rack A01..A04 (row B stays empty)',
    },
}


def variant(inv, name):
    """The inventory of a task-world variant (VARIANTS[name]) built from the default inventory ``inv``."""
    import copy as _copy
    v = VARIANTS[name]
    out = _copy.deepcopy(inv)
    out['variant'] = name
    out['about'] = inv['about'] + ' Variant %s: %s' % (name, v['about'])
    out['containers'] = [r for r in out['containers'] if r['id'] not in set(v.get('drop') or [])]
    for r in out['containers']:
        r.update(_copy.deepcopy((v.get('set') or {}).get(r['id']) or {}))
        mv = (v.get('move') or {}).get(r['id'])
        if mv:
            r['location'] = dict(mv['location'])
            for k in ('stored_in',):
                if k in mv:
                    r[k] = catalog_type(mv[k])[0] if mv[k] in PROVISIONAL_TYPES else mv[k]
    return out


# ------------------------------------------------------------------ the labware catalog (assets/labware/catalog.json)
CATALOG = CHEMSIM_ROOT / 'assets' / 'labware' / 'catalog.json'


@lru_cache(maxsize=1)
def _catalog():
    return json.loads(CATALOG.read_text(encoding='utf-8')) if CATALOG.exists() else {}


def _expand(rng, prefix=''):
    """'S1-01..S2-06' -> S1-01 .. S1-06, S2-01 .. S2-06 (rows x positions); 'C1..C8' -> C1 .. C8; comma-separated ranges."""
    import re
    out = []
    for part in [x.strip() for x in rng.split(',') if x.strip()]:
        a, _, b = part.partition('..')
        m1, m2 = re.match(r'^([A-Z]+)(\d+)(?:-(\d+))?$', a), re.match(r'^([A-Z]+)(\d+)(?:-(\d+))?$', b or a)
        if m1.group(3) is None:
            out += ['%s%s%d' % (prefix, m1.group(1), i) for i in range(int(m1.group(2)), int(m2.group(2)) + 1)]
        else:
            w = len(m1.group(3))
            for r in range(int(m1.group(2)), int(m2.group(2)) + 1):
                out += ['%s%s%d-%0*d' % (prefix, m1.group(1), r, w, i) for i in range(int(m1.group(3)), int(m2.group(3)) + 1)]
    return out


def places():
    """PLACES with the furniture model and the position ids of the labware catalog (when it maps the place)."""
    cat = _catalog().get('places') or {}
    out = []
    for p in PLACES:
        p = dict(p)
        if p.get('rack_type'):                              # a rack place: the catalog's rack model is its furniture
            p['furniture'] = catalog_type(p['rack_type'])[0]
        c = cat.get(p['id'])
        if c:
            p['furniture'] = c.get('furniture')
            p['reach'] = c.get('reach')
            p['positions'] = _expand(c['positions'], c.get('slot_prefix') or '')
        out.append(p)
    return out


def catalog_type(provisional, record_id=None):
    """(type id, status, note, count) of a provisional container type: the catalog's type when it is built (exact,
    packaging_of, substitute; per record when the map gives per_record_units), else the provisional id (not_built)."""
    m = (_catalog().get('inventory_type_map') or {}).get(provisional) or {}
    m = (m.get('per_record_units') or {}).get(record_id) or m
    if m.get('type') and m.get('status') in ('exact', 'packaging_of', 'substitute'):
        return m['type'], m['status'], m.get('note'), m.get('count')
    return provisional, m.get('status') or 'provisional', m.get('note'), None


# ------------------------------------------------------------------ shared substance tables
@lru_cache(maxsize=1)
def _tables():
    sub = json.loads((CHEMSIM_ROOT / 'assets' / 'reactions' / 'substances.json').read_text(encoding='utf-8'))['substances']
    p = CHEMSIM_ROOT / 'assets' / 'reactions' / 'spectral_library' / 'compounds.json'
    comp = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
    comp = comp.get('compounds', comp) if isinstance(comp, dict) else {c.get('id'): c for c in comp}
    cw = json.loads((CHEMSIM_ROOT / 'assets' / 'reactions' / 'shared_id_crosswalk.json').read_text(encoding='utf-8'))
    alias = dict(cw.get('substance_aliases') or {})
    for lib in (cw.get('libraries') or {}).values():
        for rec in lib.get('records') or []:
            sh = rec.get('shared_ids') or {}
            target = sh.get('substances') or sh.get('spectral_library')
            if rec.get('record_id') and isinstance(target, str):
                alias.setdefault(rec['record_id'], target)
    return sub, comp, alias


CLASS_ORDER = ('compressed_gas', 'flammable', 'oxidizer', 'toxic', 'acid', 'base', 'cold', 'general')


def table_storage_class(sid):
    """The shared substance table's storage class of ``sid`` (substances.json 1.1+), or None."""
    sub, _, _ = _tables()
    return (sub.get(sid) or {}).get('storage_class')


def shared_substance(sid):
    """(shared id, table) for an id: the id itself when substances.json / the compound table has it, else its crosswalk alias;
    (sid, None) when neither knows it (a new substance)."""
    sub, comp, alias = _tables()
    for x in (sid, alias.get(sid)):
        if x in sub:
            return x, 'substances.json'
        if x in comp:
            return x, 'spectral_library/compounds.json'
    return sid, None


# ------------------------------------------------------------------ containers the default set-up put into instruments
_ITEM_KEYS = ('sample_id', 'specimen_id', 'solution_id', 'tube_id', 'plate_id', 'gel_id', 'roll_id', 'head_id', 'container_id', 'rack_id',
              'item_id', 'item', 'kit', 'reagent',
              'bottle', 'label', 'standard_id', 'reference_id', 'template_id', 'substance_id')
_LOADERS = ('load', 'place', 'install', 'mount', 'provide', 'prepare', 'fill', 'register', 'attach', 'insert', 'present', 'configure_bath')
_SUBSTANCE_KEYS = ('substance_id', 'liquid', 'solvent', 'base', 'buffer_id', 'matrix_id', 'reagent', 'liquid_id', 'species_id')
_POS_KEYS = ('slot', 'position', 'channel', 'station', 'cell', 'lane', 'slot_index', 'rack', 'vial_index', 'chamber')


# runtime names of shared substances (CAS-style or abbreviations used by some runtimes)
RUNTIME_ALIASES = {'THF': 'tetrahydrofuran', 'cas_67_56_1': 'methanol', 'DMF': 'dmf', 'DMSO': 'dmso', 'DCM': 'dichloromethane', 'NaCl': 'sodium_chloride'}


def _substance_or_material(v):
    """A shared substance id when the shared tables know the name (directly, by crosswalk or by RUNTIME_ALIASES); otherwise the
    runtime's own material name (a mixture, a reagent kit, a template material), not claimed as a substance."""
    sid, table = shared_substance(RUNTIME_ALIASES.get(v, v))
    return {'substance': sid} if table else {'material': v}


def _amount(args):
    for k, unit, f in (('volume_m3', 'mL', 1e6), ('mass_kg', 'g', 1e3), ('source_mass_kg', 'g', 1e3), ('content_mass_kg', 'g', 1e3),
                       ('amount_mol', 'mol', 1.0), ('initial_mol', 'mol', 1.0)):
        v = args.get(k)
        if isinstance(v, (int, float)) and v:
            return {'value': round(v * f, 6), 'unit': unit}
    return None


# set-up steps that install hardware (mounts, accessories, columns, cartridges, slots ...), not contents
HARDWARE_NOUNS = {'mount', 'accessory', 'slot', 'register gc', 'column', 'cartridge', 'head', 'probe', 'stage', 'piston', 'vessel',
                  'cell', 'loading', 'grating', 'fixture', 'load template cell', 'stylus', 'pin'}


def _phase(sid):
    sub, _, _ = _tables()
    s = sub.get(sid) or {}
    return 'mixture' if s.get('kind') == 'mixture' else s.get('phase')


def _instrument_records(cfg, stock_ids=()):
    """What the default set-up put into each instrument (samples, standards, solutions, consumables; not hardware), one entry
    per item, in plain words (no capability ids).  These are the instruments' own state: their composition is defined by each
    instrument's runtime (its sample / template library), so a runtime material is named with "defined_by".  Items that are a
    stock container registered with the instrument (``stock_ids``: the stock records' sample ids) stay with the stock record."""
    from .hosts import contract_index
    idx = contract_index()
    out = []
    for e in cfg['instruments']:
        contract = None
        seen = set()
        mine = []
        for st in e.get('setup') or []:
            if st.get('default') not in ('standard', 'consumable', 'accessory'):
                continue
            name = st.get('host') or st.get('capability') or ''
            if any(k in name for k in ('unload', 'remove', 'eject_sample')):     # the item taken out again (e.g. the blank)
                noun = name.replace('host_', '').split('.', 1)[0].replace('_', ' ')
                for i in range(len(mine) - 1, -1, -1):
                    if mine[i]['kind'].split(' (')[0] == noun:
                        seen.discard(mine[i].pop('_key'))
                        del mine[i]
                        break
                continue
            if not any(k in name for k in _LOADERS):
                continue
            args = st.get('arguments')
            if args == 'contract_example':
                if contract is None:
                    contract = json.loads(idx[e['asset_type_id']].read_text(encoding='utf-8'))
                args = (contract['capabilities'].get(name) or {}).get('example_arguments') or {}
            if not isinstance(args, dict):
                args = st.get('kwargs') or {}
            item = next((str(args[k]) for k in _ITEM_KEYS if isinstance(args.get(k), (str, int)) and str(args.get(k)) != ''), None)
            noun = name.replace('host_', '').split('.', 1)[0].replace('_', ' ')
            item = item or noun.replace(' ', '_')
            if item in stock_ids or noun in HARDWARE_NOUNS:
                continue
            pos = next(('%s %s' % (k, args[k]) for k in _POS_KEYS if args.get(k) not in (None, '')), None)
            key = (item, noun, pos)
            if key in seen:
                continue
            seen.add(key)
            contents = []
            defined_by = 'the %s runtime (sample / template %s)' % (e['id'], args.get('template_id') or item)
            am = _amount(args)
            for k in _SUBSTANCE_KEYS:
                v = args.get(k)
                if isinstance(v, str) and v:
                    d = _substance_or_material(v)
                    if d.get('substance') and am and am['unit'] in VOLUME_UNITS and _phase(d['substance']) not in ('liquid', 'mixture'):
                        d = {'material': item, 'main_component': d['substance']}       # a solution of a solid / dissolved species
                    if am:
                        d['amount'] = am
                    contents.append(d)
                    break
            for comp in args.get('components') or []:
                if isinstance(comp, dict) and (comp.get('substance_id') or comp.get('species_id')):
                    sid = comp.get('substance_id') or comp.get('species_id')
                    d = _substance_or_material(sid)
                    for ck, unit in (('molality_mol_kg', 'mol/kg'), ('concentration_mol_m3', 'mol/m3')):
                        if comp.get(ck) is not None:
                            d['concentration'] = {'value': comp[ck], 'unit': unit}
                    if comp.get('amount_mol') is not None:
                        d['amount'] = {'value': comp['amount_mol'], 'unit': 'mol'}
                    elif comp.get('volume_m3') is not None:
                        d['amount'] = {'value': round(comp['volume_m3'] * 1e6, 6), 'unit': 'mL'}
                    contents.append(d)
            if not contents:
                d = {'material': item}
                if args.get('template_id'):
                    d['reference_template'] = args['template_id']
                if am:
                    d['amount'] = am
                contents.append(d)
            for d in contents:
                if 'material' in d:
                    d['defined_by'] = defined_by
            kind = noun + (' (%s)' % args['kind'] if isinstance(args.get('kind'), str) and args['kind'] != noun else '')
            mine.append({'id': '%s/%s' % (e['id'], item), 'kind': kind, 'contents': contents,
                         'location': {'place': 'instrument', 'instrument': e['id'], 'position': pos},
                         'for': [e['id']], 'source': st.get('why') or 'put in by the default lab set-up (%s)' % kind, '_key': key})
        for r in mine:
            r.pop('_key', None)
        ids = {}
        for r in mine:                                   # ids unique per instrument
            n = ids.get(r['id'], 0)
            ids[r['id']] = n + 1
            if n:
                r['id'] = '%s_%d' % (r['id'], n + 1)
        out += mine
    return out


CONVENTIONS = (
    'containers: one record per container in storage (Matterix spawns these). contents: shared substance ids '
    '(assets/reactions/substances.json) with a physical amount (g, kg, mg, mL, L, uL, mol, mmol); a solution lists its solvent '
    '(a liquid, by volume or mass) and every solute with its mass (or amount in mol) and its concentration; a mixture substance '
    '(kind mixture in the table) describes itself. count = number of units in the container; per_unit = true: the contents '
    'amounts are those of one unit; units = [{name, count, contents}] for a pack of different units (e.g. a kit or a standards '
    'set). instrument_contents: what the default set-up put into each instrument (the instruments\' own state, not spawned): a '
    'runtime material is named with defined_by (its composition is the instrument runtime\'s sample / template). cap: "on" | "off" - the closure of a container whose type '
    'has one (catalog closure: screw cap, hinged or lift-off lid); absent = "on" (closed); "off" = opened, the cap set aside '
    '(e.g. vials prepared open for a dosing run).')


def all_contents(r):
    """Every content of a record: its contents and those of its units."""
    out = list(r.get('contents') or [])
    for u in r.get('units') or []:
        out += list(u.get('contents') or [])
    return out


def _storage_class(r, place_class):
    """A container's storage class: the most restrictive class the shared substance table gives its contents; the record's own
    class when the table knows none of them (mixtures, compound-table solvents); "cold" items keep "cold" unless hazardous."""
    if r.get('keep_class'):                              # the record states why its class differs from the pure substance's
        return r['storage_class']
    found = [table_storage_class(c['substance']) for c in all_contents(r) if c.get('substance')]
    found = [x for x in found if x]
    if not found:
        return r.get('storage_class') or 'general'
    best = min(found, key=CLASS_ORDER.index)
    if best == 'general' and (r.get('storage_class') == 'cold' or place_class == 'cold'):
        return 'cold'
    return best


def build(cfg):
    """The default inventory for a lab config whose set-up already holds the default world (make_config.build())."""
    pls = places()
    by_place = {p['id']: p for p in pls}
    recs = [dict(r, location=dict(r['location'])) for r in STOCK]
    for cid, gas, ctype, mol, grade, place, pos, feeds in CYLINDERS:
        if isinstance(gas, str):
            contents = [{'substance': gas, 'amount': {'value': mol, 'unit': 'mol'}, 'grade': grade}]
        else:
            contents = [{'substance': g, 'amount': {'value': round(mol * x, 3), 'unit': 'mol'}, 'mole_fraction': x, 'grade': grade}
                        for g, x in gas]
        recs.append({'id': cid, 'type': ctype, 'contents': contents,
                     'location': {'place': place, 'position': pos}, 'storage_class': 'compressed_gas', 'for': list(feeds),
                     'connected_to': list(feeds), 'source': CYL_SOURCE_BY_ID.get(cid, CYL_SOURCE[ctype])})
    inside = _instrument_records(cfg, {r.get('sample_id') for r in STOCK if r.get('sample_id')})
    ids = {e['id'] for e in cfg['instruments']}
    cat_types = _catalog().get('types') or {}
    for r in recs:
        r['for'] = [i for i in r.get('for') or [] if i in ids]          # only instruments of this lab
        if 'connected_to' in r:
            r['connected_to'] = [i for i in r['connected_to'] if i in ids]
        loc, p = r['location'], by_place.get(r['location']['place']) or {}
        if p.get('positions') and loc.get('position') not in p['positions']:     # catalog slot prefixes (A: / B: compartments)
            pref = next((x[:x.index(':') + 1] for x in p['positions'] if ':' in x), '')
            if pref and pref + str(loc.get('position')) in p['positions']:
                loc['position'] = pref + loc['position']
        if r['type'] in PROVISIONAL_TYPES and r['type'] != 'in_instrument_item':
            r.setdefault('description', PROVISIONAL_TYPES[r['type']])
            t, status, note, count = catalog_type(r['type'], r['id'])
            r['provisional_type'] = r['type']
            r['type'], r['type_status'] = t, status
            if note:
                r['type_note'] = note
            if count and not r.get('count'):
                r['count'] = count
        if r.get('stored_in') in PROVISIONAL_TYPES:                     # the container it stands in (e.g. a head's storage cup)
            r['stored_in'] = catalog_type(r['stored_in'])[0]
        if r['location']['place'] != 'instrument':
            r['storage_class'] = _storage_class(r, p.get('storage_class'))
    used = sorted({c['substance'] for r in recs + inside for c in all_contents(r) if c.get('substance')})
    new = {s: NEW_SUBSTANCES.get(s, {'why': 'not in the shared tables'}) for s in used if shared_substance(s)[1] is None}
    types = sorted({r['type'] for r in recs})
    return {'about': 'The default lab\'s physical inventory (chemlab/inventory.py): one record per container at a storage place and '
                     'position, with contents, storage class and source. Matterix can spawn the same containers; chemlab shows them '
                     '(chemlab list --inventory [ID]). Moving a container is a host or robot action.',
            'storage_classes': list(STORAGE_CLASSES),
            'places': [dict(p, slots=len(p['positions'])) for p in pls],
            'container_types': {t: (PROVISIONAL_TYPES.get(t) or (cat_types.get(t) or {}).get('name') or
                                    (cat_types.get(t) or {}).get('description') or '') for t in types},
            'container_types_source': 'assets/labware/catalog.json types (inventory_type_map); ids still provisional where the catalog '
                                      'has not built the item (type_status not_built)',
            'new_substances': new,
            'conventions': CONVENTIONS,
            'containers': recs,
            'instrument_contents': inside}


def height_m(r):
    """A container's height (m, with cap): the catalog type's size, the plate carrier's when it sits on one, else the typical
    height of a provisional type; None when unknown."""
    cat_types = _catalog().get('types') or {}
    h = None
    if r.get('on_carrier'):
        h = ((cat_types.get(r['on_carrier']) or {}).get('size_m') or [None] * 3)[2]
    t = cat_types.get(r['type']) or {}
    if t.get('size_m'):
        h = max(h or 0.0, t['size_m'][2])
    if h is None:
        h = PROVISIONAL_HEIGHTS_M.get(r.get('provisional_type') or r['type'])
    return h


def _bench_rule(inv):
    """Tier T1 of the bench shelf: both neighbours of every container at most BENCH_T1_NEIGHBOUR_MAX_M tall."""
    t1 = {r['location']['position']: r for r in inv['containers']
          if r['location']['place'] == 'bench_shelf' and str(r['location'].get('position', '')).startswith('T1-')}
    bad = []
    for pos, r in sorted(t1.items()):
        n = int(pos.split('-')[1])
        for m in (n - 1, n + 1):
            nb = t1.get('T1-%02d' % m)
            if nb is None:
                continue
            h = height_m(nb)
            if h is None or h > BENCH_T1_NEIGHBOUR_MAX_M:
                bad.append('bench_shelf %s: neighbour %s of %s is %s m tall (T1 neighbours at most %.3f m)' % (
                    nb['location']['position'], nb['id'], r['id'], h if h is not None else 'unknown', BENCH_T1_NEIGHBOUR_MAX_M))
    return sorted(set(bad))


def _bench_grasp(inv):
    """Everything on the bench shelf is for the robot: its type (or the carrier it sits on) must be graspable by the Franka hand
    (catalog franka_grasp_ok), unless the record says it is stock only ("stock_only")."""
    cat_types = _catalog().get('types') or {}
    bad = []
    for r in inv['containers']:
        if r['location']['place'] != 'bench_shelf' or r.get('stock_only'):
            continue
        t = cat_types.get(r.get('on_carrier') or r['type']) or {}
        if t.get('franka_grasp_ok') is False:
            bad.append('bench_shelf %s: %s (%s) is not graspable by the robot hand' % (r['location'].get('position'), r['id'],
                                                                                      r.get('on_carrier') or r['type']))
    return bad


def _content_rules(rid, contents, sub, strict=True):
    """R1 every substance id is in substances.json; R2 amounts in AMOUNT_UNITS only; R3 a solution's solutes have a mass / amount
    in mol (or a concentration) - a solid or dissolved species is never booked as a volume; a runtime material given as a volume
    names its definition (defined_by) or reference template."""
    bad = []
    for c in contents:
        am = c.get('amount')
        if am is not None and (not isinstance(am, dict) or am.get('unit') not in AMOUNT_UNITS or not isinstance(am.get('value'), (int, float))):
            bad.append('%s: amount %s is not a physical amount (%s)' % (rid, am, ', '.join(AMOUNT_UNITS)))
        if strict and am is None:
            bad.append('%s: content %s has no amount' % (rid, c.get('substance') or c.get('material')))
        sid = c.get('substance')
        if sid is not None:
            if sid not in sub:
                bad.append('%s: substance %s is not in substances.json' % (rid, sid))
                continue
            s = sub[sid]
            if am and am.get('unit') in VOLUME_UNITS and s.get('phase') != 'liquid' and s.get('kind') != 'mixture':
                bad.append('%s: %s (%s) booked as a volume: give the solute mass and its solvent' % (rid, sid, s.get('phase')))
        elif c.get('material') is not None:
            if strict:
                bad.append('%s: content %s is not a shared substance' % (rid, c['material']))
            elif am and am.get('unit') in VOLUME_UNITS and not (c.get('defined_by') or c.get('reference_template')):
                bad.append('%s: material %s given as a volume without its definition' % (rid, c['material']))
        else:
            bad.append('%s: content without substance or material' % rid)
    solvents = [c for c in contents if c.get('substance') in sub and (c.get('amount') or {}).get('unit') in VOLUME_UNITS]
    if strict and len(contents) > 1 and not solvents and not all((c.get('amount') or {}).get('unit') in ('g', 'kg', 'mg', 'mol', 'mmol')
                                                                   for c in contents):
        bad.append('%s: a multi-component content list needs masses or a solvent volume' % rid)
    return bad


def check(inv):
    """Problems of an inventory: unknown places / types, positions out of a place or used twice, storage class against the place,
    the bench shelf's T1 neighbour-height rule, robot-graspable bench items, and the content rules (_content_rules)."""
    places_ = {p['id']: p for p in inv['places']}
    cat_types = _catalog().get('types') or {}
    bad, used = [], {}
    for r in inv['containers']:
        loc = r['location']
        p = places_.get(loc['place'])
        if p is None:
            bad.append('%s: unknown place %s' % (r['id'], loc['place']))
            continue
        if p['id'] == 'instrument' and not loc.get('instrument'):
            bad.append('%s: at an instrument without naming it' % r['id'])
        if p['positions']:
            if loc.get('position') not in p['positions']:
                bad.append('%s: position %s not in %s' % (r['id'], loc.get('position'), p['id']))
            k = (p['id'], loc.get('position'))
            if k in used:
                bad.append('%s and %s share %s %s' % (used[k], r['id'], *k))
            used[k] = r['id']
        if r['type'] not in inv['container_types'] or (r['type'] not in PROVISIONAL_TYPES and cat_types and r['type'] not in cat_types):
            bad.append('%s: unknown type %s' % (r['id'], r['type']))
        if r['storage_class'] not in STORAGE_CLASSES:
            bad.append('%s: unknown storage class %s' % (r['id'], r['storage_class']))
        pc = p.get('storage_class')
        if pc in ('flammable', 'acid', 'base', 'oxidizer', 'toxic') and r['storage_class'] != pc:
            bad.append('%s (%s) in the %s place %s' % (r['id'], r['storage_class'], pc, p['id']))
        if r['storage_class'] in ('acid', 'base', 'oxidizer', 'toxic', 'compressed_gas') and pc not in (r['storage_class'], 'compressed_gas') \
                and p['id'] != 'instrument':
            bad.append('%s (%s) stored in %s' % (r['id'], r['storage_class'], p['id']))
        if r['storage_class'] == 'cold' and pc != 'cold' and p['id'] != 'instrument':
            bad.append('%s (cold) stored in %s' % (r['id'], p['id']))
    sub = _tables()[0]
    for r in inv['containers']:
        bad += _content_rules(r['id'], r.get('contents') or [], sub)
        for u in r.get('units') or []:
            if not isinstance(u.get('count'), int) or u['count'] < 1:
                bad.append('%s: unit %s has no count' % (r['id'], u.get('name')))
            bad += _content_rules('%s [%s]' % (r['id'], u.get('name')), u.get('contents') or [], sub)
        if r.get('per_unit') and not (isinstance(r.get('count'), int) and r['count'] >= 1):
            bad.append('%s: per_unit contents without a count' % r['id'])
    for r in inv.get('instrument_contents') or []:
        bad += _content_rules(r['id'], r.get('contents') or [], sub, strict=False)
    for r in inv['containers']:                            # cap: on / off, and only on a type with a closure
        if 'cap' in r:
            if r['cap'] not in ('on', 'off'):
                bad.append('%s: cap %r is not "on" or "off"' % (r['id'], r['cap']))
            elif not (cat_types.get(r['type']) or {}).get('closure'):
                bad.append('%s: cap given but its type %s has no closure' % (r['id'], r['type']))
    for r in inv['containers']:                            # rack rows the robot cannot use stay empty (vial rack row B)
        p = places_.get(r['location']['place']) or {}
        pos = str(r['location'].get('position') or '')
        if p.get('access') == 'top_down' and any(pos.startswith(x) for x in p.get('robot_forbidden_prefixes') or []):
            bad.append('%s: %s %s is a position the robot cannot use (keep it empty)' % (r['id'], p['id'], pos))
    for r in inv['containers']:                            # no "in reach" claim at a place the arm reaches only from the side
        p = places_.get(r['location']['place']) or {}
        text = ' '.join(str(r.get(k) or '') for k in ('source', 'description')).lower()
        if p.get('access') == 'side_only' and ('in reach' in text or 'within reach' in text):
            bad.append('%s: claims "in reach" at %s, a side-access-only place' % (r['id'], p['id']))
    return bad + _bench_rule(inv) + _bench_grasp(inv)


# ------------------------------------------------------------------ reading it (lab config "inventory")
def resolve(cfg):
    """The inventory of a lab config: config["inventory"] is the records inline (a dict) or a file name (configs/ or absolute)."""
    inv = cfg.get('inventory')
    if isinstance(inv, dict):
        return inv
    if isinstance(inv, str):
        p = Path(inv)
        cands = [p] if p.is_absolute() else ([Path(cfg['source']).parent / inv] if cfg.get('source') else []) + \
            [Path(__file__).resolve().parents[1] / 'configs' / inv]
        for c in cands:                                  # next to the config file, else configs/
            if c.exists():
                return _load_cached(str(c.resolve()), c.stat().st_mtime)
    return {'containers': [], 'places': []}


@lru_cache(maxsize=4)
def _load_cached(path, mtime):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def contents_text(r):
    if r.get('units'):
        return '; '.join('%s%s: %s' % ('%d x ' % u['count'] if u.get('count', 1) > 1 else '', u.get('name'),
                                       contents_text({'contents': u.get('contents')})) for u in r['units'])
    parts = []
    for c in r.get('contents') or []:
        what = c.get('substance') or c.get('material') or '?'
        am = c.get('amount') or {}
        s = what + (' %g %s' % (am['value'], am['unit']) if am.get('value') is not None else '')
        if c.get('grade'):
            s += ' (%s)' % c['grade']
        if c.get('certificate'):
            s += ' [certificate: %s]' % ', '.join('%s %s' % kv for kv in c['certificate'].items())
        if c.get('concentration'):
            s += ' [%g %s]' % (c['concentration']['value'], c['concentration']['unit'])
        if c.get('reference_template'):
            s += ' [reference %s]' % c['reference_template']
        parts.append(s)
    if not parts and r.get('description'):
        parts.append(r['description'])
    if r.get('count'):
        if r.get('per_unit') and parts:
            return '%d units, each: %s' % (r['count'], '; '.join(parts))
        parts.append('%d pieces' % r['count'])
    return '; '.join(parts) or 'empty'


def where_text(r):
    loc = r['location']
    if loc['place'] == 'instrument':
        return 'in %s%s' % (loc.get('instrument'), (' (%s)' % loc['position']) if loc.get('position') else '')
    s = '%s %s' % (loc['place'], loc.get('position') or '')
    if r.get('on_carrier'):
        s += ', on a plate carrier'
    if r.get('stored_in'):
        s += ', in its storage container'
    if r.get('connected_to'):
        s += ', piped to ' + ', '.join(r['connected_to'])
    return s.strip()
