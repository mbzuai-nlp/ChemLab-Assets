"""MCP shell: the generic chemlab commands as MCP tools (JSON-RPC 2.0 over stdio).  No rules of its own.

Each tool call is turned into the same argv the command line would send (``chemlab call INST CAP --args JSON`` ...) and handed
to the same core service, either through the lab's unix socket with this caller's token (like the C client) or in-process.
The reply text and exit code are returned as they are (exit code != 0 -> isError).  ``screen`` also returns the PNG as an
image content block, so a multimodal model sees the display.

    CHEMLAB_SOCKET=.../lab.sock CHEMLAB_TOKEN=... CHEMLAB_WORK=.../work python -m chemlab.mcp_shell
    python -m chemlab.gserve --only mt_xpr204e --mcp            # in-process, for trying it out
"""
import base64
import json
import os
import socket
import sys
from pathlib import Path

PROTOCOL_VERSION = '2025-06-18'
_J = {'type': 'boolean', 'description': 'machine-readable JSON reply instead of text'}


def _tool(name, description, props, required=()):
    p = dict(props)
    p['json'] = _J
    return {'name': name, 'description': description,
            'inputSchema': {'type': 'object', 'properties': p, 'required': list(required), 'additionalProperties': False}}


INST = {'type': 'string', 'description': 'instrument id (see chemlab_list)'}
TOOLS = [
    _tool('chemlab_list', 'Instruments in the lab: id, name, domain.', {'domain': {'type': 'string'}}),
    _tool('chemlab_describe', "An instrument's agent-visible capabilities (arguments with types, units, ranges) and state slots.",
          {'instrument': INST}, ['instrument']),
    _tool('chemlab_help', 'One capability in full, with an example call; capability "panel" explains the front-panel gestures.',
          {'instrument': INST, 'capability': {'type': 'string'}}, ['instrument', 'capability']),
    _tool('chemlab_read', 'Observations with units and quality (all agent-visible slots, or the ones named).',
          {'instrument': INST, 'slots': {'type': 'array', 'items': {'type': 'string'}}}, ['instrument']),
    _tool('chemlab_call', 'Invoke one capability. Arguments exactly as the contract defines them (SI units); the instrument checks them.',
          {'instrument': INST, 'capability': {'type': 'string'}, 'arguments': {'type': 'object'},
           'request_id': {'type': 'string', 'description': 'repeat it to get the first reply again instead of a second execution'}},
          ['instrument', 'capability']),
    _tool('chemlab_write', 'Set a writable state slot.', {'instrument': INST, 'slot': {'type': 'string'}, 'value': {}},
          ['instrument', 'slot', 'value']),
    _tool('chemlab_status', 'Your asynchronous operations, or one of them (op-N).', {'operation': {'type': 'string'}}),
    _tool('chemlab_cancel', 'Cancel an operation where the instrument supports it.', {'operation': {'type': 'string'}}, ['operation']),
    _tool('chemlab_wait', 'Let simulated time pass: a number of seconds, or until an operation ends (max seconds). The only way time moves.',
          {'seconds': {'type': 'number'}, 'operation': {'type': 'string'}, 'max': {'type': 'number'}}),
    _tool('chemlab_panel', 'A front-panel gesture: press KEY [hold], turn KNOB by detents, switch SW to position, touch DISPLAY at x,y [to].',
          {'instrument': INST, 'gesture': {'type': 'string', 'enum': ['press', 'turn', 'switch', 'touch']},
           'target': {'type': 'string', 'description': 'key, knob, switch or display id'},
           'detents': {'type': 'integer'}, 'position': {'type': 'string'}, 'x': {'type': 'integer'}, 'y': {'type': 'integer'},
           'hold': {'type': 'number'}, 'to': {'type': 'array', 'items': {'type': 'integer'}, 'minItems': 2, 'maxItems': 2},
           'with': {'type': 'array', 'items': {'type': 'string'}}}, ['instrument', 'gesture', 'target']),
    _tool('chemlab_screen', 'Capture a display as a PNG (returned as an image, also saved in the work dir).',
          {'instrument': INST, 'display': {'type': 'string'}, 'file': {'type': 'string'}}, ['instrument']),
    _tool('chemlab_log', 'Your past requests and replies.', {'last': {'type': 'integer'}, 'full': {'type': 'boolean'}}),
]


def argv_for(name, a):
    """Tool call -> the chemlab argv (the only thing this shell does)."""
    inst = a.get('instrument')
    if name == 'chemlab_list':
        v = ['list'] + (['--domain', a['domain']] if a.get('domain') else [])
    elif name == 'chemlab_describe':
        v = ['describe', inst]
    elif name == 'chemlab_help':
        v = ['help', inst, a['capability']]
    elif name == 'chemlab_read':
        v = ['read', inst] + list(a.get('slots') or [])
    elif name == 'chemlab_call':
        v = ['call', inst, a['capability'], '--args', json.dumps(a.get('arguments') or {})]
        if a.get('request_id'):
            v += ['--request-id', a['request_id']]
    elif name == 'chemlab_write':
        val = a['value']
        v = ['write', inst, a['slot'], val if isinstance(val, str) else json.dumps(val)]
    elif name == 'chemlab_status':
        v = ['status'] + ([a['operation']] if a.get('operation') else [])
    elif name == 'chemlab_cancel':
        v = ['cancel', a['operation']]
    elif name == 'chemlab_wait':
        if a.get('operation'):
            v = ['wait', a['operation']] + (['--max', repr(float(a['max']))] if a.get('max') is not None else [])
        else:
            v = ['wait', repr(float(a.get('seconds', 0)))]
    elif name == 'chemlab_panel':
        g = a['gesture']
        v = ['panel', inst, g, a['target']]
        if g == 'turn':
            v.append(str(a.get('detents', 0)))
        elif g == 'switch':
            v.append(str(a.get('position', '')))
        elif g == 'touch':
            v += [str(a.get('x', '')), str(a.get('y', ''))]
            if a.get('to'):
                v += ['--to', str(a['to'][0]), str(a['to'][1])]
        if a.get('hold') is not None:
            v += ['--hold', repr(float(a['hold']))]
        for k in a.get('with') or []:
            v += ['--with', k]
    elif name == 'chemlab_screen':
        v = ['screen', inst] + ([a['display']] if a.get('display') else []) + (['-o', a['file']] if a.get('file') else [])
    elif name == 'chemlab_log':
        v = ['log', '--last', str(int(a.get('last', 20)))] + (['--full'] if a.get('full') else [])
    else:
        raise KeyError(name)
    if a.get('json') or name == 'chemlab_screen':
        v.append('--json')
    return ['chemlab'] + [str(x) for x in v]


class CoreTransport:
    """In-process: the core's handle() with a fixed caller."""

    def __init__(self, core, caller='agent'):
        self.core, self.caller = core, caller
        self.work = Path(core.work_dir)

    def send(self, argv):
        r = self.core.handle(argv, self.caller)
        return r.exit_code, r.out, r.err


class SocketTransport:
    """Through the lab's unix socket with a token, exactly like the C client (wire: one JSON line; CHEMLAB/1 header + bytes)."""

    def __init__(self, path, token, work=None):
        self.path, self.token = path, token
        self.work = Path(work) if work else None

    def send(self, argv):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        here = os.getcwd()
        try:
            os.chdir(os.path.dirname(self.path) or '.')                      # socket paths are limited to 108 bytes
            s.connect(os.path.basename(self.path))
        except OSError as exc:
            return 5, '', 'error[service/UNREACHABLE]: cannot connect to the chemlab service (%s).\n' % exc
        finally:
            os.chdir(here)
        with s:
            s.sendall((json.dumps({'token': self.token, 'argv': argv}) + '\n').encode())
            s.shutdown(socket.SHUT_WR)
            data = b''
            while True:
                chunk = s.recv(1 << 16)
                if not chunk:
                    break
                data += chunk
        head, _, body = data.partition(b'\n')
        _, code, nout, nerr = head.decode().split()
        nout, nerr = int(nout), int(nerr)
        return int(code), body[:nout].decode('utf-8', 'replace'), body[nout:nout + nerr].decode('utf-8', 'replace')


def call_tool(transport, name, arguments):
    try:
        argv = argv_for(name, arguments or {})
    except KeyError as exc:
        return {'content': [{'type': 'text', 'text': 'missing or unknown: %s' % exc}], 'isError': True}
    code, out, err = transport.send(argv)
    text = out if code == 0 else (err or out)
    content = [{'type': 'text', 'text': text}]
    if name == 'chemlab_screen' and code == 0 and transport.work is not None:
        try:
            rel = json.loads(out).get('file')
            png = (transport.work / rel).read_bytes()
            content.append({'type': 'image', 'data': base64.b64encode(png).decode('ascii'), 'mimeType': 'image/png'})
        except (ValueError, OSError, TypeError):
            pass
    return {'content': content, 'isError': code != 0}


def handle_message(transport, msg):
    """One JSON-RPC message -> response dict, or None for notifications."""
    mid, method, params = msg.get('id'), msg.get('method'), msg.get('params') or {}
    if mid is None:
        return None                                                          # notifications (initialized, cancelled, ...)
    if method == 'initialize':
        result = {'protocolVersion': params.get('protocolVersion') or PROTOCOL_VERSION, 'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'chemlab', 'version': '0.2.0'},
                  'instructions': 'Simulated lab instruments. Start with chemlab_list, then chemlab_describe. Values are SI units as '
                                  'each contract defines them. Time only moves with chemlab_wait.'}
    elif method == 'ping':
        result = {}
    elif method == 'tools/list':
        result = {'tools': TOOLS}
    elif method == 'tools/call':
        result = call_tool(transport, params.get('name'), params.get('arguments'))
    else:
        return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32601, 'message': 'method not found: %s' % method}}
    return {'jsonrpc': '2.0', 'id': mid, 'result': result}


def serve(transport, inp=None, out=None):
    inp, out = inp or sys.stdin, out or sys.stdout
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            out.write(json.dumps({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'parse error'}}) + '\n')
            out.flush()
            continue
        resp = handle_message(transport, msg)
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=False) + '\n')
            out.flush()


def serve_stdio(core, caller='agent'):
    serve(CoreTransport(core, caller))


def main():
    path, token = os.environ.get('CHEMLAB_SOCKET'), os.environ.get('CHEMLAB_TOKEN')
    if not path or not token:
        sys.stderr.write('set CHEMLAB_SOCKET and CHEMLAB_TOKEN (and CHEMLAB_WORK for screen images)\n')
        return 2
    serve(SocketTransport(path, token, os.environ.get('CHEMLAB_WORK')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
