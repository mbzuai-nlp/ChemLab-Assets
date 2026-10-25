"""Real appearance of the storage furniture: body / door colours from the makers' data sheets (RAL codes), the safety
labels the cabinets carry, and simple decals for the fridge / freezer controllers and the Secador hygrometer.

Visual only: geometry, collisions, frames and slots are unchanged.  Labels are decals (textured quads 0.6 mm in front of
the door face) added as extra visual elements of the door link.  Sign artwork: ISO 7010 safety signs and UNECE GHS
pictograms as rendered on Wikimedia Commons (public domain; per-file licence in assets/labware/_textures/signs/LICENCES.json),
composed onto a white label plate here.  Controller / dial decals are drawn here (project drawing, no maker artwork, no logos).
Colours: RAL Classic sRGB approximations from the Wikipedia 'List of RAL colours' (accessed 2026-10-23); the kit converts
them to linear for glTF / URDF and keeps sRGB in the MTL files.
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from labware_kit import TEXTURES_DIR, LabwareAsset, decal_quad, hex_rgba

SIGNS = TEXTURES_DIR / "signs"
LABELS = TEXTURES_DIR / "labels"
SIGN_LICENCES = json.loads((SIGNS / "LICENCES.json").read_text(encoding="utf-8"))["signs"]

RAL = {"RAL 7016": "#373F43", "RAL 7035": "#CBD0CC", "RAL 1004": "#E49E00", "RAL 9016": "#F7FBF5", "RAL 9010": "#F7F9EF", "RAL 5010": "#13447C"}
RAL_SOURCE = "RAL Classic sRGB approximations, Wikipedia 'List of RAL colours' (https://en.wikipedia.org/wiki/List_of_RAL_colours), accessed 2026-10-23"

SOURCES = {
    "asecos_q90_30001041": {"kind": "manufacturer", "reference": "asecos product data sheet ID 30001-041, model Q90.195.120 (https://www.asecos-configurator.com/EN/DE/asecos/download/DAT_EXT_30001041-product-data-sheet_IN.pdf)",
                            "locator": "p.1 colouring: body RAL 7016 anthracite grey, wing doors RAL 7035 light grey", "note": "Standard 3-shelf Q90.195.120 colours.", "licence": "facts"},
    "asecos_cs_30496040": {"kind": "manufacturer", "reference": "asecos product data sheet ID 30496-040, model CS.195.105.WDFW (https://www.asecos-configurator.com/EN/DE/asecos/download/DAT_EXT_30496040-product-data-sheet_IN.pdf)",
                           "locator": "p.1: body RAL 7016 anthracite grey, glass-paneled wing doors warning yellow RAL 1004; internal 996 x 493 x 1835 mm; 75 kg; depth with open doors 1026 mm",
                           "note": "Variant used for the toxics and oxidizers cabinets.", "licence": "facts"},
    "asecos_cs_30496041": {"kind": "manufacturer", "reference": "asecos product data sheet ID 30496-041, model CS.195.105.WDFW (https://www.asecos-configurator.com/EN/DE/asecos/download/DAT_EXT_30496041-product-data-sheet_IN.pdf)",
                           "locator": "p.1: body RAL 7016 anthracite grey, glass-paneled wing doors light grey RAL 7035; 75 kg", "note": "Variant used for the standards and consumables cabinets.", "licence": "facts"},
    "asecos_g90_30641001": {"kind": "manufacturer", "reference": "asecos product data sheet ID 30641-001, model G90.205.140 (https://www.asecos-configurator.com/EN/DE/asecos/download/DAT_EXT_30641001-product-data-sheet_IN.pdf)",
                            "locator": "p.1: body and doors RAL 7035 light grey; 690 kg without interior; 2 doors, door opening angle 180 deg; depth with open doors 1337 mm",
                            "note": "Gas cylinder cabinet colours, mass and door angle.", "licence": "facts"},
    "asecos_sl_30606005": {"kind": "manufacturer", "reference": "asecos product data sheet ID 30606-005, model SL.196.120.MV",
                           "locator": "p.1: body and doors laboratory white (sim. RAL 9016)", "note": "Acid/base cabinet colour.", "licence": "facts"},
    "liebherr_white": {"kind": "manufacturer", "reference": "Liebherr LKv 3910 MediLine listings (https://remma.fr/en/model/laboratory-refrigerator-mediline-lkv-3910; machineseeker listing)",
                       "locator": "exterior white painted steel, PS interior; electronic controller with digital display (+3...+16 C in 0.1 C steps)",
                       "note": "White finish (no RAL code published; RAL 9016 sRGB used for display) and controller with digital display; the LGUex 1500 of the same MediLine series is taken as the same white.", "licence": "facts"},
    "trippnt_white": {"kind": "manufacturer", "reference": "TrippNT 24 in step shelf 50123, white PVC (VWR 'Step shelves, white PVC' https://b2b-uk.vwr.com/store/product/39705954/null; Avantor 16117910 'Step Shelf, White PVC, TrippNT')",
                      "locator": "material: white PVC", "note": "White (RAL not published; RAL 9010 sRGB used for display).", "licence": "facts"},
    "denios_blue": {"kind": "manufacturer", "reference": "DENIOS 115873W listing (blue powder-coated steel)", "locator": "finish: blue powder coating",
                    "note": "Blue (RAL not published; RAL 5010 gentian blue sRGB used for display).", "licence": "facts"},
    "secador_hygrometer": {"kind": "manufacturer", "reference": "SP Bel-Art Secador vertical desiccator cabinets product page (https://belart.com/products/dry-storage/standard-desiccators/secador-1-0-2-0-3-0-4-0-vertical-desiccator-cabinets/)",
                           "locator": "hygrometer dial in the door shows relative humidity", "note": "Hygrometer dial decal (drawn here).", "licence": "facts"},
    "public_domain_signs": {"kind": "publication", "reference": "ISO 7010 safety signs and UNECE GHS pictograms as SVG files on Wikimedia Commons (licence per file: public domain), list in assets/labware/_textures/signs/LICENCES.json",
                            "locator": "W021 flammable, W023 corrosive, W016 toxic, W028 oxidizing, W029 gas cylinder, P003 no open flame; GHS02/03/04/05/06",
                            "note": "Safety labels on the cabinet doors (EN 14470-1/-2 and the hazard classes stored).", "licence": "public domain"},
    "ral_srgb": {"kind": "publication", "reference": RAL_SOURCE, "locator": "RAL 1004 #E49E00, 5010 #13447C, 7016 #373F43, 7035 #CBD0CC, 9010 #F7F9EF, 9016 #F7FBF5",
                 "note": "Display colours for the RAL codes.", "licence": "facts"},
}


# ------------------------------------------------------------------------------------------------ textures
def label(sign: str, plate_px=(600, 600)) -> Path:
    """White label plate with a thin grey border and the sign centred (no alpha: safe for OBJ/URDF importers)."""
    LABELS.mkdir(parents=True, exist_ok=True)
    out = LABELS / f"label_{sign}.png"
    if out.exists():
        return out
    W, H = plate_px
    img = Image.new("RGB", (W, H), (250, 250, 248))
    d = ImageDraw.Draw(img)
    d.rectangle([4, 4, W - 5, H - 5], outline=(150, 150, 150), width=6)
    s = Image.open(SIGNS / f"{sign}.png").convert("RGBA")
    k = min((W - 80) / s.width, (H - 80) / s.height)
    s = s.resize((int(s.width * k), int(s.height * k)), Image.LANCZOS)
    img.paste(s, ((W - s.width) // 2, (H - s.height) // 2), s)
    img.save(out)
    return out


def _font(px):
    try:
        return ImageFont.load_default(size=px)
    except TypeError:
        return ImageFont.load_default()


def controller_panel(name: str, text: str, size_px=(1200, 160)) -> Path:
    """Drawn controller strip: dark panel, backlit display with the set-point text, four round keys, status LED."""
    LABELS.mkdir(parents=True, exist_ok=True)
    out = LABELS / f"panel_{name}.png"
    W, H = size_px
    img = Image.new("RGB", (W, H), (58, 62, 66))
    d = ImageDraw.Draw(img)
    dx0, dy0, dx1, dy1 = int(W * 0.38), int(H * 0.18), int(W * 0.62), int(H * 0.82)
    d.rectangle([dx0, dy0, dx1, dy1], fill=(20, 30, 36), outline=(110, 110, 110), width=3)
    d.text(((dx0 + dx1) // 2, (dy0 + dy1) // 2), text, fill=(120, 230, 255), font=_font(int(H * 0.42)), anchor="mm")
    for i in range(4):
        cx = int(W * 0.68) + i * int(H * 0.55)
        d.ellipse([cx - H * 0.18, H * 0.32, cx + H * 0.18, H * 0.68], fill=(200, 202, 205), outline=(30, 30, 30), width=2)
    d.ellipse([W * 0.30, H * 0.42, W * 0.30 + H * 0.16, H * 0.58], fill=(60, 200, 90))
    img.save(out)
    return out


def hygrometer_dial() -> Path:
    LABELS.mkdir(parents=True, exist_ok=True)
    out = LABELS / "dial_hygrometer.png"
    S = 400
    img = Image.new("RGB", (S, S), (235, 235, 232))
    d = ImageDraw.Draw(img)
    d.ellipse([10, 10, S - 10, S - 10], fill=(250, 250, 248), outline=(40, 40, 40), width=8)
    import math
    for i in range(11):
        a = math.radians(225 - 27 * i)
        r0, r1 = S * 0.36, S * 0.44
        d.line([S / 2 + r0 * math.cos(a), S / 2 - r0 * math.sin(a), S / 2 + r1 * math.cos(a), S / 2 - r1 * math.sin(a)], fill=(30, 30, 30), width=5)
    a = math.radians(225 - 27 * 2.5)
    d.line([S / 2, S / 2, S / 2 + S * 0.33 * math.cos(a), S / 2 - S * 0.33 * math.sin(a)], fill=(200, 30, 30), width=7)
    d.text((S / 2, S * 0.70), "% RH", fill=(30, 30, 30), font=_font(44), anchor="mm")
    img.save(out)
    return out


# ------------------------------------------------------------------------------------------------ application
def set_colour(a: LabwareAsset, link_id: str, ral: str, source_id: str, element: int = 0, alpha: float = 1.0, metallic: float = 0.15):
    l = a.link(link_id)
    el = list(l.visual[element])
    opts = dict(el[2]) if len(el) > 2 and el[2] else {}
    opts.update({"ral": ral, "source_id": source_id, "metallic": metallic, "roughness": 0.55})
    l.visual[element] = (el[0], hex_rgba(RAL[ral], alpha), opts)


def door_decal(a: LabwareAsset, door: str, png: Path, z_world: float, size, x_frac: float = 0.5, name: str = "", licence: str = ""):
    """Decal on the front face of a door leaf; x_frac 0 = hinge edge, 1 = free edge; z_world = centre height above the floor."""
    dd = a.labware["doors"][door]
    w, t, h = dd["leaf_size_m"]
    sgn = 1.0 if dd["hinge_side"] == "left" else -1.0
    z0 = dd["hinge_origin_m"][2]
    mesh, uv = decal_quad((sgn * w * x_frac, -t, z_world - z0), size[0], size[1], "-y")
    a.link(door).visual.append((mesh, (1.0, 1.0, 1.0, 1.0), {"texture": str(png), "uv": uv, "name": f"decal_{name or png.stem}",
                                                              "texture_licence": licence, "source_id": "public_domain_signs" if "label_" in png.name else "project_drawing"}))


def glass_panel(a: LabwareAsset, door: str, z0_world: float, z1_world: float, width: float):
    """WDFW glass panel: a tinted translucent pane 1 mm proud of the door face (visual only)."""
    import trimesh
    dd = a.labware["doors"][door]
    w, t, h = dd["leaf_size_m"]
    sgn = 1.0 if dd["hinge_side"] == "left" else -1.0
    zc = (z0_world + z1_world) / 2 - dd["hinge_origin_m"][2]
    pane = trimesh.creation.box(extents=[width, 0.001, z1_world - z0_world])
    pane.apply_translation([sgn * w * 0.5, -t - 0.0007, zc])
    a.link(door).visual.append((pane, (0.55, 0.65, 0.72, 0.55), {"name": "glass_panel", "note_zh": "门上玻璃观察窗（WDFW 型）", "source_id": "asecos_cs"}))


def _lic(sign: str) -> str:
    rec = SIGN_LICENCES[sign]
    return f"{rec['licence']} ({rec['source_page']})"


def _record(a: LabwareAsset, body: dict, labels: list, extra_sources: list):
    a.sources.update({k: SOURCES[k] for k in extra_sources + ["ral_srgb"]})
    a.labware["appearance"] = {"body": body, "labels": labels, "colour_source": RAL_SOURCE,
                               "note_zh": "外观（颜色、标签、面板贴花）只影响显示；几何、碰撞、坐标与存放位不变。"}


def hazard_doors(a: LabwareAsset, doors: list, signs: list, ghs: str | None):
    labels = []
    for door in doors:
        for k, sign in enumerate(signs):
            png = label(sign)
            door_decal(a, door, png, 1.55 - 0.22 * k, (0.17, 0.17), x_frac=0.5, name=sign, licence=_lic(sign))
            labels.append({"door": door, "sign": sign, "licence": SIGN_LICENCES[sign]["licence"], "source_page": SIGN_LICENCES[sign]["source_page"]})
        if ghs:
            png = label(ghs)
            door_decal(a, door, png, 1.55 - 0.22 * len(signs), (0.13, 0.13), x_frac=0.5, name=ghs, licence=_lic(ghs))
            labels.append({"door": door, "sign": ghs, "licence": SIGN_LICENCES[ghs]["licence"], "source_page": SIGN_LICENCES[ghs]["source_page"]})
    return labels


def apply(a: LabwareAsset) -> LabwareAsset:
    slug = a.slug
    if slug == "asecos_q90_195_120":
        set_colour(a, "carcass", "RAL 7016", "asecos_q90_30001041")
        for door in ("door_left", "door_right"):
            set_colour(a, door, "RAL 7035", "asecos_q90_30001041")
        labels = hazard_doors(a, ["door_left", "door_right"], ["iso7010_w021_flammable"], "ghs02_flame")
        png = label("iso7010_p003_no_open_flame")
        door_decal(a, "door_left", png, 1.11, (0.13, 0.13), x_frac=0.5, name="iso7010_p003_no_open_flame", licence=_lic("iso7010_p003_no_open_flame"))
        labels.append({"door": "door_left", "sign": "iso7010_p003_no_open_flame", "licence": "Public domain"})
        _record(a, {"body": "RAL 7016 anthracite grey", "doors": "RAL 7035 light grey", "source_id": "asecos_q90_30001041"}, labels,
                ["asecos_q90_30001041", "public_domain_signs"])
    elif slug == "asecos_sl_196_120":
        set_colour(a, "carcass", "RAL 9016", "asecos_sl_30606005")
        for door in ("door_left", "door_right"):
            set_colour(a, door, "RAL 9016", "asecos_sl_30606005")
        labels = hazard_doors(a, ["door_left", "door_right"], ["iso7010_w023_corrosive"], "ghs05_corrosion")
        _record(a, {"body": "laboratory white (sim. RAL 9016)", "doors": "laboratory white (sim. RAL 9016)", "source_id": "asecos_sl_30606005"}, labels,
                ["asecos_sl_30606005", "public_domain_signs"])
    elif slug.startswith("asecos_cs_195_105"):
        hazardous = slug in ("asecos_cs_195_105", "asecos_cs_195_105_oxidizers")
        src = "asecos_cs_30496040" if hazardous else "asecos_cs_30496041"
        set_colour(a, "carcass", "RAL 7016", src)
        for door in ("door_left", "door_right"):
            set_colour(a, door, "RAL 1004" if hazardous else "RAL 7035", src)
            glass_panel(a, door, 0.45, 1.20, 0.26)
        labels = []
        if slug == "asecos_cs_195_105":
            labels = hazard_doors(a, ["door_left", "door_right"], ["iso7010_w016_toxic"], "ghs06_skull")
        elif slug == "asecos_cs_195_105_oxidizers":
            labels = hazard_doors(a, ["door_left", "door_right"], ["iso7010_w028_oxidizing"], "ghs03_flame_over_circle")
        _record(a, {"body": "RAL 7016 anthracite grey", "doors": "RAL 1004 warning yellow" if hazardous else "RAL 7035 light grey",
                    "glass_panels": "WDFW glass-paneled wing doors", "source_id": src}, labels, [src] + (["public_domain_signs"] if labels else []))
        for l in a.links:
            for el in l.visual:
                if len(el) > 2 and el[2].get("source_id") == "asecos_cs":
                    el[2]["source_id"] = src
    elif slug == "asecos_g90_205_140":
        set_colour(a, "carcass", "RAL 7035", "asecos_g90_30641001")
        for door in ("door_left", "door_right"):
            set_colour(a, door, "RAL 7035", "asecos_g90_30641001")
        labels = hazard_doors(a, ["door_left", "door_right"], ["iso7010_w029_gas_cylinder", "iso7010_w021_flammable"], "ghs04_gas_cylinder")
        _record(a, {"body": "RAL 7035 light grey", "doors": "RAL 7035 light grey", "source_id": "asecos_g90_30641001"}, labels,
                ["asecos_g90_30641001", "public_domain_signs"])
    elif slug in ("liebherr_lkv3910", "liebherr_lguex1500"):
        set_colour(a, "carcass", "RAL 9016", "liebherr_white", metallic=0.1)
        set_colour(a, "door", "RAL 9016", "liebherr_white", metallic=0.1)
        fridge = slug == "liebherr_lkv3910"
        png = controller_panel(slug, "+5.0°C" if fridge else "-20.0°C")
        dd = a.labware["doors"]["door"]
        top = dd["hinge_origin_m"][2] + dd["leaf_size_m"][2]
        door_decal(a, "door", png, top - 0.05, (dd["leaf_size_m"][0] * 0.92, 0.07), x_frac=0.5, name="controller_panel", licence="project drawing (no maker artwork)")
        _record(a, {"body": "white painted steel", "doors": "white", "controller": "electronic controller with digital display (decal)", "source_id": "liebherr_white"},
                [{"door": "door", "decal": "controller_panel", "licence": "project drawing"}], ["liebherr_white"])
    elif slug == "belart_secador_2_vertical":
        png = hygrometer_dial()
        door_decal(a, "door", png, 0.30, (0.06, 0.06), x_frac=0.5, name="hygrometer", licence="project drawing (no maker artwork)")
        _record(a, {"body": "clear (F42072-1000)", "doors": "clear", "hygrometer": "dial in the door (decal)", "source_id": "secador_hygrometer"},
                [{"door": "door", "decal": "hygrometer", "licence": "project drawing"}], ["secador_hygrometer"])
    elif slug == "trippnt_step_shelf_2tier":
        set_colour(a, "shelf", "RAL 9010", "trippnt_white", metallic=0.0)
        _record(a, {"body": "white PVC", "source_id": "trippnt_white"}, [], ["trippnt_white"])
    elif slug == "denios_cylinder_stand_x4":
        set_colour(a, "stands", "RAL 5010", "denios_blue", metallic=0.2)
        _record(a, {"body": "blue powder coating", "source_id": "denios_blue"}, [], ["denios_blue"])
    return a
