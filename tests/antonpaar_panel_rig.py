"""Test rig of the Anton Paar touch panels (front-panel group G07): a runtime with an agent and an evaluator session; gestures only through the
panel.* capabilities of the session API; widgets found by id or text in the structured frame (panel.frame, evaluator) and tapped at their
centre; typing on the engine's on-screen keyboards.  Used by tests/test_panel_ui_antonpaar_*.py (not a test module itself)."""
import copy
from pathlib import Path


class Rig:
    def __init__(self, rt, runtime_class, display='screen', power=None):
        self.rt = rt
        self.h = getattr(rt, runtime_class)()
        self.h.register_principal('agent', visibility='agent')
        self.h.register_principal('judge', visibility='evaluator')
        self.A, self.E = self.h.session('agent'), self.h.session('judge')
        self.d = display
        self.n = 0
        if power:
            self.call(self.A, 'panel.switch', input_id=power[0], position=power[1])

    # ---- sessions
    def call(self, sess, cap, expect='ok', **args):
        self.n += 1
        t0 = self.h.time_s
        r = sess.invoke(instance_id=self.h.instance_id, capability_id=cap, arguments=args, request_id=f'q{self.n}')
        if expect == 'ok':
            assert r['status'] in ('succeeded', 'accepted'), (cap, args, r['status'], r['error'])
        else:
            assert r['status'] == 'rejected' and r['error']['code'] == expect, (cap, args, r['status'], r['error'])
        assert self.h.time_s == t0, cap                                          # commands and gestures take no simulated time
        return r['result'] if expect == 'ok' else r

    def val(self, sid):
        return self.E.read(instance_id=self.h.instance_id, state_ids=[sid])['observations'][sid]['value']

    def step(self, seconds):
        t = 0.0
        while t < seconds - 1e-9:
            d = min(10.0, seconds - t)
            self.h.step(d)
            t += d

    # ---- screen
    def frame(self):
        return self.call(self.E, 'panel.frame')['frame']

    def disp(self):
        return self.frame()['displays'][self.d]

    def widgets(self):
        return {w['id']: w for w in self.disp()['widgets']}

    def text(self, wid):
        return self.widgets()[wid]['text']

    def page(self):
        f = self.frame()
        return f['screen'], f['ui']['page']

    def touch(self, x, y, **kw):
        return self.call(self.A, 'panel.touch', display_id=self.d, x=int(x), y=int(y), **kw)['accepted']

    def tap(self, wid):
        w = self.widgets()[wid]
        x, y, ww, hh = w['rect']
        return self.touch(x + ww // 2, y + hh // 2)

    def tap_text(self, text):
        """Tap the (first) button whose text is this (two-line labels compared with a blank)."""
        ws = self.disp()['widgets']
        for w in ws:
            if w['type'] == 'button' and w['text'].replace('\n', ' ') == text:
                x, y, ww, hh = w['rect']
                return self.touch(x + ww // 2, y + hh // 2)
        raise AssertionError(f'{text!r} not on {self.page()}: {[w["text"] for w in ws if w["type"] == "button"]}')

    def has_button(self, text):
        return any(w['type'] == 'button' and w['text'].replace('\n', ' ') == text for w in self.disp()['widgets'])

    def button(self, text):
        return next(w for w in self.disp()['widgets'] if w['type'] == 'button' and w['text'].replace('\n', ' ') == text)

    def list_widget(self, wid=None):
        ws = [w for w in self.disp()['widgets'] if w['type'] in ('list', 'form') and 'cells' in w]
        if wid:
            return next(w for w in ws if w['id'] == wid)
        assert len(ws) == 1, [w['id'] for w in ws]
        return ws[0]

    def row(self, first, wid=None, col=0):
        """Tap the row of a list or form whose cell `col` is this text (dragging the rows until it shows)."""
        w = self.list_widget(wid)
        x, y, ww, hh = w['rect']
        for _ in range(40):                                                     # start from the top of the list
            if w['scroll'] == 0:
                break
            self.touch(x + 10, y + 5, to_x=x + 10, to_y=y + hh - 5)
            w = self.list_widget(wid)
        for _ in range(40):
            w = self.list_widget(wid)
            texts = [c[col] if len(c) > col else None for c in w['cells']]
            if first in texts:
                k = texts.index(first)
                return self.touch(w['rect'][0] + 10, w['rect'][1] + k * w['row_h'] + w['row_h'] // 2)
            x, y, ww, hh = w['rect']
            assert w['scroll'] + len(w['cells']) < w['count'], (first, texts, self.page())
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
        raise AssertionError(first)

    def rows(self, wid=None):
        """All rows of a list / form (scrolled through from the top)."""
        w = self.list_widget(wid)
        x, y, ww, hh = w['rect']
        for _ in range(40):
            if w['scroll'] == 0:
                break
            self.touch(x + 10, y + 5, to_x=x + 10, to_y=y + hh - 5)
            w = self.list_widget(wid)
        out = list(w['cells'])
        while w['scroll'] + len(w['cells']) < w['count']:
            self.touch(x + 10, y + hh - 5, to_x=x + 10, to_y=y + 5)
            w = self.list_widget(wid)
            out += w['cells'][len(out) - w['scroll']:]
        return out

    def cells(self, wid=None):
        return {c[0]: (c[1] if len(c) > 1 else None) for c in self.rows(wid)}

    def overlay(self):
        return self.disp().get('overlay')

    def ov(self, label):
        """Tap a button of the top overlay (keyboard key, option, dialog button) by its label."""
        ov = self.overlay()
        assert ov is not None, f'no overlay on {self.page()}'
        for b in sorted(ov['buttons'], key=lambda b: b['id'] == 'field'):     # a keyboard's input field shows the typed text: keys first
            if b['label'] == label:
                x, y, w, h = b['rect']
                return self.touch(x + w // 2, y + h // 2)
        raise AssertionError(f'{label!r} not in {[b["label"] for b in ov["buttons"]]}')

    def type(self, text):
        """Type on the open keyboard (the shown text is replaced): upper case through Shift, digits through 12@ on the alpha keyboard."""
        ov = self.overlay()
        assert ov['kind'] == 'keyboard', ov
        labels = {b['label'] for b in ov['buttons']}
        if '7' in labels and 'q' not in labels and 'Q' not in labels:           # numeric keyboard
            if ov['field']:
                self.ov('Del')
                while self.overlay()['field']:
                    self.ov('Del')
            for ch in text:
                assert self.ov(ch), ch
            return
        if ov['field']:
            self.ov('Del')
            while self.overlay()['field']:
                self.ov('Del')
        for ch in text:
            mode = self.overlay()['mode']
            if ch == ' ':
                self.ov('Space')
            elif ch.isdigit() or ch in '$@#%&/():;_+=\'"!?,':
                if mode != 'sym':
                    self.ov('12@')
                self.ov(ch)
            elif ch.isupper():
                if mode == 'sym':
                    self.ov('abc')
                if self.overlay()['mode'] != 'upper':
                    self.ov('Shift')
                self.ov(ch)
            else:
                if mode == 'sym':
                    self.ov('abc')
                if self.overlay()['mode'] == 'upper':
                    self.ov('Shift')
                self.ov(ch)
        assert self.overlay()['field'] in (text, '*' * len(text)), (self.overlay()['field'], text)

    def enter(self, label, value, wid=None):
        """A form row: tap it, type the value, OK."""
        self.row(label, wid)
        self.type(str(value))
        return self.ov('OK')

    def choose(self, label, option, wid=None):
        self.row(label, wid)
        return self.ov(option)

    def dialog(self):
        ov = self.overlay()
        return ov.get('dialog') if ov else None

    def png(self):
        r = self.call(self.A, 'panel.capture', display_id=self.d)
        return Path(r['image']['path']).read_bytes()

    def snapshot(self):
        return copy.deepcopy(self.h.__dict__)
