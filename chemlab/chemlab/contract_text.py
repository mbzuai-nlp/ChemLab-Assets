"""English help generated from the structure of an asset contract (ids, kinds, schemas, units, ranges, enums, preconditions,
effects, errors, execution).  The contracts' prose is Chinese (function_zh, meaning_zh); it is shown as it is, labelled
"(zh)", next to the generated English.  No manual text is used.
"""
import json
import shlex

KIND_EN = {'device_command': 'device command', 'configuration': 'configuration', 'query': 'query',
           'robot_skill': 'physical action (hands or robot)', 'simulation': 'simulation set-up', 'analysis': 'data analysis'}
ENUM_SHOW = 8


def unit_of(schema):
    u = schema.get('x-unit') if isinstance(schema, dict) else None
    return None if u in (None, '', '1') else u


def _types(schema):
    t = schema.get('type')
    if isinstance(t, list):
        return t
    if t:
        return [t]
    out = []
    for key in ('anyOf', 'oneOf'):
        for s in schema.get(key) or []:
            out += _types(s)
    return out


def _num(v):
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return repr(v)
    return json.dumps(v)


def type_short(schema):
    """Compact type for describe: NUM(K, 273.15..583.15), INT(0..10), BOOL, TEXT, {a|b|c}, JSON-LIST, JSON-OBJECT, A|null."""
    if not isinstance(schema, dict):
        return 'JSON'
    for key in ('anyOf', 'oneOf'):
        if schema.get(key):
            parts = []
            for s in schema[key]:
                p = type_short(s)
                if p not in parts:
                    parts.append(p)
            return '|'.join(parts)
    t = schema.get('type')
    if isinstance(t, list):
        return '|'.join(type_short(dict(schema, type=x)) for x in t)
    if 'const' in schema:
        return json.dumps(schema['const'])
    if 'enum' in schema:
        vals = [str(v) if isinstance(v, str) else json.dumps(v) for v in schema['enum']]
        if len(vals) > ENUM_SHOW:
            return '{%s|...(%d)}' % ('|'.join(vals[:ENUM_SHOW - 2]), len(vals))
        return '{%s}' % '|'.join(vals)
    if t in ('number', 'integer'):
        bits = []
        u = unit_of(schema)
        if u:
            bits.append(u)
        lo = schema.get('minimum', schema.get('exclusiveMinimum'))
        hi = schema.get('maximum', schema.get('exclusiveMaximum'))
        if lo is not None or hi is not None:
            rng = '%s..%s' % ('' if lo is None else _num(lo), '' if hi is None else _num(hi))
            if 'exclusiveMinimum' in schema:
                rng = '>' + rng
            bits.append(rng)
        return ('NUM' if t == 'number' else 'INT') + ('(%s)' % ', '.join(bits) if bits else '')
    if t == 'boolean':
        return 'BOOL'
    if t == 'string':
        return 'TEXT'
    if t == 'null':
        return 'null'
    if t == 'array':
        return 'JSON-LIST'
    if t == 'object':
        return 'JSON-OBJECT'
    return 'JSON'


def type_long(schema, indent='    '):
    """Lines describing a schema in full (nested objects and list items included)."""
    lines = []
    if not isinstance(schema, dict):
        return [indent + 'any JSON value']
    head = type_short(schema)
    t = schema.get('type')
    extra = []
    if t == 'string':
        if 'minLength' in schema or 'maxLength' in schema:
            extra.append('length %s..%s' % (schema.get('minLength', 0), schema.get('maxLength', '')))
        if 'pattern' in schema:
            extra.append('pattern %s' % schema['pattern'])
        if 'enum' in schema and len(schema['enum']) > ENUM_SHOW:
            head = 'one of: ' + ', '.join(map(str, schema['enum']))
    if t == 'array':
        if 'minItems' in schema or 'maxItems' in schema:
            extra.append('%s..%s items' % (schema.get('minItems', 0), schema.get('maxItems', '')))
        if schema.get('uniqueItems'):
            extra.append('unique')
    if schema.get('x-reference-conditions'):
        extra.append('reference conditions: %s' % json.dumps(schema['x-reference-conditions'], ensure_ascii=False))
    if schema.get('description'):
        extra.append(str(schema['description']))
    lines.append(indent + head + (' - ' + '; '.join(extra) if extra else ''))
    if t == 'array' and isinstance(schema.get('items'), dict):
        lines.append(indent + 'each item:')
        lines += type_long(schema['items'], indent + '  ')
    if t == 'object' and schema.get('properties'):
        req = set(schema.get('required') or [])
        for k, s in schema['properties'].items():
            lines.append('%s  %s%s:' % (indent, k, '' if k in req else ' (optional)'))
            lines += type_long(s, indent + '    ')
    return lines


def arg_signature(input_schema):
    """'--temperature_k NUM(K, 273.15..583.15) [--hold NUM(s)]' for describe."""
    props = (input_schema or {}).get('properties') or {}
    req = set((input_schema or {}).get('required') or [])
    parts = []
    for k, s in props.items():
        p = '--%s %s' % (k, type_short(s))
        parts.append(p if k in req else '[%s]' % p)
    return ' '.join(parts) if parts else '(no arguments)'


def value_text(v):
    if isinstance(v, str):
        return shlex.quote(v)
    return shlex.quote(json.dumps(v, ensure_ascii=False))


def example_call(inst_id, cap_id, example):
    """Shell lines that call the capability with the contract's example arguments."""
    ex = example if isinstance(example, dict) else {}
    lines = []
    if all(isinstance(v, (str, int, float, bool)) or v is None for v in ex.values()):
        lines.append(' '.join(['chemlab call', inst_id, cap_id] + ['--%s %s' % (k, value_text(v)) for k, v in ex.items()]))
    lines.append('chemlab call %s %s --args %s' % (inst_id, cap_id, shlex.quote(json.dumps(ex, ensure_ascii=False))))
    return lines


def summary_en(cap):
    """One line of English from the structure: kind, execution, what it changes."""
    kind = KIND_EN.get(cap.get('kind'), cap.get('kind') or 'capability')
    ex = cap.get('execution') or {}
    bits = [kind]
    if ex.get('mode') and ex.get('mode') != 'synchronous':
        bits.append('%s, returns an operation id' % ex['mode'])
    if ex.get('cancellation') not in (None, 'unsupported'):
        bits.append('cancellable')
    return ', '.join(bits)
