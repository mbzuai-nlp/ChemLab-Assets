"""Contract examples that must work as written : every ``template.read`` example of every contract.

``template.read`` looks up a public reference library, so its example must resolve on a fresh instrument whatever the
lab state.  gamry_reference620 named ``redox_fe3_fe2``, which its library does not have (INVALID_ARGUMENT on every
state; fixed in its 1.1.1).  The lab here has only the instruments whose contract has ``template.read`` and no set-up.
"""
import json
import shutil
import unittest

from helpers import tmpdir
from chemlab.hosts import contract_index
from chemlab.labbackend import LabBackend, load_config


class TemplateReadExamples(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tmpdir()
        cfg, index = load_config(), contract_index()
        cfg['instruments'] = [e for e in cfg['instruments']
                              if 'template.read' in json.loads(index[e['asset_type_id']].read_text(encoding='utf-8'))['capabilities']]
        cls.backend = LabBackend(cfg, run_dir=cls.tmp + '/lab', setup=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_template_read_example_resolves(self):
        n = 0
        for iid, inst in sorted(self.backend.instruments.items()):
            cap = inst.contract['capabilities']['template.read']
            examples = cap.get('example_arguments')
            for i, args in enumerate(examples if isinstance(examples, list) else [examples]):
                n += 1
                with self.subTest(instrument=iid, arguments=args):
                    r = self.backend.invoke('agent', iid, 'template.read', args, 'example_%d' % i)
                    self.assertIn(r.get('status'), ('succeeded', 'accepted'), (r.get('error') or {}).get('code'))
        self.assertGreaterEqual(n, 40)                                          # 49 examples when this test was written


if __name__ == '__main__':
    unittest.main()
