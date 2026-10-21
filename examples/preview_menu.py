"""Step through an instrument's front-panel menu and save the screen after every step.

    python examples/preview_menu.py aqualab_4te btn3=MENU down btn2=ENTER up btn2=SAVE
    python examples/preview_menu.py ika_plate_rctdigital menu_key turn:knob_speed:1 speed_push_key menu_key
    python examples/preview_menu.py julabo_corio_cd200f --list
    python examples/preview_menu.py mt_xpr204e tap:Methods tap:Tests tap:Close --out preview_xpr

The instrument starts at its home screen (power on, world set up). Each step is one gesture, sent through the
agent session of the instrument's runtime with the contract's panel capabilities:

    KEY  or  press:KEY[:SECONDS]           panel.press    a key, or a knob's push key (SECONDS >= hold time = long press)
    turn:KNOB:DETENTS                       panel.turn     an encoder or knob, signed detents (+ = clockwise)
    switch:SWITCH:POSITION                  panel.switch   a switch, e.g. switch:power_switch:off
    touch:DISPLAY:X,Y[:SECONDS]             panel.touch    a touch screen, pixel coordinates of that display
    touch:DISPLAY:X,Y>X2,Y2                 panel.touch    drag from (X, Y) to (X2, Y2)
    tap:LABEL                               panel.touch    touch the centre of the button (or list row) labelled LABEL
    wait:SECONDS                            -              let simulated time pass (timeouts, warm-up, blinking)
    STEP=CAPTION                                           any step with its own caption in strip.png, e.g. btn3=MENU

After each step the screen is captured with panel.capture (what a camera on the display would see) and written as
<out>/NN_<step>.png; <out>/strip.png puts all frames side by side with the step under each. --list prints the
inputs, displays and, for touch screens, the labelled buttons on the current page.

tap:LABEL looks the button up in the structured screen (panel.frame), which is an evaluator-only view; an agent
policy that should rely on pixels only uses touch:DISPLAY:X,Y instead. Everything else uses the agent view.
The script finds assets/ and scripts/ next to its own folder, so it runs from anywhere. Needs numpy and Pillow.
"""
import argparse
import inspect
import importlib
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
TABLE = json.load(open(Path(__file__).resolve().parent / "panel_instruments.json", encoding="utf-8"))["instruments"]
PRESSABLE = ("button", "key", "tab", "tile", "toggle", "checkbox", "radio", "icon_button", "softkey")


class Instrument:
    """One runtime host with an agent session (gestures, screens) and an evaluator session (world set-up)."""

    def __init__(self, slug, instance_id=None):
        self.slug, self.cfg = slug, TABLE[slug]
        self.iid = instance_id or f"{slug}_01"
        self.t = 0.0
        self._n = 0
        h = self.cfg["host"]
        cls = getattr(importlib.import_module(h["module"]), h["cls"])
        params = inspect.signature(cls.__init__).parameters
        kw = dict(h.get("ctor") or {})
        if "scene_adapter" in params and h.get("ctor_scene_adapter", True):
            kw["scene_adapter"] = lambda instance, updates: None      # no 3D scene here
        if h.get("advance") is None and "clock" in params:
            kw["clock"] = lambda: self.t                               # runtimes that read a clock callable
        if h.get("instance_kw"):
            kw[h["instance_kw"]] = self.iid
            self.host = cls(**kw)
        else:
            self.host = cls(self.iid, **kw) if h.get("instance_in_ctor") else cls(**kw)
        create = h.get("create")
        if create == "type":
            self.host.create_instance(self.iid, self.cfg["asset_type_id"])
        elif create == "plain":
            self.host.create_instance(self.iid)
        elif create == "scene":
            self.host.create_instance(self.iid, lambda instance, updates: None)
        if h.get("host_is_session"):
            self.agent = self.lab = self.host
        else:
            self.agent = self._session("preview_agent", "agent", h)
            self.lab = self._session("preview_lab", "evaluator", h)
        self.contract = json.load(open(ROOT / self.cfg["contract"], encoding="utf-8"))
        self.spec = json.load(open(ROOT / self.cfg["panel_spec"], encoding="utf-8"))

    def _session(self, name, visibility, h):
        if visibility == "agent" and h.get("agent_session_default"):
            return self.host.session()
        if visibility == "evaluator" and h.get("evaluator_register_positional"):
            self.host.register_principal(name)
        else:
            try:
                self.host.register_principal(name, visibility=visibility)
            except TypeError:
                self.host.register_principal(name, visibility)
        return self.host.session(name)

    def call(self, capability, arguments=None, evaluator=False):
        self._n += 1
        s = self.lab if evaluator else self.agent
        return s.invoke(instance_id=self.iid, capability_id=capability, arguments=dict(arguments or {}),
                        request_id=f"preview-{self._n}")

    def advance(self, seconds, dt=1.0):
        """Advance the simulated clock in steps of at most dt seconds, with the runtime's own clock call."""
        mode, h = self.cfg["host"].get("advance"), self.host
        left = float(seconds)
        while left > 1e-9:
            d = min(dt, left)
            t_new = self.t + d
            if mode == "step_dt":
                h.step(d)
            elif mode == "host_step_dt":
                h.host_step(d)
            elif mode == "host_step_dt_world":
                h.host_step(d, world_time_s=t_new)
            elif mode == "step_dt_world":
                h.step(d, world_time_s=t_new)
            elif mode == "host_step_abs":
                h.host_step(t_new)
            elif mode == "host_advance_abs":
                h.host_advance(t_new)
            elif mode == "host_set_clock_abs":
                h.host_set_clock(t_new)
            elif mode == "vici_set_time":
                h.host_set_time(world_time_s=t_new, request_id=f"preview-clock-{self._n}-{t_new:.3f}")
            self.t = t_new                                             # mode None: the runtime reads self.t
            left -= d

    def bring_up(self):
        caps = self.contract.get("capabilities") or {}
        for st in self.cfg["bringup"]:
            if "advance_s" in st:
                self.advance(st["advance_s"])
                continue
            args = st.get("arguments")
            if args == "contract_example":
                args = (caps.get(st["capability"]) or {}).get("example_arguments") or {}
            r = self.call(st["capability"], args, evaluator=bool(st.get("evaluator")))
            if r.get("status") not in ("succeeded", "accepted", "running"):
                print(f"  note: bring-up step {st['capability']} -> {r.get('status')} {(r.get('error') or {}).get('code')}")

    def capture(self, display, dst):
        """panel.capture of one display into dst; display 'all' puts every display side by side (LED-only panels)."""
        if display != "all":
            shutil.copyfile(self._capture_one(display), dst)
            return dst
        from PIL import Image, ImageDraw
        ims = [(d, Image.open(self._capture_one(d)).convert("RGB")) for d in self.spec["displays"]]
        h = max(im.height for _, im in ims)
        k = max(1, 96 // h)
        ims = [(d, im.resize((im.width * k, im.height * k), Image.NEAREST)) for d, im in ims]
        w = max(max(im.width for _, im in ims), 72)
        out = Image.new("RGB", (len(ims) * (w + 8) + 8, h * k + 30), (40, 44, 50))
        dr = ImageDraw.Draw(out)
        for i, (d, im) in enumerate(ims):
            x = 8 + i * (w + 8) + (w - im.width) // 2
            out.paste(im, (x, 6))
            dr.text((8 + i * (w + 8), h * k + 12), d.removeprefix("led_")[:12], fill=(220, 224, 230))
        out.save(dst)
        return dst

    def _capture_one(self, display):
        r = self.call("panel.capture", {"display_id": display})
        if r.get("status") != "succeeded":
            raise SystemExit(f"panel.capture {display}: {r.get('status')} {r.get('error')}")
        return r["result"]["image"]["path"]

    def frame(self):
        r = self.call("panel.frame", {}, evaluator=True)
        return (r.get("result") or {}).get("frame") or {}

    def schema(self, capability):
        d = self.agent.describe(instance_id=self.iid)["capabilities"]
        caps = d if isinstance(d, dict) else {c.get("capability_id"): c for c in d}
        return ((caps.get(capability) or {}).get("input_schema") or {}).get("properties") or {}


# ------------------------------------------------------------------------------------------------------------ steps
def touch_targets(frame, display):
    """Labelled, enabled buttons and list rows on the current page of a touch display: [(label, x, y)]."""
    out = []
    d = (frame.get("displays") or {}).get(display) or {}
    for w in d.get("widgets") or []:
        if not w.get("enabled", True) or not w.get("rect"):
            continue
        x, y, ww, hh = w["rect"]
        if w.get("type") in PRESSABLE and str(w.get("text") or "").strip():
            out.append((str(w["text"]).strip(), x + ww // 2, y + hh // 2))
        rh = w.get("row_h")
        for i, row in enumerate(w.get("rows") or []):
            if rh and isinstance(row, str) and row.strip():
                cy = y + (i - int(w.get("scroll") or 0)) * rh + rh // 2
                if y <= cy < y + hh:
                    label = (w.get("cells") or [[row]])[i][0] if w.get("cells") else row
                    out.append((str(label).strip(), x + ww // 2, cy))
    return out


def parse_step(text, inst):
    """'press:menu_key:2' -> (capability, arguments, label); ('wait', seconds, label) for wait steps."""
    parts = text.split(":")
    verb = parts[0].lower()
    if verb not in ("press", "turn", "switch", "touch", "tap", "wait"):
        verb, parts = "press", ["press"] + parts
    if verb == "wait":
        return "wait", float(parts[1]), f"wait {parts[1]} s"
    if verb == "press":
        args = {"input_id": parts[1]}
        if len(parts) > 2:
            args["duration_s"] = float(parts[2])
        return "panel.press", args, ("hold " if len(parts) > 2 and float(parts[2]) >= 1 else "") + parts[1]
    if verb == "turn":
        n = int(parts[2])
        return "panel.turn", {"input_id": parts[1], "detents": n}, f"turn {parts[1]} {n:+d}"
    if verb == "switch":
        return "panel.switch", {"input_id": parts[1], "position": parts[2]}, f"{parts[1]} {parts[2]}"
    if verb == "touch":
        m = re.fullmatch(r"(\d+),(\d+)(?:>(\d+),(\d+))?", parts[2])
        if not m:
            raise SystemExit(f"touch needs X,Y or X,Y>X2,Y2: {text}")
        args = {"display_id": parts[1], "x": int(m.group(1)), "y": int(m.group(2))}
        if m.group(3):
            args.update(to_x=int(m.group(3)), to_y=int(m.group(4)))
        if len(parts) > 3:
            args["duration_s"] = float(parts[3])
        return "panel.touch", args, f"touch {m.group(0)}"
    # tap:LABEL
    label = text.split(":", 1)[1]
    disp = [k for k, v in inst.spec["displays"].items() if v["kind"] == "touch_tft"]
    frame = inst.frame()
    for d in disp:
        hits = [t for t in touch_targets(frame, d) if t[0].lower() == label.lower()] or \
               [t for t in touch_targets(frame, d) if t[0].lower().startswith(label.lower())]
        if hits:
            _, x, y = hits[0]
            return "panel.touch", {"display_id": d, "x": int(x), "y": int(y)}, f"tap {label}"
    raise SystemExit(f"no button labelled {label!r} on the current page (see --list)")


def safe(s):
    return re.sub(r"[^A-Za-z0-9_.+-]+", "_", s).strip("_")[:40] or "step"


def strip_image(frames, path, height=None, cols=None):
    """Frames in reading order (one row, or wrapped after cols frames), same height (pixel-exact scaling for small displays),
    the step and whether the instrument accepted it written under each frame."""
    from PIL import Image, ImageDraw, ImageFont
    ims = [Image.open(f).convert("RGB") for f, _, _ in frames]
    h = height or max(240, min(480, max(im.height for im in ims)))
    scaled = []
    for im in ims:
        k = h / im.height
        res = Image.NEAREST if k >= 2 else Image.LANCZOS
        scaled.append(im.resize((max(1, round(im.width * k)), h), res))
    try:
        font = ImageFont.load_default(size=max(16, h // 11))
    except TypeError:
        font = ImageFont.load_default()
    pad, cap = 14, max(34, h // 7)
    w = max(im.width for im in scaled)
    if not cols:                                   # as many as fit in 2400 px, rows balanced (6 frames -> 3 + 3)
        fit = max(1, min(len(scaled), 2400 // (w + pad)))
        cols = -(-len(scaled) // -(-len(scaled) // fit))
    rows = -(-len(scaled) // cols)
    out = Image.new("RGB", (cols * (w + pad) + pad, rows * (h + cap + pad) + pad), (246, 247, 249))
    dr = ImageDraw.Draw(out)
    for i, (im, (_, label, ok)) in enumerate(zip(scaled, frames)):
        x, y = pad + (i % cols) * (w + pad), pad + (i // cols) * (h + cap + pad)
        out.paste(im, (x, y))
        dr.rectangle([x - 1, y - 1, x + im.width, y + h], outline=(200, 204, 210))
        txt = f"{i}. {label}" if i else label
        if ok is False:
            txt += "  (ignored)"
        dr.text((x, y + h + 8), txt, fill=(21, 32, 42) if ok is not False else (160, 60, 40), font=font)
    out.save(path)
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("instrument", help="slug, e.g. ika_plate_rctdigital (examples/panel_instruments.json lists all)")
    ap.add_argument("steps", nargs="*", help="gestures, see above")
    ap.add_argument("--out", help="output folder (default: menu_preview/<slug>)")
    ap.add_argument("--display", help="display to capture, or 'all' (default: the main screen; 'all' on LED-only panels)")
    ap.add_argument("--settle", type=float, default=1.0,
                    help="simulated seconds after each gesture (default 1.0, which also keeps blinking fields in their visible phase)")
    ap.add_argument("--cols", type=int, help="frames per row in strip.png (default: as many as fit in 2400 px)")
    ap.add_argument("--list", action="store_true", help="print inputs, displays and touch targets, then stop")
    ap.add_argument("--no-bringup", action="store_true", help="start powered off, without the home-screen set-up")
    args = ap.parse_args()
    slug = args.instrument.split(".", 1)[-1]
    if slug not in TABLE:
        raise SystemExit(f"{slug}: no front-panel menu in this release (see examples/panel_instruments.json)")
    inst = Instrument(slug)
    if not args.no_bringup:
        inst.bring_up()
    display = args.display or inst.cfg["main_display"]

    if args.list:
        print(f"{slug}: {inst.cfg['panel_ui'].replace('_', ' ')} panel; capabilities {', '.join(inst.cfg['panel_capabilities'])}")
        for cap in ("panel.press", "panel.turn"):
            p = inst.schema(cap)
            if p:
                names = [f"{k} ({inst.spec['inputs'][k]['label']})" if inst.spec["inputs"].get(k, {}).get("label") not in (None, k) else k
                         for k in p["input_id"]["enum"]]
                print(f"  {cap:13s} {', '.join(names)}")
        for k in inst.schema("panel.switch").get("input_id", {}).get("enum", []):
            print(f"  panel.switch  {k}: {' / '.join(inst.spec['inputs'][k].get('positions', []))}")
        for did, d in inst.spec["displays"].items():
            size = "x".join(str(v) for v in d.get("size", [])) or "-"
            print(f"  display {did:16s} {d['kind']:12s} {size}{'   (main)' if did == display or display == 'all' else ''}")
        fr = inst.frame()
        print(f"  current screen: {fr.get('screen')}")
        for did, d in inst.spec["displays"].items():
            if d["kind"] == "touch_tft":
                for label, x, y in touch_targets(fr, did):
                    print(f"    tap:{label:24s} = touch:{did}:{x},{y}")
        return

    out = Path(args.out or f"menu_preview/{slug}")
    out.mkdir(parents=True, exist_ok=True)
    for old in list(out.glob("[0-9][0-9]_*.png")) + [out / "strip.png"]:   # frames of an earlier run in the same folder
        old.unlink(missing_ok=True)
    frames = [(inst.capture(display, out / "00_start.png"), "start", None)]
    for i, text in enumerate(args.steps, 1):
        text, _, caption = text.partition("=")
        cap, a, label = parse_step(text, inst)
        label = caption or label
        ok = None
        if cap == "wait":
            inst.advance(a)
        else:
            r = inst.call(cap, a)
            if r.get("status") != "succeeded":
                raise SystemExit(f"step {i} {text}: {r.get('status')} {(r.get('error') or {}).get('code')} "
                                 f"{(r.get('error') or {}).get('message')}")
            ok = bool((r.get("result") or {}).get("accepted"))
            inst.advance(args.settle)
        f = inst.capture(display, out / f"{i:02d}_{safe(label)}.png")
        frames.append((f, label, ok))
        print(f"{i:2d}. {label:28s} {'' if ok is None else ('accepted' if ok else 'ignored by the instrument')}  -> {f.name}")
    strip_image(frames, out / "strip.png", cols=args.cols)
    print("strip:", out / "strip.png")


if __name__ == "__main__":
    main()
