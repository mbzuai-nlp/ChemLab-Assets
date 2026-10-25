"""The verb table against the asset contract: every verb maps to agent-usable capabilities; hidden ones are unreachable."""
import unittest

from helpers import make_core, run, run_json
from chemlab.contract import NOT_A_VERB, validate


class VerbTableVsContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend, cls.core = make_core()
        cls.inst, cls.contract = cls.core.inst, cls.core.contract

    def test_contract_is_the_flex_contract(self):
        self.assertEqual(self.contract.asset_type_id, self.backend.asset_type_id)
        self.assertEqual(len(self.contract.capabilities), 41)

    def test_every_verb_maps_to_agent_usable_capabilities(self):
        for v in self.inst.verbs.values():
            for cid in v.maps_to:
                cap = self.contract.capabilities[cid]
                self.assertTrue(cap.agent_usable, (v.name, cid))
                self.assertNotIn(cid, self.contract.hidden(), (v.name, cid))

    def test_every_capability_has_a_verb_or_a_reason(self):
        mapped = {cid for v in self.inst.verbs.values() for cid in v.maps_to}
        for cid, cap in self.contract.capabilities.items():
            self.assertTrue(cid in mapped or cap.hidden_reason(), '%s has neither a verb nor a reason to be hidden' % cid)

    def test_hidden_set(self):
        hidden = self.contract.hidden()
        for cid in ('velocity.set', 'sample.mix', 'liquid_level.detect'):          # unsupported in the contract
            self.assertIn('not supported', hidden[cid])
        self.assertIn('evaluator', hidden['panel.frame'])
        for cid in NOT_A_VERB:
            self.assertIn(cid, hidden)

    def test_describe_lists_only_working_verbs_first(self):
        code, d = run_json(self.core, 'describe flex')
        self.assertEqual(code, 0)
        names = [v['name'] for v in d['verbs']]
        for n in ('power', 'home', 'status', 'labware', 'move-to', 'pick-up-tip', 'drop-tip', 'return-tip', 'aspirate', 'dispense', 'transfer',
                  'lights', 'protocols', 'run-protocol', 'pause', 'resume', 'cancel', 'recover'):
            self.assertIn(n, names)
        for n in ('shutdown', 'device-reset', 'deck-config', 'settings', 'detach-pipette'):
            self.assertNotIn(n, names)
        self.assertNotIn('not_offered', d)
        code, d = run_json(self.core, 'describe flex --all')
        names = [v['name'] for v in d['verbs']]
        for n in ('shutdown', 'device-reset', 'deck-config', 'settings', 'detach-pipette', 'protocol-delete', 'restart'):
            self.assertIn(n, names)
        self.assertIn('sample.mix', d['not_offered'])

    def test_destructive_verbs(self):
        destructive = {v.name for v in self.inst.verbs.values() if v.destructive}
        self.assertEqual(destructive, {'shutdown', 'restart', 'protocol-delete', 'device-reset', 'detach-pipette'})

    def test_help_admin_and_verb_help(self):
        r = run(self.core, 'help flex admin')
        self.assertEqual(r.exit_code, 0)
        self.assertIn('device-reset', r.out)
        r = run(self.core, 'run flex aspirate --help')
        self.assertIn('usage: chemlab run flex aspirate', r.out)
        self.assertIn('sample.aspirate', r.out)

    def test_contract_ranges_used_by_the_gate(self):
        self.assertEqual((self.inst.vmin, self.inst.vmax), (5e-9, 1e-6))
        self.assertTrue(self.contract.check('motion.move', {'x_m': 0.5, 'y_m': 0, 'z_m': 0}))
        self.assertFalse(self.contract.check('motion.move', {'x_m': 0.1, 'y_m': 0, 'z_m': 0}))
        self.assertTrue(self.contract.check('sample.mix', {}))                  # unsupported -> refused by the gate

    def test_schema_validator(self):
        s = {'type': 'object', 'properties': {'a': {'type': 'number', 'minimum': 0}, 'b': {'anyOf': [{'type': 'string'}, {'type': 'null'}]}},
             'required': ['a'], 'additionalProperties': False}
        self.assertEqual(validate(s, {'a': 1, 'b': None}), [])
        self.assertTrue(validate(s, {'a': -1}))
        self.assertTrue(validate(s, {'b': 'x'}))
        self.assertTrue(validate(s, {'a': 1, 'c': 2}))
        self.assertTrue(validate(s, {'a': True}))


if __name__ == '__main__':
    unittest.main()
