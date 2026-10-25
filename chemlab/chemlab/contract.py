"""What the core service takes from the asset contract: capabilities, their input schemas (types, ranges, enums, units),
availability and visibility.  The verb table (flex_verbs.py) is checked against this at start-up, and every backend call is
validated against the capability's input schema before it is sent.
"""
import json
import math
from pathlib import Path

# Capabilities that exist but are not offered as verbs, with the reason given to an agent that asks for them.
NOT_A_VERB = {
    'panel.touch': 'touchscreen gesture path for robots/vision; every touchscreen function has its own verb',
    'panel.switch': 'rear power rocker gesture; use `power on|off`',
    'panel.capture': 'screen image for vision pipelines; not part of the command line',
    'state.read': 'wrapped by `status`',
    'files.download': 'needs a USB drive inserted by the operator (not in this pilot)',
    'quick_transfer.create': 'touchscreen wizard with a fixed B2/C2/D2 layout; use `transfer`',
    'run.lpc': 'Labware Position Check: an operator jogs the pipette and confirms the alignment by eye (not for agents in this pilot)',
    'run.confirm_placements': 'operator confirmation on the touchscreen that labware is placed; not needed to start a run',
    'run.close': 'touchscreen housekeeping after a run; a new run replaces a finished one',
}


class Capability:
    def __init__(self, cid, spec):
        self.id = cid
        self.spec = spec
        self.availability = spec.get('availability')
        self.visibility = spec.get('visibility')
        self.kind = spec.get('kind')
        self.input_schema = spec.get('input_schema') or {'type': 'object', 'properties': {}}

    @property
    def agent_usable(self):
        return self.availability == 'implemented' and self.visibility == 'agent'

    def hidden_reason(self):
        if self.availability != 'implemented':
            return 'not supported by this instrument twin (%s)' % self.availability
        if self.visibility != 'agent':
            return '%s-only capability' % self.visibility
        return NOT_A_VERB.get(self.id)


class Contract:
    def __init__(self, path):
        self.path = Path(path)
        data = json.loads(self.path.read_text())
        self.asset_type_id = data['asset_type_id']
        self.asset_version = data.get('asset_version')
        self.capabilities = {cid: Capability(cid, spec) for cid, spec in data['capabilities'].items()}
        self.state_slots = data['state_slots']

    def agent_state_ids(self):
        return sorted(sid for sid, s in self.state_slots.items() if s.get('visibility') == 'agent')

    def hidden(self):
        return {cid: c.hidden_reason() for cid, c in self.capabilities.items() if c.hidden_reason()}

    def check(self, cid, args):
        """Validate backend arguments against the contract.  Returns a list of problems (empty = valid)."""
        cap = self.capabilities.get(cid)
        if cap is None:
            return ['capability %s is not in the contract' % cid]
        if not cap.agent_usable:
            return ['capability %s is %s' % (cid, cap.hidden_reason())]
        return validate(cap.input_schema, args, cid)


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate(schema, value, where='value'):
    """A small JSON-schema subset: type, enum, const, minimum/maximum, min/maxLength, properties/required/
    additionalProperties, items/minItems/maxItems, anyOf."""
    problems = []
    if 'anyOf' in schema:
        if all(validate(s, value, where) for s in schema['anyOf']):
            problems.append('%s does not match any allowed form' % where)
        return problems
    t = schema.get('type')
    checks = {'object': lambda v: isinstance(v, dict), 'array': lambda v: isinstance(v, list), 'string': lambda v: isinstance(v, str),
              'boolean': lambda v: isinstance(v, bool), 'number': _is_number,
              'integer': lambda v: _is_number(v) and float(v).is_integer(), 'null': lambda v: v is None}
    if t and not checks[t](value):
        return ['%s must be %s' % (where, t)]
    if 'enum' in schema and value not in schema['enum']:
        problems.append('%s must be one of %s' % (where, ', '.join(map(str, schema['enum']))))
    if 'const' in schema and value != schema['const']:
        problems.append('%s must be %r' % (where, schema['const']))
    if _is_number(value):
        if 'minimum' in schema and value < schema['minimum']:
            problems.append('%s is below the minimum %g' % (where, schema['minimum']))
        if 'maximum' in schema and value > schema['maximum']:
            problems.append('%s is above the maximum %g' % (where, schema['maximum']))
    if isinstance(value, str):
        if len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', 1 << 30):
            problems.append('%s has the wrong length' % where)
    if isinstance(value, dict) and t == 'object':
        props = schema.get('properties', {})
        for k in schema.get('required', []):
            if k not in value:
                problems.append('%s.%s is required' % (where, k))
        if schema.get('additionalProperties') is False:
            problems += ['%s.%s is not a known argument' % (where, k) for k in value if k not in props]
        for k, v in value.items():
            if k in props:
                problems += validate(props[k], v, '%s.%s' % (where, k))
    if isinstance(value, list) and t == 'array':
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', 1 << 30):
            problems.append('%s has the wrong number of items' % where)
        if 'items' in schema:
            for n, v in enumerate(value):
                problems += validate(schema['items'], v, '%s[%d]' % (where, n))
    return problems
