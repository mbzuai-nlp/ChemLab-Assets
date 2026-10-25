"""The sandbox really confines the agent's shell: no source, no runtime import, no hidden data, no way around the gate.

Each probe runs as an agent command inside the sandbox through the real client; the instrument must be unchanged after all
bypass attempts and only the chemlab client may reach the service.
"""
import os
import shutil
import tempfile
import unittest

from helpers import ROOT, snapshot
from chemlab.backend import SCRIPTS_DIR
from chemlab.flex_tasks import deck
from chemlab.lab import Lab
from chemlab.sandbox import check as sandbox_problems

RUNS = os.path.join(ROOT, 'build', 'test_runs')
DENIED = ('Permission denied', 'Operation not permitted', 'No such file')


class SandboxBypass(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sandbox_problems():
            raise unittest.SkipTest('agent sandbox not available here: %s' % '; '.join(sandbox_problems()))
        os.makedirs(RUNS, exist_ok=True)
        cls.tmp = tempfile.mkdtemp(dir=RUNS)
        cls.lab = Lab(deck({'reservoir:A1': ('buffer', 10000)}), os.path.join(cls.tmp, 'lab'), speed=None).start()
        cls.before = snapshot(cls.lab.backend)

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

    # -- source code, contract, hidden data
    def test_cannot_read_runtime_source_or_contract(self):
        self.denied('cat %s/basic40_runtime_automation.py' % SCRIPTS_DIR)
        self.denied('cat %s/chemlab/core.py' % ROOT)
        self.denied('cat %s' % self.lab.backend.contract_path)
        self.denied('ls %s' % os.path.dirname(ROOT))
        self.denied('ls ~/..')
        self.denied('grep -r OpentronsFlexRuntime %s' % SCRIPTS_DIR)

    def test_cannot_read_the_run_dir_log_or_other_runs(self):
        self.denied('cat %s/core_log.jsonl' % self.lab.run_dir)
        self.denied('ls %s' % self.lab.run_dir)
        self.denied('ls %s' % RUNS)

    def test_no_proc_no_tmp_no_home(self):
        self.denied('cat /proc/self/environ')
        self.denied('cat /proc/%d/environ' % os.getpid())
        self.denied('ls /proc')
        self.denied('ls /tmp')
        self.denied('echo x > /tmp/chemlab_probe')
        self.denied('echo x > %s/probe' % self.lab.run_dir)
        self.denied('ls %s' % os.path.dirname(os.path.expanduser('~')))
        self.denied('ls %s' % os.path.expanduser('~'))

    def test_no_interpreters_no_runtime_import(self):
        self.denied('python3 -c "import sys; sys.path.insert(0, \'%s\'); import basic40_runtime_automation"' % SCRIPTS_DIR)
        for exe in ('/usr/bin/python3', shutil.which('python3') or '/usr/bin/python3', '/usr/bin/perl', '/usr/bin/nc', '/usr/bin/curl', '/usr/bin/wget',
                    '/usr/bin/socat', '/usr/bin/ssh', '/usr/bin/gcc', '/usr/bin/systemd-run', '/usr/bin/dbus-send'):
            self.denied('%s --version' % exe, 'Permission denied', 'No such file')

    def test_no_exec_of_own_files_and_no_link_tricks(self):
        self.denied('cp /usr/bin/ls ./myls && ./myls')
        self.denied('printf "#!/bin/sh\\necho hi\\n" > x.sh && chmod +x x.sh && ./x.sh')
        self.denied('ln -s %s/basic40_runtime_automation.py link.py && cat link.py' % SCRIPTS_DIR)
        self.denied('ln %s/basic40_runtime_automation.py hard.py && cat hard.py' % SCRIPTS_DIR, 'Permission denied', 'Invalid cross-device',
                    'Operation not permitted')

    def test_no_network_no_signals(self):
        self.denied('exec 3<>/dev/tcp/127.0.0.1/22')
        self.denied('exec 3<>/dev/udp/127.0.0.1/53')
        self.denied('kill -0 %d' % os.getpid())
        self.denied('kill -0 $PPID')

    # -- only the client reaches the service, only with this shell's token
    def test_client_auth_and_socket_checks(self):
        n = len(self.lab.core.log.entries)
        self.denied('CHEMLAB_TOKEN=0000 chemlab run flex power off', 'NOT_AUTHORIZED')
        self.denied('CHEMLAB_SOCKET=/run/user/%d/bus chemlab status' % os.getuid(), 'UNREACHABLE')
        self.denied('CHEMLAB_SOCKET=%s/other/lab.sock chemlab status' % self.tmp, 'UNREACHABLE')
        self.assertEqual(len(self.lab.core.log.entries), n)                  # nothing reached the core
        self.assertIn('bad token', self.lab.server.rejected)

    def test_other_labs_token_is_refused(self):
        other = Lab(deck({}), os.path.join(self.tmp, 'other'), speed=None).start()
        try:
            r = self.lab.sh('CHEMLAB_SOCKET=%s chemlab status' % other.server.path)   # own token, someone else's socket
            self.assertEqual(r.exit_code, 2)
            self.assertIn('NOT_AUTHORIZED', r.err)
            self.assertEqual(other.core.log.entries, [])
        finally:
            other.stop()

    def test_service_down_is_exit_5(self):
        other = Lab(deck({}), os.path.join(self.tmp, 'down'), speed=None)    # never started: no socket
        r = other.sh('chemlab status; echo "exit=$?"')
        self.assertIn('exit=5', r.out)
        self.assertIn('UNREACHABLE', r.err)

    # -- the shell still works
    def test_shell_still_works(self):
        r = self.lab.sh('for i in 1 2 3; do echo $i; done | wc -l; cat <<EOF > n.txt\nnote\nEOF\ncat n.txt; '
                        'chemlab status flex --json | jq -r .power; seq 3 | awk \'{s+=$1} END {print s}\'; env | sort | cut -d= -f1 | paste -sd, -')
        self.assertEqual(r.exit_code, 0, r.err)
        lines = r.out.splitlines()
        self.assertEqual(lines[:4], ['3', 'note', 'true', '6'])
        allowed = {'CHEMLAB_SOCKET', 'CHEMLAB_TOKEN', 'HOME', 'LANG', 'LC_ALL', 'PATH', 'PS1', 'SHELL', 'TERM', 'TMPDIR', 'PWD', 'SHLVL', '_', 'OLDPWD'}
        self.assertLessEqual(set(lines[4].split(',')), allowed)          # nothing leaks in from the harness environment
        self.assertIn('CHEMLAB_TOKEN', lines[4])

    def test_zz_instrument_unchanged_by_all_probes(self):
        self.assertEqual(snapshot(self.lab.backend), self.before)


if __name__ == '__main__':
    unittest.main()
