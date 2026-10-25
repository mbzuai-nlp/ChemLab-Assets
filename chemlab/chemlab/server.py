"""Layer 3 - the local socket the `chemlab` client talks to.

One request per connection: a JSON line {"token": ..., "argv": [...]}; the reply is "CHEMLAB/1 <exit> <len out> <len err>\\n"
followed by stdout and stderr bytes.  The token selects the caller identity (each caller is an agent-visibility principal of
the backend); the evaluator identity is never reachable through this socket.
"""
import hmac
import json
import os
import socket
import threading

MAX_REQUEST = 1 << 20


class LabServer:
    def __init__(self, core, socket_dir, tokens):
        """tokens: {token: caller name}."""
        self.core = core
        self.socket_dir = os.path.abspath(socket_dir)
        self.path = os.path.join(self.socket_dir, 'lab.sock')
        self.tokens = dict(tokens)
        self._sock = None
        self._thread = None
        self._stop = threading.Event()
        self.rejected = []                                                # connections refused (bad token / bad request)

    def start(self):
        if os.path.exists(self.path):
            os.unlink(self.path)
        dfd = os.open(self.socket_dir, os.O_PATH | os.O_DIRECTORY)
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.bind('/proc/self/fd/%d/lab.sock' % dfd)                       # socket paths are limited to 108 bytes
        finally:
            os.close(dfd)
        os.chmod(self.path, 0o600)
        s.listen(16)
        s.settimeout(0.2)
        self._sock = s
        self._thread = threading.Thread(target=self._serve, name='chemlab-server', daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._sock:
            self._sock.close()
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _reply(self, conn, code, out, err):
        ob, eb = out.encode('utf-8'), err.encode('utf-8')
        conn.sendall(('CHEMLAB/1 %d %d %d\n' % (code, len(ob), len(eb))).encode() + ob + eb)

    def _handle(self, conn):
        with conn:
            conn.settimeout(30)
            data = b''
            try:
                while b'\n' not in data and len(data) < MAX_REQUEST:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                req = json.loads(data.split(b'\n', 1)[0].decode('utf-8'))
                token, argv = req.get('token'), req.get('argv')
                if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv) or argv[0] != 'chemlab':
                    raise ValueError('bad argv')
            except Exception:
                self.rejected.append('bad request')
                self._reply(conn, 2, '', 'error[validation/BAD_REQUEST]: the request could not be read.\nfix: use the chemlab client.\n'
                                         'instrument changed: no\n')
                return
            caller = next((c for t, c in self.tokens.items() if isinstance(token, str) and hmac.compare_digest(t, token)), None)
            if caller is None:
                self.rejected.append('bad token')
                self._reply(conn, 2, '', 'error[validation/NOT_AUTHORIZED]: this shell is not authorized for the chemlab service.\n'
                                         'fix: use the shell you were given (CHEMLAB_TOKEN).\ninstrument changed: no\n')
                return
            reply = self.core.handle(argv, caller)
            try:
                self._reply(conn, reply.exit_code, reply.out, reply.err)
            except OSError:
                pass
