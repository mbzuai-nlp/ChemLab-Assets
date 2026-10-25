"""Host recipes: configs/host_recipes.json is complete and loads on its own; when the Matterix bridge is present, the JSON
equals the recipes parsed from it (otherwise that test is skipped).

    cd chemlab/tests_generic && python -m unittest test_host_recipes
"""
import os
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401  (puts the chemlab package on sys.path)
from chemlab import recipes as R
from chemlab.hosts import SCRIPTS_DIR


class HostRecipesJson(unittest.TestCase):
    def test_json_is_complete(self):
        rec = R.all_recipes(R.HOST_RECIPES_JSON)
        self.assertEqual(len(rec), 203)
        for atid, r in rec.items():
            self.assertTrue(atid.startswith('instrument.'), atid)
            self.assertIn(r.get('advance'), R.ADVANCE_MODES, atid)
            self.assertTrue(r.get('module') and r.get('cls'), atid)
            mod = Path(SCRIPTS_DIR, *r['module'].split('.'))
            self.assertTrue(mod.with_suffix('.py').is_file() or (mod / '__init__.py').is_file(), (atid, r['module']))

    def test_json_is_the_default_without_matterix(self):
        with mock.patch.object(R, 'MATTERIX_ROOT', Path(os.devnull) / 'no_matterix'):
            self.assertIsNone(R.bridge_path())
            data = R.load_recipes()
        self.assertEqual(Path(data['source']), R.HOST_RECIPES_JSON)
        self.assertEqual(len(data['recipes']) + len(data['panel_recipes']), 203)

    def test_json_equals_the_bridge(self):
        if R.bridge_path() is None:
            self.skipTest('the Matterix bridge is not present (set MATTERIX_PATH to compare)')
        self.assertEqual(R.diff(), [], 'run: python -m chemlab.recipes --export')
        bridge = R.all_recipes(R.bridge_path())
        self.assertEqual(R._plain(bridge), R.all_recipes(R.HOST_RECIPES_JSON))


if __name__ == '__main__':
    unittest.main()
