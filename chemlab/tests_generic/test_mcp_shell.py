"""The optional MCP shell: same commands, same core, no rules of its own (it only builds argv)."""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

from helpers import ROOT, run, tmpdir
from chemlab.glab import GenericLab, single
from chemlab.mcp_shell import TOOLS, argv_for


def strip_ids(text):
    return re.sub(r'(request: |"request_id": ")r-\d+', r'\1r-N', text)


class McpShell(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tmpdir()
        cls.lab = GenericLab(os.path.join(cls.tmp, 'lab'), single(['vaisala_dmt152', 'mt_xpr204e'])).start()
        env = dict(os.environ, CHEMLAB_SOCKET=cls.lab.server.path, CHEMLAB_TOKEN=cls.lab.tokens['agent'], CHEMLAB_WORK=str(cls.lab.work),
                   PYTHONPATH=ROOT)
        cls.proc = subprocess.Popen([sys.executable, '-m', 'chemlab.mcp_shell'], cwd=ROOT, env=env, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, text=True)
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        cls.proc.stdin.close()
        cls.proc.wait(timeout=10)
        cls.lab.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def rpc(self, method, params=None):
        type(self).n += 1
        self.proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': self.n, 'method': method, 'params': params or {}}) + '\n')
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def test_initialize_and_tools(self):
        r = self.rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 't', 'version': '0'}})
        self.assertEqual(r['result']['serverInfo']['name'], 'chemlab')
        names = {t['name'] for t in self.rpc('tools/list')['result']['tools']}
        self.assertEqual(names, {t['name'] for t in TOOLS})

    def test_same_replies_as_the_command_line(self):
        for tool, args, cmd in [('chemlab_describe', {'instrument': 'vaisala_dmt152'}, 'describe vaisala_dmt152'),
                                ('chemlab_help', {'instrument': 'vaisala_dmt152', 'capability': 'power.set'}, 'help vaisala_dmt152 power.set'),
                                ('chemlab_call', {'instrument': 'vaisala_dmt152', 'capability': 'truth.inspect'}, 'call vaisala_dmt152 truth.inspect'),
                                ('chemlab_call', {'instrument': 'vaisala_dmt152', 'capability': 'power.set', 'arguments': {'on': True, 'voltage_v': 99}},
                                 'call vaisala_dmt152 power.set --on true --voltage_v 99')]:
            r = self.rpc('tools/call', {'name': tool, 'arguments': args})['result']
            c = run(self.lab, cmd)
            self.assertEqual(r['isError'], c.exit_code != 0, tool)
            self.assertEqual(strip_ids(r['content'][0]['text']), strip_ids(c.out if c.exit_code == 0 else c.err), tool)

    def test_screen_returns_the_image(self):
        r = self.rpc('tools/call', {'name': 'chemlab_screen', 'arguments': {'instrument': 'mt_xpr204e', 'file': 'mcp.png'}})['result']
        self.assertFalse(r['isError'], r)
        img = [c for c in r['content'] if c['type'] == 'image']
        self.assertEqual(len(img), 1)
        self.assertTrue((self.lab.work / 'mcp.png').exists())

    def test_argv_only(self):
        self.assertEqual(argv_for('chemlab_panel', {'instrument': 'x', 'gesture': 'touch', 'target': 'd', 'x': 1, 'y': 2, 'to': [3, 4], 'hold': 1}),
                         ['chemlab', 'panel', 'x', 'touch', 'd', '1', '2', '--to', '3', '4', '--hold', '1.0'])
        self.assertEqual(argv_for('chemlab_wait', {'seconds': 5}), ['chemlab', 'wait', '5.0'])


if __name__ == '__main__':
    unittest.main()
