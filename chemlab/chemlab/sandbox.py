"""Agent sandbox without root: Landlock (file and exec allowlist) + seccomp (no signals, no ptrace, AF_UNIX sockets only).

Containers need root (or the docker group) and unprivileged user namespaces are often blocked, so the sandbox uses the two
kernel features an unprivileged process may apply to itself:

* Landlock (ABI >= 3, Linux 6.2 or newer with Landlock enabled; TCP rules need ABI 4, Linux 6.7): the shell may read system
  files (/usr, /etc), execute only an allowlist of shell tools plus the chemlab client, and write only inside its work
  directory.  Everything else - the Chemistry-Sim source, the contracts, the evaluator data, the home directory, /proc,
  /tmp - is invisible ("Permission denied").  TCP bind/connect is denied (ABI 4).
* seccomp: kill/tkill/tgkill/pidfd*, ptrace, process_vm_*, bpf, perf_event_open, io_uring, memfd_create, execveat fail with
  EPERM; socket() only for AF_UNIX.

Limits (written in the README): Landlock ABI 4 does not restrict connect() on UNIX sockets, so any same-user UNIX socket on
the host stays reachable by a program that can speak to it; only the allowlisted tools can run, and none of them opens UNIX
sockets except the chemlab client, which only talks to a socket named lab.sock and authenticates with a per-run token.  A
hand-written ELF shared object loaded with LD_PRELOAD into an allowed tool could run arbitrary code (Landlock does not check
mmap); it would still be confined by Landlock and seccomp.  Processes share the user id and the PID namespace (no user
namespaces), so /proc is simply not readable.

Requirements: Linux on x86_64 (the seccomp filter uses x86_64 system-call numbers), Landlock ABI >= 3, /usr/bin/bash.
``check()`` says what is missing on this machine; ``require()`` raises SandboxUnavailable with that text.  The sandbox is never
weakened to make it run: without it, chemlab only offers an explicit, opt-in unsandboxed mode for local, trusted use
(``python -m chemlab.gserve --no-sandbox``, see glab.py).

Usage (the harness side):  argv = sandbox_argv(workdir, bindir, command); subprocess.run(argv, env=sandbox_env(...))
"""
import ctypes
import os
import platform
import shutil
import struct
import subprocess
import sys

# ---- Landlock
_SYS_CREATE, _SYS_ADD_RULE, _SYS_RESTRICT = 444, 445, 446
FS = dict(EXECUTE=1 << 0, WRITE_FILE=1 << 1, READ_FILE=1 << 2, READ_DIR=1 << 3, REMOVE_DIR=1 << 4, REMOVE_FILE=1 << 5,
          MAKE_CHAR=1 << 6, MAKE_DIR=1 << 7, MAKE_REG=1 << 8, MAKE_SOCK=1 << 9, MAKE_FIFO=1 << 10, MAKE_BLOCK=1 << 11,
          MAKE_SYM=1 << 12, REFER=1 << 13, TRUNCATE=1 << 14)
NET_BIND_TCP, NET_CONNECT_TCP = 1 << 0, 1 << 1
FILE_RIGHTS = FS['EXECUTE'] | FS['WRITE_FILE'] | FS['READ_FILE'] | FS['TRUNCATE']
READ = FS['READ_FILE'] | FS['READ_DIR']
WRITE_TREE = (READ | FS['WRITE_FILE'] | FS['REMOVE_DIR'] | FS['REMOVE_FILE'] | FS['MAKE_DIR'] | FS['MAKE_REG']
              | FS['MAKE_FIFO'] | FS['MAKE_SYM'] | FS['REFER'] | FS['TRUNCATE'])

# System trees the shell may read (no execute right on them: executing needs a file rule below).
READ_TREES = ['/usr', '/etc', '/lib', '/lib64']
# The only programs the agent can run.  Interpreters with socket support (python, perl, nc, curl, ...) are not here.
EXEC_ALLOW = ['bash', 'sh', 'dash', 'ls', 'cat', 'echo', 'printf', 'head', 'tail', 'wc', 'sort', 'uniq', 'cut', 'tr', 'seq',
              'sleep', 'date', 'tee', 'true', 'false', 'env', 'xargs', 'grep', 'egrep', 'fgrep', 'sed', 'gawk', 'mawk', 'jq',
              'bc', 'expr', 'test', '[', 'basename', 'dirname', 'mkdir', 'rm', 'cp', 'mv', 'touch', 'pwd', 'diff', 'cmp',
              'paste', 'nl', 'fold', 'tac', 'rev', 'od', 'yes', 'numfmt', 'comm', 'join', 'column', 'which', 'stat', 'find']
# The dynamic loader (a dynamically linked tool needs EXECUTE on it); the path differs between distributions.
LOADERS = ['/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2', '/lib64/ld-linux-x86-64.so.2', '/usr/lib64/ld-linux-x86-64.so.2',
           '/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2']
LOADER = LOADERS[0]
BASH = '/usr/bin/bash'


class _RulesetAttr(ctypes.Structure):
    _fields_ = [('handled_access_fs', ctypes.c_uint64), ('handled_access_net', ctypes.c_uint64)]


_libc = ctypes.CDLL(None, use_errno=True)
_libc.syscall.restype = ctypes.c_long


class SandboxUnavailable(RuntimeError):
    pass


def landlock_abi():
    if not sys.platform.startswith('linux'):
        return 0
    r = _libc.syscall(_SYS_CREATE, None, ctypes.c_size_t(0), ctypes.c_uint32(1))
    return r if r > 0 else 0


def check():
    """What this machine lacks for the sandbox: a list of reasons, empty when the sandbox can run."""
    if not sys.platform.startswith('linux'):
        return ['the agent sandbox needs Linux (Landlock and seccomp); this is %s' % sys.platform]
    out = []
    if platform.machine() not in ('x86_64', 'AMD64'):
        out.append('the seccomp filter is written for x86_64; this machine is %s' % platform.machine())
    abi = landlock_abi()
    if abi < 3:
        out.append('Landlock ABI >= 3 is needed (Linux 6.2 or newer, with "landlock" in the kernel\'s LSM list); found %s'
                   % (abi or 'none: Landlock is not available'))
    if not os.access(BASH, os.X_OK):
        out.append('%s is needed' % BASH)
    return out


_PROBED = None


def require():
    """Raise SandboxUnavailable unless the sandbox can be applied here (static checks, then one real run of `true`)."""
    global _PROBED
    if _PROBED is None:
        problems = check()
        if not problems:
            r = subprocess.run(sandbox_argv(os.devnull, os.devnull, 'true'), env={'PATH': '/usr/bin:/bin'},
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, cwd='/')
            if r.returncode != 0:
                problems = [r.stderr.decode('utf-8', 'replace').strip().splitlines()[-1:] or ['exit %d' % r.returncode]][0]
        _PROBED = problems
    if _PROBED:
        raise SandboxUnavailable('the chemlab agent sandbox cannot run on this machine: %s. For local, trusted use only, start '
                                 'the lab with --no-sandbox (no isolation: the shell runs as you).' % '; '.join(_PROBED))


def _landlock(rules):
    """rules: list of (path, access).  Paths that do not exist are skipped."""
    abi = landlock_abi()
    if abi < 3:
        raise OSError('Landlock ABI >= 3 required (found %d)' % abi)
    handled_fs = 0
    for v in FS.values():
        handled_fs |= v
    attr = _RulesetAttr(handled_fs, (NET_BIND_TCP | NET_CONNECT_TCP) if abi >= 4 else 0)
    size = ctypes.sizeof(_RulesetAttr) if abi >= 4 else 8
    fd = _libc.syscall(_SYS_CREATE, ctypes.byref(attr), ctypes.c_size_t(size), ctypes.c_uint32(0))
    if fd < 0:
        raise OSError(ctypes.get_errno(), 'landlock_create_ruleset')
    for path, access in rules:
        try:
            pfd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        except FileNotFoundError:
            continue
        try:
            if not os.path.isdir(path):
                access &= FILE_RIGHTS
            pb = ctypes.create_string_buffer(struct.pack('<Qi', access, pfd))   # struct landlock_path_beneath_attr (packed)
            if _libc.syscall(_SYS_ADD_RULE, ctypes.c_int(fd), ctypes.c_int(1), pb, ctypes.c_uint32(0)) < 0:
                raise OSError(ctypes.get_errno(), 'landlock_add_rule %s' % path)
        finally:
            os.close(pfd)
    if _libc.prctl(38, 1, 0, 0, 0) != 0:                                   # PR_SET_NO_NEW_PRIVS
        raise OSError(ctypes.get_errno(), 'no_new_privs')
    if _libc.syscall(_SYS_RESTRICT, ctypes.c_int(fd), ctypes.c_uint32(0)) < 0:
        raise OSError(ctypes.get_errno(), 'landlock_restrict_self')
    os.close(fd)


# ---- seccomp (classic BPF)
_DENY_SYSCALLS = {'kill': 62, 'tkill': 200, 'tgkill': 234, 'rt_sigqueueinfo': 129, 'rt_tgsigqueueinfo': 297,
                  'pidfd_send_signal': 424, 'pidfd_open': 434, 'pidfd_getfd': 438, 'ptrace': 101, 'process_vm_readv': 310,
                  'process_vm_writev': 311, 'bpf': 321, 'perf_event_open': 298, 'io_uring_setup': 425, 'memfd_create': 319,
                  'execveat': 322, 'keyctl': 250, 'add_key': 248, 'request_key': 249, 'userfaultfd': 323}
_SOCKET_NR = 41
_AF_UNIX = 1
_EPERM = 1
_AUDIT_ARCH_X86_64 = 0xC000003E


def _bpf(code, jt, jf, k):
    return struct.pack('HBBI', code, jt, jf, k)


def _seccomp():
    LD_W_ABS, JEQ, JGE, RET = 0x20, 0x15, 0x35, 0x06
    ALLOW, KILL, ERRNO = 0x7FFF0000, 0x80000000, 0x00050000 | _EPERM
    prog = [_bpf(LD_W_ABS, 0, 0, 4), _bpf(JEQ, 1, 0, _AUDIT_ARCH_X86_64), _bpf(RET, 0, 0, KILL),
            _bpf(LD_W_ABS, 0, 0, 0), _bpf(JGE, 0, 1, 0x40000000), _bpf(RET, 0, 0, ERRNO)]
    for nr in sorted(_DENY_SYSCALLS.values()):
        prog += [_bpf(JEQ, 0, 1, nr), _bpf(RET, 0, 0, ERRNO)]
    # socket(domain, ...): allow AF_UNIX only
    prog += [_bpf(JEQ, 0, 3, _SOCKET_NR), _bpf(LD_W_ABS, 0, 0, 16), _bpf(JEQ, 1, 0, _AF_UNIX), _bpf(RET, 0, 0, ERRNO),
             _bpf(RET, 0, 0, ALLOW)]
    buf = ctypes.create_string_buffer(b''.join(prog))

    class _Fprog(ctypes.Structure):
        _fields_ = [('len', ctypes.c_ushort), ('filter', ctypes.c_void_p)]
    fprog = _Fprog(len(prog), ctypes.addressof(buf))
    if _libc.prctl(38, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'no_new_privs')
    if _libc.prctl(22, 2, ctypes.byref(fprog), 0, 0) != 0:                # PR_SET_SECCOMP, SECCOMP_MODE_FILTER
        raise OSError(ctypes.get_errno(), 'seccomp')


def _exec_rules(bindir):
    rules = [(p, READ) for p in READ_TREES]
    seen = set()
    for name in EXEC_ALLOW:
        path = shutil.which(name, path='/usr/bin:/usr/sbin')
        if path:
            real = os.path.realpath(path)
            if real not in seen:
                seen.add(real)
                rules.append((real, FS['EXECUTE'] | FS['READ_FILE']))
    for loader in LOADERS:
        if os.path.exists(loader):
            real = os.path.realpath(loader)
            if real not in seen:
                seen.add(real)
                rules.append((real, FS['EXECUTE'] | FS['READ_FILE']))
    rules.append((bindir, FS['EXECUTE'] | READ))
    rules.append(('/dev/null', FS['READ_FILE'] | FS['WRITE_FILE'] | FS['TRUNCATE']))
    rules.append(('/dev/zero', FS['READ_FILE']))
    rules.append(('/dev/urandom', FS['READ_FILE']))
    return rules


def enter(workdir, bindir):
    """Restrict the calling process (irreversible).  Call in the child right before exec."""
    rules = _exec_rules(bindir) + [(workdir, WRITE_TREE)]
    _landlock(rules)
    _seccomp()


def sandbox_env(workdir, bindir, extra=None):
    env = {'PATH': '%s:/usr/bin:/bin' % bindir, 'HOME': workdir, 'TMPDIR': workdir, 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
           'TERM': 'dumb', 'PS1': '$ ', 'SHELL': '/usr/bin/bash'}
    env.update(extra or {})
    return env


def sandbox_argv(workdir, bindir, command):
    """argv for subprocess: this module re-executes itself as the launcher, applies the sandbox, then execs bash -c."""
    return [sys.executable, '-I', '-S', os.path.abspath(__file__), workdir, bindir, command]


def _main(argv):
    workdir, bindir, command = argv[1], argv[2], argv[3]
    if os.path.isdir(workdir):
        os.chdir(workdir)
    os.umask(0o077)
    try:
        enter(workdir, bindir)
    except OSError as e:                                                    # never run the command unconfined
        sys.stderr.write('chemlab sandbox: cannot confine the shell (%s)\n' % e)
        sys.exit(126)
    os.execve(BASH, ['bash', '-c', command], dict(os.environ))


if __name__ == '__main__':
    _main(sys.argv)
