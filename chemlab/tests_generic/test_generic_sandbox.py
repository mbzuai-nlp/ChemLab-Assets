"""The pilot's sandbox bypass probes, against the generic lab (several instruments, the default set-up).

Each probe runs as an agent command inside the sandbox through the real client; the instruments must be unchanged after all
bypass attempts and only the chemlab client may reach the service.
"""
import json
import os
import shutil
import unittest

from helpers import ROOT, RUNS, tmpdir
from chemlab.glab import GenericLab, single
from chemlab.hosts import SCRIPTS_DIR
from chemlab.sandbox import check as sandbox_problems

DENIED = ('Permission denied', 'Operation not permitted', 'No such file')
IDS = ['ika_plate_rctdigital', 'mt_xpr204e', 'vaisala_dmt152']


def snapshot(lab):
    return json.dumps({i: lab.backend.evaluator_read(i)['observations'] for i in IDS}, sort_keys=True, default=str)


class GenericSandboxBypass(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sandbox_problems():
            raise unittest.SkipTest('agent sandbox not available here: %s' % '; '.join(sandbox_problems()))
        cls.tmp = tmpdir()
        cls.lab = GenericLab(os.path.join(cls.tmp, 'lab'), single(IDS)).start()
        cls.before = snapshot(cls.lab)
        cls.contract = str(cls.lab.backend.instruments['ika_plate_rctdigital'].contract_path)

    @classmethod
    def tearDownClass(cls):
        cls.lab.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def denied(self, command, *needles):
        r = self.lab.sh(command)
        text = r.out + r.err
        self.assertNotEqual(r.exit_code, 0, 'not refused: %s\n%s' % (command, text))
        self.assertTrue(any(n in text for n in (needles or DENIED)), 'unexpected output for %s:\n%s' % (command, text))
        return r

    def test_cannot_read_runtime_source_contract_or_panel_spec(self):
        self.denied('cat %s/asset_runtime/ika_plate_rctdigital.py' % SCRIPTS_DIR)
        self.denied('cat %s/chemlab/generic.py' % ROOT)
        self.denied('cat %s' % self.contract)
        self.denied('cat %s' % self.contract.replace('asset.contract.json', 'panel_ui.json'))
        self.denied('ls %s' % os.path.dirname(ROOT))
        self.denied('cat %s/configs/all203.json' % ROOT)
        self.denied('grep -r IkaPlateRctdigitalRuntime %s' % SCRIPTS_DIR)

    def test_cannot_read_the_run_dir_captures_or_log(self):
        self.denied('cat %s/core_log.jsonl' % self.lab.run_dir)
        self.denied('ls %s/captures' % self.lab.run_dir)
        self.denied('ls %s' % self.lab.run_dir)
        self.denied('ls %s' % RUNS)

    def test_no_proc_no_tmp_no_home(self):
        self.denied('cat /proc/self/environ')
        self.denied('cat /proc/%d/environ' % os.getpid())
        self.denied('ls /proc')
        self.denied('ls /tmp')
        self.denied('ls /tmp/chemistry_sim_panel')
        self.denied('echo x > /tmp/chemlab_probe')
        self.denied('ls %s' % os.path.dirname(os.path.expanduser('~')))
        self.denied('ls %s' % os.path.expanduser('~'))

    def test_no_interpreters_no_runtime_import(self):
        self.denied('python3 -c "import sys; sys.path.insert(0, \'%s\'); import basic40_runtime_measure"' % SCRIPTS_DIR)
        for exe in ('/usr/bin/python3', shutil.which('python3') or '/usr/bin/python3', '/usr/bin/perl', '/usr/bin/nc', '/usr/bin/curl',
                    '/usr/bin/wget', '/usr/bin/socat', '/usr/bin/ssh', '/usr/bin/gcc', '/usr/bin/systemd-run', '/usr/bin/dbus-send'):
            self.denied('%s --version' % exe, 'Permission denied', 'No such file')

    def test_no_exec_of_own_files_and_no_link_tricks(self):
        self.denied('cp /usr/bin/ls ./myls && ./myls')
        self.denied('printf "#!/bin/sh\\necho hi\\n" > x.sh && chmod +x x.sh && ./x.sh')
        self.denied('ln -s %s link.json && cat link.json' % self.contract)
        self.denied('ln %s hard.json && cat hard.json' % self.contract, 'Permission denied', 'Invalid cross-device', 'Operation not permitted')

    def test_no_network_no_signals(self):
        self.denied('exec 3<>/dev/tcp/127.0.0.1/22')
        self.denied('exec 3<>/dev/udp/127.0.0.1/53')
        self.denied('kill -0 %d' % os.getpid())
        self.denied('kill -0 $PPID')

    def test_client_auth_and_socket_checks(self):
        n = len(self.lab.core.log.entries)
        self.denied('CHEMLAB_TOKEN=0000 chemlab call vaisala_dmt152 power.set --on true', 'NOT_AUTHORIZED')
        self.denied('CHEMLAB_SOCKET=/run/user/%d/bus chemlab list' % os.getuid(), 'UNREACHABLE')
        self.denied('CHEMLAB_SOCKET=%s/other/lab.sock chemlab list' % self.tmp, 'UNREACHABLE')
        self.assertEqual(len(self.lab.core.log.entries), n)
        self.assertIn('bad token', self.lab.server.rejected)

    def test_other_labs_socket_refuses_this_token(self):
        other = GenericLab(os.path.join(self.tmp, 'other'), single(['vaisala_dmt152']), setup=False).start()
        try:
            r = self.lab.sh('CHEMLAB_SOCKET=%s chemlab list' % other.server.path)
            self.assertEqual(r.exit_code, 2)
            self.assertIn('NOT_AUTHORIZED', r.err)
            self.assertEqual(other.core.log.entries, [])
        finally:
            other.stop()

    def test_service_down_is_exit_5(self):
        down = GenericLab(os.path.join(self.tmp, 'down'), single(['vaisala_dmt152']), setup=False)      # never started
        r = down.sh('chemlab list; echo "exit=$?"')
        self.assertIn('exit=5', r.out)
        self.assertIn('UNREACHABLE', r.err)

    def test_screen_lands_in_the_work_dir_only(self):
        r = self.lab.sh('chemlab screen mt_xpr204e -o shots/home.png && head -c 8 shots/home.png | od -c | head -1')
        self.assertEqual(r.exit_code, 0, r.err)
        self.assertIn('P   N   G', r.out)
        r = self.lab.sh('chemlab screen mt_xpr204e -o ../../escape.png; echo "exit=$?"')
        self.assertIn('exit=2', r.out)
        self.assertFalse(os.path.exists(os.path.join(self.lab.run_dir, '..', 'escape.png')))
        r = self.lab.sh('chemlab screen mt_xpr204e -o "$HOME/abs.png" && ls abs.png')
        self.assertEqual(r.exit_code, 0, r.err)

    def test_shell_still_works(self):
        r = self.lab.sh('for i in 1 2 3; do echo $i; done | wc -l; chemlab list --json | jq -r ".instruments | length"; '
                        'chemlab read vaisala_dmt152 --json | jq -r .instrument; env | sort | cut -d= -f1 | paste -sd, -')
        self.assertEqual(r.exit_code, 0, r.err)
        lines = r.out.splitlines()
        self.assertEqual(lines[:3], ['3', '3', 'vaisala_dmt152'])
        allowed = {'CHEMLAB_SOCKET', 'CHEMLAB_TOKEN', 'HOME', 'LANG', 'LC_ALL', 'PATH', 'PS1', 'SHELL', 'TERM', 'TMPDIR', 'PWD', 'SHLVL', '_', 'OLDPWD'}
        self.assertLessEqual(set(lines[3].split(',')), allowed)

    def test_scripts_of_chemlab_calls_run_with_bash(self):
        r = self.lab.sh('printf "chemlab time\\nchemlab read vaisala_dmt152 power.on --json | jq -r .instrument\\n" > s.sh; bash s.sh; . ./s.sh | tail -1')
        self.assertEqual(r.exit_code, 0, r.err)
        self.assertEqual(r.out.count('vaisala_dmt152'), 2, r.out)

    def test_zz_instruments_unchanged_by_all_probes(self):
        self.assertEqual(snapshot(self.lab), self.before)


if __name__ == '__main__':
    unittest.main()
