"""Device layer of the Anton Paar touch-screen assets (front-panel group G07: Abbemat Advanced 5201, DMA 5002, MCR 503, Monowave 400,
Ultrapyc 5000): what the instrument software keeps besides the measuring physics - settings, clock, users and logon, message list,
favorites, methods and quick settings, sample list, data memory (measured / check / adjustment data, audit trail), user functions, and the
front-panel host protocol of the generic panel engine (_panel_ui.py).

Model: the M series instrument software (Abbemat 350/550 manual D22IB001PL-G, 'abm p.N'; Reference Guide General Software Functions
XPAIB001EN-V, 'gsf p.N').  Each runtime passes a profile (quantities, method fields, factory methods, checks, hooks into its physics) and
keeps its own physics; this module never touches the physics directly, only through the profile hooks.

State lives in plain dicts on the host (host.ap, host.panel_state), so copies and comparisons of a runtime stay value-based (the runtimes'
own tests compare copy.deepcopy(host.__dict__) with host.__dict__).  Every command validates on a candidate copy and commits only when it
succeeds: a refusal leaves the device unchanged."""
from __future__ import annotations

import ast
import copy
import datetime
import hashlib
import math
import re
import tempfile
from pathlib import Path

from asset_runtime.mt_hx204 import ActionRejected

EPOCH0 = datetime.datetime(2026, 1, 5, 8, 0, 0)                            # device clock at simulation time 0 (assumption; clock.set changes it)
DEVICE_CODES = ['POWER_OFF', 'LOGON_REQUIRED', 'PERMISSION_DENIED', 'BUSY', 'WRONG_PASSWORD', 'ACCOUNT_LOCKED', 'IN_USE', 'NOT_FOUND',
                'MEMORY_FULL', 'MANDATORY_FIELD', 'NOT_EXPORTED', 'NO_DEVICE', 'NO_PROCEDURE', 'CHECK_OVERDUE', 'NOT_ALLOWED']


def need(cond, code='PRECONDITION_FAILED'):
    if not cond:
        raise ActionRejected(code)


# ---------------------------------------------------------------- argument schemas (subset shared with asset_tool.lint_schema)
def num(unit, lo=None, hi=None, integer=False):
    s = {'type': 'integer' if integer else 'number', 'x-unit': unit}
    if lo is not None:
        s['minimum'] = lo
    if hi is not None:
        s['maximum'] = hi
    return s


def obj(props, optional=(), extra=False):
    return {'type': 'object', 'properties': props, 'required': [k for k in props if k not in optional], 'additionalProperties': extra}


def nullable(s):
    return {'anyOf': [s, {'type': 'null'}]}


STR = {'type': 'string'}
NAME = {'type': 'string', 'minLength': 1, 'maxLength': 50}
BOOL = {'type': 'boolean'}
ANYOBJ = {'type': 'object'}
ACCEPTED = obj({'accepted': BOOL})


def check(schema, v, path='$'):
    """Validate a command argument against the schema subset used here (type incl. integer / null, enum, bounds, lengths, pattern, required,
    additionalProperties, items, anyOf); raises INVALID_ARGUMENT."""
    if 'anyOf' in schema:
        for sub in schema['anyOf']:
            try:
                check(sub, v, path)
                return
            except ActionRejected:
                pass
        raise ActionRejected('INVALID_ARGUMENT')
    t = schema.get('type')
    ok = {'object': lambda x: isinstance(x, dict), 'string': lambda x: isinstance(x, str), 'boolean': lambda x: type(x) is bool,
          'array': lambda x: isinstance(x, list), 'null': lambda x: x is None,
          'integer': lambda x: isinstance(x, int) and not isinstance(x, bool),
          'number': lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)}
    need(t is None or ok[t](v), 'INVALID_ARGUMENT')
    if 'enum' in schema:
        need(any(v == e and type(v) is type(e) or (isinstance(v, (int, float)) and not isinstance(v, bool) and isinstance(e, (int, float))
                                                    and not isinstance(e, bool) and v == e) for e in schema['enum']), 'INVALID_ARGUMENT')
    if t in ('number', 'integer'):
        need(schema.get('minimum', -math.inf) <= v <= schema.get('maximum', math.inf), 'INVALID_ARGUMENT')
    if t == 'string':
        need(schema.get('minLength', 0) <= len(v) <= schema.get('maxLength', 10 ** 6), 'INVALID_ARGUMENT')
        if 'pattern' in schema:
            need(re.search(schema['pattern'], v) is not None, 'INVALID_ARGUMENT')
    if t == 'array':
        need(schema.get('minItems', 0) <= len(v) <= schema.get('maxItems', 10 ** 6), 'INVALID_ARGUMENT')
        for x in v:
            check(schema.get('items', {}), x, path)
    if t == 'object':
        need(set(schema.get('required', [])) <= set(v), 'INVALID_ARGUMENT')
        props = schema.get('properties', {})
        for k, x in v.items():
            sub = props.get(k, schema.get('additionalProperties', True))
            need(sub is not False, 'INVALID_ARGUMENT')
            if isinstance(sub, dict):
                check(sub, x, f'{path}.{k}')


# ---------------------------------------------------------------- settings tables
def t_bool(key, label, default, ev, section, **kw):
    return dict(key=key, label=label, type='bool', default=default, ev=ev, section=section, **kw)


def t_enum(key, label, options, default, ev, section, **kw):
    return dict(key=key, label=label, type='enum', options=list(options), default=default, ev=ev, section=section, **kw)


def t_num(key, label, lo, hi, default, ev, section, unit=None, integer=False, **kw):
    return dict(key=key, label=label, type='int' if integer else 'num', min=lo, max=hi, default=default, ev=ev, section=section, unit=unit, **kw)


def t_text(key, label, lo, hi, default, ev, section, **kw):
    return dict(key=key, label=label, type='text', min_len=lo, max_len=hi, default=default, ev=ev, section=section, **kw)


def value_ok(f, v):
    t = f['type']
    if t == 'bool':
        return type(v) is bool
    if t == 'enum':
        return any(v == o and type(v) is type(o) for o in f['options'])
    if t in ('int', 'num'):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or (t == 'int' and not float(v).is_integer()):
            return False
        return f['min'] - 1e-9 <= v <= f['max'] + 1e-9
    if t == 'text':
        return isinstance(v, str) and f['min_len'] <= len(v) <= f['max_len'] and (not f.get('pattern') or re.search(f['pattern'], v) is not None)
    return False


def field_schema(f):
    t = f['type']
    if t == 'bool':
        return dict(BOOL)
    if t == 'enum':
        kinds = {type(o) for o in f['options']}
        s = {'enum': list(f['options'])}
        if kinds == {str}:
            s['type'] = 'string'
        return s
    if t in ('int', 'num'):
        return num(f.get('unit') or '1', f['min'], f['max'], integer=t == 'int')
    s = {'type': 'string', 'minLength': f['min_len'], 'maxLength': f['max_len']}
    if f.get('pattern'):
        s['pattern'] = f['pattern']
    return s


def settings_schema(fields, min_props=1):
    return {'type': 'object', 'properties': {f['key']: field_schema(f) for f in fields}, 'additionalProperties': False, 'minProperties': min_props}


IP = dict(pattern=r'^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$')
LANGUAGES = ('English', 'Deutsch', 'Français', 'Español', 'Italiano')
DATA_FORMATS = ('English (United States)', 'English (United Kingdom)', 'Deutsch (Deutschland)', 'Français (France)')
KEYBOARDS = ('US', 'German', 'French')
NAME_FIELDS = ('None', 'Date and Time', 'User Name', 'Sample Number', 'Method')

# Instrument settings common to the M series software (ev = (Abbemat manual page, general-software-functions page)); 'right' is the user
# right needed to change the section (abm p.47, Appendix F p.139-141: Setup is Administrator-only, Methods Manager / Administrator).
MSERIES_SETTINGS = [
    # Setup > Control Panel
    t_num('control.brightness', 'Display Brightness', 20, 100, 100, ('p.34', 'p.9'), 'brightness', unit='%', integer=True,
          assumption='factory brightness not given; 100 % assumed'),
    t_bool('control.screen_saver', 'Automatically switch off display after', True, ('p.34', 'p.9'), 'screen_saver',
           assumption='screen saver active by default (not stated)'),
    t_num('control.screen_saver_min', 'Minutes', 1, 300, 30, ('p.34', 'p.9'), 'screen_saver', unit='min', integer=True,
          visible=('control.screen_saver', True), assumption='factory time not given; 30 min assumed'),
    t_bool('control.beep_touch', 'Feedback beep on screen action', True, ('p.35', 'p.9'), 'beeps'),
    t_bool('control.beep_measurement', 'Beep when a measurement is finished', True, ('p.35', 'p.9'), 'beeps'),
    t_enum('region.language', 'Language', LANGUAGES, 'English', ('p.41', 'p.13'), 'regional', reboot=True),
    t_enum('region.data_format', 'Data Format', DATA_FORMATS, 'English (United States)', ('p.41', 'p.13'), 'regional', reboot=True),
    t_enum('region.keyboard', 'Keyboard Layout', KEYBOARDS, 'US', ('p.41', 'p.13'), 'regional', reboot=True),
    t_enum('units.temperature', 'Input unit for temperature', ('°C', '°F', 'K'), '°C', ('p.42', 'p.13'), 'input_units'),
    t_bool('network.dhcp', 'Obtain an IP address automatically (DHCP)', True, ('p.42', 'p.14'), 'network'),
    t_text('network.ip', 'IP Address', 7, 15, '192.168.0.100', ('p.42', 'p.14'), 'network', visible=('network.dhcp', False), **IP),
    t_text('network.subnet', 'Subnet Mask', 7, 15, '255.255.255.0', ('p.42', 'p.14'), 'network', visible=('network.dhcp', False), **IP),
    t_text('network.gateway', 'Default Gateway', 7, 15, '192.168.0.1', ('p.42', 'p.14'), 'network', visible=('network.dhcp', False), **IP),
    t_text('network.dns1', 'Primary DNS Server', 7, 15, '0.0.0.0', ('p.42', 'p.14'), 'network', page2=True, **IP),
    t_text('network.dns2', 'Secondary DNS Server', 7, 15, '0.0.0.0', ('p.42', 'p.14'), 'network', page2=True, **IP),
    t_text('instrument.name', 'Instrument name', 0, 30, '', ('p.42', 'p.15'), 'instrument_name'),
    t_text('instrument.location', 'Instrument location', 0, 30, '', ('p.42', 'p.15'), 'instrument_name'),
    t_bool('keyboard.barcode_sample_list', 'Use a bar code scanner to edit the sample list', False, ('p.37', 'p.11'), 'keyboard_barcode'),
    t_text('smtp.host', 'Host', 0, 64, '', ('p.51-52', 'p.20'), 'smtp'),
    t_num('smtp.port', 'Port', 1, 65535, 25, ('p.51-52', 'p.20'), 'smtp', integer=True, assumption='factory port not given; 25 assumed'),
    t_text('smtp.user', 'User name', 0, 64, '', ('p.51-52', 'p.20'), 'smtp'),
    t_text('smtp.password', 'Password', 0, 64, '', ('p.51-52', 'p.20'), 'smtp', secret=True),
    t_text('smtp.from', 'From address', 0, 64, '', ('p.51-52', 'p.20'), 'smtp'),
    t_bool('smtp.ssl', 'Enable SSL', False, ('p.51-52', 'p.20'), 'smtp'),
    t_enum('lims.baud', 'Baud rate', (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200), 9600, ('p.110', 'p.45'), 'rs232_lims'),
    t_enum('lims.data_bits', 'Data bits', (7, 8), 8, ('p.110', 'p.45'), 'rs232_lims'),
    t_enum('lims.parity', 'Parity', ('None', 'Even', 'Odd'), 'None', ('p.110', 'p.45'), 'rs232_lims'),
    t_enum('lims.stop_bits', 'Stop bits', (1, 2), 1, ('p.110', 'p.45'), 'rs232_lims'),
    t_enum('lims.handshake', 'Handshake', ('None', 'Hardware', 'Software'), 'None', ('p.110', 'p.45'), 'rs232_lims'),
    # Setup > Printout Settings
    t_bool('printout.auto', 'After each measurement', False, ('p.44', 'p.16'), 'auto_printout'),
    t_bool('printout.sub_samples', 'Enable printing of sub samples', False, ('p.44', 'p.16'), 'auto_printout'),
    t_text('printout.printer', 'Printer', 0, 50, 'PDF Export', ('p.44-45', 'p.16'), 'auto_printout'),
    t_text('printout.report', 'Report', 0, 50, 'Report 1', ('p.44-45', 'p.16'), 'auto_printout'),
    t_enum('printout.export_name', 'Export File Name', ('Time', 'Sample Name', 'Method'), 'Time', ('p.45', 'p.16'), 'auto_printout'),
    t_enum('printout.header_logo', 'Header logo', ('None', 'Anton Paar'), 'Anton Paar', ('p.44', 'p.16'), 'logos',
           assumption='internal logo list holds the factory logo only'),
    t_enum('printout.background_logo', 'Background logo', ('None', 'Anton Paar'), 'None', ('p.44', 'p.16'), 'logos'),
    t_text('printout.company', 'Name', 0, 40, '', ('p.44', 'p.16'), 'name_address'),
    t_text('printout.address', 'Address', 0, 80, '', ('p.44', 'p.16'), 'name_address'),
    # Setup > Measuring System Settings > Sample List / Sample Settings
    t_enum('samples.mode', 'Sample List Mode', ('No Sample List', 'Sample List'), 'No Sample List', ('p.46', 'p.16-17'), 'sample_naming'),
    t_text('samples.prefix', 'Sample Name Prefix', 0, 20, '', ('p.45-46', 'p.16-17'), 'sample_naming'),
    t_enum('samples.prefix_field', 'Prefix data field', NAME_FIELDS, 'None', ('p.45', 'p.16'), 'sample_naming'),
    t_text('samples.postfix', 'Sample Name Postfix', 0, 20, '', ('p.45-46', 'p.16-17'), 'sample_naming'),
    t_enum('samples.postfix_field', 'Postfix data field', NAME_FIELDS, 'None', ('p.45', 'p.16'), 'sample_naming'),
    t_bool('samples.field1', 'User-defined Data Field 1', False, ('p.46', 'p.17'), 'data_fields'),
    t_text('samples.field1_name', 'Name of data field 1', 1, 20, 'Data Field 1', ('p.46', 'p.17'), 'data_fields', visible=('samples.field1', True)),
    t_bool('samples.field2', 'User-defined Data Field 2', False, ('p.46', 'p.17'), 'data_fields'),
    t_text('samples.field2_name', 'Name of data field 2', 1, 20, 'Data Field 2', ('p.46', 'p.17'), 'data_fields', visible=('samples.field2', True)),
    t_bool('samples.field3', 'User-defined Data Field 3', False, ('p.46', 'p.17'), 'data_fields'),
    t_text('samples.field3_name', 'Name of data field 3', 1, 20, 'Data Field 3', ('p.46', 'p.17'), 'data_fields', visible=('samples.field3', True)),
    t_bool('samples.warn_no_name', 'Warn if sample list contains a sample without a name', False, ('p.46', 'p.17'), 'list_warnings'),
    t_bool('samples.warn_temperatures', 'Warn if sample list contains methods with different measuring temperatures', False, ('p.46', 'p.17'),
           'list_warnings'),
    t_bool('samples.mandatory_name', 'Sample name is mandatory', False, ('p.46', 'p.17'), 'mandatory'),
    t_bool('samples.mandatory_field1', 'Data field 1 is mandatory', False, ('p.46', 'p.17'), 'mandatory'),
    t_bool('samples.mandatory_field2', 'Data field 2 is mandatory', False, ('p.46', 'p.17'), 'mandatory'),
    t_bool('samples.mandatory_field3', 'Data field 3 is mandatory', False, ('p.46', 'p.17'), 'mandatory'),
    # Data memory settings
    t_bool('memory.auto_delete', 'Automatically delete old measured data', False, ('p.87', 'p.25'), 'memory_settings'),
    t_bool('memory.auto_delete_checks', 'Automatically delete old check data', False, ('p.87', 'p.25'), 'check_memory_settings'),
    # Setup > System Security
    t_enum('security.level', 'Security Level', ('Low', '21 CFR Part 11', 'User-defined'), 'Low', ('p.52-53', 'p.29'), 'security_level'),
    t_bool('security.audit_trail', 'Audit Trail active', False, ('p.57', 'p.32'), 'audit_trail'),
    t_bool('security.audit_reminder', 'Audit Trail Backup Reminder', False, ('p.57', 'p.32'), 'audit_trail'),
    t_num('security.audit_reminder_days', 'Reminder interval', 1, 365, 7, ('p.53', 'p.29'), 'audit_trail', unit='days', integer=True,
          visible=('security.audit_reminder', True)),
    t_bool('security.esign', 'Electronic Signature', False, ('p.59', 'p.34'), 'esign'),
    t_bool('security.esign_password', 'Electronic Signature requires Password', False, ('p.59', 'p.34'), 'esign', visible=('security.esign', True)),
    t_enum('security.esign_levels', 'Electronic Signature Levels', ('3 roles', '2 roles'), '3 roles', ('p.58', 'p.34'), 'esign',
           visible=('security.esign', True)),
    t_bool('security.esign_substitute', 'Allow substitute signing', True, ('p.58', 'p.34'), 'esign', visible=('security.esign', True)),
    t_bool('security.auto_logoff', 'Auto logoff', False, ('p.54', 'p.30'), 'logon'),
    t_num('security.auto_logoff_min', 'Auto logoff time', 1, 60, 60, ('p.54', 'p.30'), 'logon', unit='min', integer=True,
          visible=('security.auto_logoff', True)),
    t_bool('security.pw_expiry', 'Password expires', False, ('p.54', 'p.30'), 'logon'),
    t_num('security.pw_expiry_days', 'Password expiry time', 1, 60, 60, ('p.54', 'p.30'), 'logon', unit='days', integer=True,
          visible=('security.pw_expiry', True)),
    t_bool('security.pw_min_active', 'Minimum password length', False, ('p.54', 'p.30'), 'logon'),
    t_num('security.pw_min_length', 'Password length', 6, 30, 6, ('p.54', 'p.30'), 'logon', integer=True, visible=('security.pw_min_active', True)),
    t_bool('security.increased', 'Increased Security', False, ('p.54-55', 'p.31'), 'increased_security'),
    t_bool('security.disable_usb', 'Disable USB memory devices', False, ('p.56', 'p.31'), 'advanced_security'),
    t_bool('security.service_password', 'Service logon requires customer password', False, ('p.56', 'p.31'), 'advanced_security'),
]
SECTION_RIGHT = {'brightness': 'setup_control_panel', 'screen_saver': 'setup_control_panel', 'beeps': 'setup_control_panel',
                 'regional': 'setup_control_panel', 'input_units': 'setup_control_panel', 'network': 'setup_control_panel',
                 'instrument_name': 'setup_control_panel', 'keyboard_barcode': 'setup_control_panel', 'smtp': 'setup_control_panel',
                 'rs232_lims': 'setup_control_panel', 'auto_printout': 'setup_printout', 'logos': 'setup_printout', 'name_address': 'setup_printout',
                 'sample_naming': 'setup_measuring', 'data_fields': 'setup_measuring', 'list_warnings': 'setup_measuring', 'mandatory': 'setup_measuring',
                 'memory_settings': 'data_settings', 'check_memory_settings': 'data_settings', 'security_level': 'setup_security',
                 'audit_trail': 'setup_security', 'esign': 'setup_security', 'logon': 'setup_security', 'increased_security': 'setup_security',
                 'advanced_security': 'setup_security'}
SECURITY_LEVELS = {                                                      # abm Table 9.1 p.53 (gsf Table 2 p.29)
    'Low': {'security.audit_trail': False, 'security.esign': False, 'security.auto_logoff': False, 'security.pw_expiry': False,
            'security.increased': False},
    '21 CFR Part 11': {'security.audit_trail': True, 'security.audit_reminder': True, 'security.audit_reminder_days': 7,
                       'security.esign_password': True, 'security.auto_logoff': True, 'security.auto_logoff_min': 60, 'security.pw_expiry': True,
                       'security.pw_expiry_days': 60, 'security.increased': True}}

# ---------------------------------------------------------------- users, groups and rights (abm p.47-50, gsf p.18-20)
RIGHTS = ('measure', 'method_select', 'quick_settings', 'sample_list', 'checks', 'monitor', 'favorites', 'data_view', 'adjustments', 'output_fields',
          'method_settings', 'method_management', 'data_delete', 'data_settings', 'check_settings', 'user_functions', 'setup_measuring',
          'setup_control_panel', 'setup_printout', 'setup_security', 'setup_users', 'service')
RIGHT_LABELS = {'measure': 'Start / Stop measurements', 'method_select': 'Select a method', 'quick_settings': 'Quick settings',
                'sample_list': 'Edit the sample list', 'checks': 'Perform checks', 'monitor': 'Monitor mode', 'favorites': 'Favorites',
                'data_view': 'View data memory', 'adjustments': 'Perform adjustments', 'output_fields': 'Change output fields',
                'method_settings': 'Method settings', 'method_management': 'Method management / visibility', 'data_delete': 'Delete data',
                'data_settings': 'Data memory settings', 'check_settings': 'Check / standard management', 'user_functions': 'User functions',
                'setup_measuring': 'Measuring system settings', 'setup_control_panel': 'Control panel', 'setup_printout': 'Printout settings',
                'setup_security': 'System security', 'setup_users': 'User management', 'service': 'Service'}
OPERATOR_RIGHTS = ['measure', 'method_select', 'quick_settings', 'sample_list', 'checks', 'monitor', 'favorites', 'data_view']
MANAGER_RIGHTS = OPERATOR_RIGHTS + ['adjustments', 'output_fields', 'method_settings', 'data_delete', 'check_settings']
STANDARD_GROUPS = [dict(name='Administrator', base='Administrator', icon='Administrator', rights=list(RIGHTS), standard=True),
                   dict(name='Manager', base='Manager', icon='Manager', rights=list(MANAGER_RIGHTS), standard=True),
                   dict(name='Operator', base='Operator', icon='Operator', rights=list(OPERATOR_RIGHTS), standard=True)]
GROUP_ICONS = ('Administrator', 'Manager', 'Operator', 'Icon 4', 'Icon 5', 'Icon 6', 'Icon 7')
FACTORY_USERS = [dict(name='Administrator', group='Administrator', password='administrator', active=True, auto_logon=True, role='None',
                      sign_name='', email='', email_active=False, failed=0, history=[]),
                 dict(name='Manager', group='Manager', password='manager', active=True, auto_logon=False, role='None', sign_name='', email='',
                      email_active=False, failed=0, history=[]),
                 dict(name='Operator', group='Operator', password='operator', active=True, auto_logon=False, role='None', sign_name='', email='',
                      email_active=False, failed=0, history=[])]
SIGN_ROLES = ('None', 'Submitter', 'Reviewer', 'Approver')

# ---------------------------------------------------------------- data memory filters (abm p.89-90, gsf p.26)
FILTER_CRITERIA = ('Sample Name', 'Method', 'User', 'Date Range', 'Sample Error State', 'Print/Export Status', 'Measuring Value', 'Master Condition')
COMPARATORS = ('equals', 'not equal', 'larger than', 'smaller than', 'containing', 'not containing', 'printed/exported', 'not printed/exported')
DATE_RANGES = ('Today', 'Yesterday', 'Last Week', 'Last Month')
MEASUREMENT_TYPES = {'S': 'S (Standard)', 'C': 'C (Check)', 'MM': 'MM (Multiple Measurement)', 'MF': 'MF (Multi Fill)', 'TS': 'TS (Temperature Scan)',
                     'RM': 'RM (Repeated Mode)', 'TTS': 'TTS (Temperature Table Scan)'}                 # RM / TTS: DMA M (dma p.36-38)
RM_SAVE = ('all', 'valid two only', 'final one only')
PRINTER_TYPES = ('RS-232 printer', 'PCL printer (USB)', 'PCL printer (network)', 'PDF File Printer')
REPORT_TYPES = ('Detail Report', 'List Report')
FUNCTION_TYPES = ('Constant', 'Formula', 'Linear Function', 'Polynomial (1D)', 'Polynomial (2D)', 'Table')


# ---------------------------------------------------------------- device state
def new_device(profile):
    """Factory state of the device layer (as delivered: Administrator logged on by auto logon, abm p.47; factory methods; no data)."""
    settings = {f['key']: copy.deepcopy(f['default']) for f in profile['settings']}
    methods = [method_record(profile, m) for m in profile['factory_methods']]
    ap = dict(settings=settings, clock_offset_s=0.0, users=copy.deepcopy(FACTORY_USERS), groups=copy.deepcopy(STANDARD_GROUPS), user=None,
              last_input_s=0.0, boot_s=0.0, power_epoch=0, messages=[], next_msg=1, favorites=copy.deepcopy(profile.get('fixed_favorites', [])),
              methods=methods, active=methods[0]['name'], quick=quick_from_method(profile, methods[0]), sample_no=1, sample_list=[],
              templates=[dict(name=f'Sample List {i + 1}', entries=[]) for i in range(10)], data=[], next_data=1, check_data=[], next_check=1,
              adjustments=[], next_adj=1, audit=[], next_audit=1, filters=dict(view=[], delete=[], check=[]),
              printers=[], shares=[], reports=[dict(name='Report 1', type='Detail Report', default=True, gxp=False, orientation='Portrait', printed_by=True)],
              browser=list(profile['browser_default']), functions=[], checks=copy.deepcopy(profile.get('factory_checks', [])),
              standards=copy.deepcopy(profile.get('factory_standards', [])), run=None, monitor=True, frozen=None, frozen_rec=None, procedure=None,
              extrapolation=copy.deepcopy(profile.get('extrapolation_default', [])),
              touch_cal=None, firmware_checked=None, reboot_pending=False, beeps=0, auto_printouts=0, unique=1)
    ap.update(copy.deepcopy(profile.get('device_extra', {})))
    ap['user'] = auto_logon_user(ap)
    return ap


def method_record(profile, m):
    out = dict(name=m['name'], visible=True, protected=m.get('protected', False), settings={f['key']: f['default'] for f in profile['method_fields']},
               mode='S', mm_count=3, mf_count=2, ts_start=20.0, ts_step=5.0, ts_end=40.0, check=None, layout=profile['layouts'][0][0],
               rm_count=3, rm_dev=None, rm_save='all', tts_temps=[], **copy.deepcopy(profile.get('method_extra_defaults', {})),
               fields=copy.deepcopy(profile['default_fields']), output=copy.deepcopy(profile['default_output']), limits=[],
               quick=list(profile['quick_default']), modules={}, constants={})
    for k, v in m.items():
        if k in out['settings']:
            out['settings'][k] = v
        elif k in out and k != 'name':
            out[k] = copy.deepcopy(v)
    return out


def quick_from_method(profile, m):
    q = dict(sample_name='', type=m['mode'], mm_count=m['mm_count'], mf_count=m['mf_count'], ts_start=m['ts_start'], ts_step=m['ts_step'],
             ts_end=m['ts_end'], check=m['check'], field1='', field2='', field3='', rm_count=m.get('rm_count', 3), rm_dev=m.get('rm_dev'),
             rm_save=m.get('rm_save', 'all'), tts_temps=list(m.get('tts_temps', [])))
    q.update({k: m['settings'][k] for k in profile['quick_fields'] if k in m['settings']})
    return q


def method(ap, name=None):
    name = ap['active'] if name is None else name
    for m in ap['methods']:
        if m['name'] == name:
            return m
    return None


def auto_logon_user(ap):
    if ap['settings'].get('security.increased') or ap['settings'].get('security.audit_trail'):
        return None                                                     # abm p.48, p.57: auto logon disabled with increased security / audit trail
    for u in ap['users']:
        if u['auto_logon'] and u['active']:
            return u['name']
    return None


def user_record(ap, name):
    for u in ap['users']:
        if u['name'].lower() == str(name).lower():
            return u
    return None


def group_record(ap, name):
    for g in ap['groups']:
        if g['name'] == name:
            return g
    return None


def rights_of(ap):
    u = user_record(ap, ap['user']) if ap['user'] else None
    g = group_record(ap, u['group']) if u else None
    return list(g['rights']) if g else []


def has_right(ap, right):
    return right in rights_of(ap)


# ---------------------------------------------------------------- clock
def device_dt(host):
    return EPOCH0 + datetime.timedelta(seconds=host.time_s + host.ap['clock_offset_s'])


def device_iso(host):
    return device_dt(host).replace(microsecond=0).isoformat()


def clock_text(host):
    dt = device_dt(host)
    if host.ap['settings'].get('region.data_format', '').startswith('English (United States)'):
        h = dt.hour % 12 or 12
        return f'{h}:{dt.minute:02d}:{dt.second:02d} {"AM" if dt.hour < 12 else "PM"}'
    return f'{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}'


def date_text(host, dt=None):
    dt = dt or device_dt(host)
    fmt = host.ap['settings'].get('region.data_format', '')
    if fmt == 'English (United States)':
        return f'{dt.month}/{dt.day}/{dt.year}'
    if fmt == 'English (United Kingdom)':
        return f'{dt.day:02d}/{dt.month:02d}/{dt.year}'
    return f'{dt.day:02d}.{dt.month:02d}.{dt.year}'


# ---------------------------------------------------------------- messages and audit trail
def add_message(host, level, text):
    ap = host.ap
    if any(m['text'] == text for m in ap['messages']):
        return
    ap['messages'].append(dict(id=ap['next_msg'], level=level, text=text, time=device_iso(host)))
    ap['next_msg'] += 1


def status(ap):
    levels = {m['level'] for m in ap['messages']}
    return 'error' if 'error' in levels else 'warning' if 'warning' in levels else 'ok'


def audit(host, text):
    ap = host.ap
    if not ap['settings'].get('security.audit_trail'):
        return
    ap['audit'].append(dict(id=ap['next_audit'], time=device_iso(host), user=ap['user'] or '-', text=text, comment='', exported=False))
    ap['next_audit'] += 1
    if len(ap['audit']) > 999:                                            # abm p.57: up to 999 entries
        del ap['audit'][:-999]


# ---------------------------------------------------------------- formatting of values (unit conversion, digits)
def to_unit(q, value, unit):
    if value is None:
        return None
    if q.get('kind') == 'temperature':
        return value if unit == '°C' else value * 9.0 / 5.0 + 32.0 if unit == '°F' else value + 273.15
    if q.get('kind') == 'fraction':
        return value * 100.0 if unit in ('% w/w', '%') else value * 1000.0 if unit == 'g/kg' else value
    if q.get('kind') == 'density':
        return value if unit == 'g/cm³' else value * 1000.0 if unit == 'kg/m³' else value
    return value


def fmt_value(profile, field, value, ap=None):
    q = profile['quantities'].get(field['q'], {})
    if value is None:
        return '---' if q.get('kind') != 'text' else ''
    if q.get('kind') in ('text',):
        return str(value)
    v = to_unit(q, value, field.get('unit') or q.get('unit'))
    d = int(field.get('digits', q.get('digits', 2)))
    x = math.floor(abs(v) * 10 ** d + 0.5 + 1e-9) / 10 ** d
    s = f'{math.copysign(x, v) if x else 0.0:.{d}f}'
    if ap is not None and ap['settings'].get('region.data_format') in ('Deutsch (Deutschland)', 'Français (France)'):
        s = s.replace('.', ',')
    return s


def from_input_temperature(ap, v):
    """A temperature typed in the input unit (abm p.42) -> degC."""
    u = ap['settings'].get('units.temperature', '°C')
    return v if u == '°C' else (v - 32.0) * 5.0 / 9.0 if u == '°F' else v - 273.15


def to_input_temperature(ap, c):
    u = ap['settings'].get('units.temperature', '°C')
    return c if u == '°C' else c * 9.0 / 5.0 + 32.0 if u == '°F' else c + 273.15


# ---------------------------------------------------------------- user functions (abm 14.1 p.94-99, gsf 9.2 p.34-37)
_ALLOWED = {ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load,
            ast.Call, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd}
_FUNCS = {'log': math.log10, 'ln': math.log, 'sqrt': math.sqrt, 'sin': math.sin, 'cos': math.cos, 'tan': math.tan, 'asin': math.asin,
          'acos': math.acos, 'atan': math.atan}


def formula_ok(text, names):
    try:
        tree = ast.parse(text.replace('^', '**'), mode='eval')
    except SyntaxError:
        return False
    for n in ast.walk(tree):
        if type(n) not in _ALLOWED:
            return False
        if isinstance(n, ast.Call) and not (isinstance(n.func, ast.Name) and n.func.id in _FUNCS and len(n.args) == 1 and not n.keywords):
            return False
        if isinstance(n, ast.Name) and n.id not in names and n.id not in _FUNCS:
            return False
    return True


def formula_eval(text, env):
    tree = ast.parse(text.replace('^', '**'), mode='eval')

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            return float(n.value)
        if isinstance(n, ast.Name):
            return float(env[n.id])
        if isinstance(n, ast.UnaryOp):
            return -ev(n.operand) if isinstance(n.op, ast.USub) else ev(n.operand)
        if isinstance(n, ast.Call):
            return _FUNCS[n.func.id](ev(n.args[0]))
        a, b = ev(n.left), ev(n.right)
        return {ast.Add: lambda: a + b, ast.Sub: lambda: a - b, ast.Mult: lambda: a * b, ast.Div: lambda: a / b, ast.Pow: lambda: a ** b}[type(n.op)]()
    return ev(tree)


def function_value(fn, values, constants=None):
    """Output of one user function from the quantities of a result (None when an input is missing or the calculation fails)."""
    try:
        t = fn['type']
        if t == 'Constant':
            return float((constants or {}).get(fn['name'], fn.get('value', 0.0)))
        xs = [values.get(q) for q in fn.get('inputs', [])]
        if any(x is None for x in xs):
            return None
        if t == 'Linear Function':
            return fn['offset'] + fn['factor'] * xs[0]
        if t == 'Polynomial (1D)':
            return sum(c * xs[0] ** i for i, c in enumerate(fn['coefficients']))
        if t == 'Polynomial (2D)':
            x, y = xs[0], xs[1]
            c = fn['coefficients']                                         # order: 00 01 02 03 10 11 12 20 21 30 (gsf p.36)
            terms = [1, y, y * y, y ** 3, x, x * y, x * y * y, x * x, x * x * y, x ** 3]
            return sum(a * b for a, b in zip(c, terms))
        if t == 'Table':
            pts = sorted(fn['table'])
            x = xs[0]
            if not pts or not pts[0][0] <= x <= pts[-1][0]:
                return None                                                # interpolates, never extrapolates (gsf p.36)
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                if x0 <= x <= x1:
                    return y0 + (y1 - y0) * (x - x0) / (x1 - x0) if x1 > x0 else y0
            return pts[0][1]
        if t == 'Formula':
            env = {n: x for n, x in zip(fn['variables'], xs)}
            return formula_eval(fn['formula'], env)
    except (ValueError, ZeroDivisionError, OverflowError, KeyError, IndexError, TypeError):
        return None
    return None


# ---------------------------------------------------------------- data memory
def record_values(profile, ap, values, m):
    out = dict(values)
    out.update(profile.get('extra_values', lambda a, v: {})(ap, out))
    for fn in ap['functions']:
        out['fn:' + fn['name']] = function_value(fn, out, m.get('constants') if m else None)
    return out


def store_record(host, rec):
    """Store a result in the data memory (capacity, reminder, circular buffer; abm p.87, gsf p.25)."""
    ap, prof = host.ap, host.AP_PROFILE
    if ap['settings'].get('security.non_storage'):                         # gsf p.29-30: non storage mode, results are not stored
        rec = dict(rec, id=ap['next_data'], uid=f'{prof["serial"]}-{ap["unique"]:06d}', exported=False, signatures={}, comments=[], transient=True)
        ap['next_data'] += 1
        ap['unique'] += 1
        return rec
    cap = prof['memory_capacity']
    if len(ap['data']) >= cap:
        if ap['settings']['memory.auto_delete'] and not ap['settings']['security.increased']:
            del ap['data'][:100]
        else:
            add_message(host, 'error', 'Data memory is full. No further results can be stored.')
            return None
    rec = dict(rec, id=ap['next_data'], uid=f'{prof["serial"]}-{ap["unique"]:06d}', exported=False,
               signatures=dict(submitter=None, reviewer=None, approver=None), comments=[])
    ap['next_data'] += 1
    ap['unique'] += 1
    ap['data'].append(rec)
    if len(ap['data']) >= prof['memory_reminder']:
        add_message(host, 'warning', 'Data memory almost full. Please delete measured data.')
    return rec


def _match(host, rec, c):
    crit, comp, val = c['criterion'], c['comparator'], c.get('value', '')
    prof = host.AP_PROFILE
    if crit == 'Print/Export Status':
        return rec['exported'] if comp == 'printed/exported' else not rec['exported']
    if crit == 'Date Range':
        d = datetime.datetime.fromisoformat(rec['time']).date()
        today = device_dt(host).date()
        span = {'Today': (today, today), 'Yesterday': (today - datetime.timedelta(days=1),) * 2,
                'Last Week': (today - datetime.timedelta(days=7), today), 'Last Month': (today - datetime.timedelta(days=31), today)}[val]
        inside = span[0] <= d <= span[1]
        return inside if comp == 'equals' else not inside
    if crit == 'Measuring Value':
        x = rec['values'].get(prof['main_quantity'])
        try:
            ref = float(val)
        except (TypeError, ValueError):
            return False
        if x is None:
            return False
        return {'larger than': x > ref, 'smaller than': x < ref, 'equals': abs(x - ref) < 1e-12, 'not equal': abs(x - ref) >= 1e-12}.get(comp, False)
    field = {'Sample Name': 'sample_name', 'Method': 'method', 'User': 'user', 'Sample Error State': 'error_state', 'Master Condition': 'condition'}[crit]
    s = str(rec.get(field, ''))
    return {'equals': s == val, 'not equal': s != val, 'containing': str(val).lower() in s.lower(),
            'not containing': str(val).lower() not in s.lower()}.get(comp, False)


def filtered(host, view, records=None):
    ap = host.ap
    recs = ap['data'] if records is None else records
    return [r for r in recs if all(_match(host, r, c) for c in ap['filters'][view])]


def statistics(host):
    """Statistics per method over the filtered measured data, scans and checks excluded (abm p.91, gsf p.26-27)."""
    ap, prof = host.ap, host.AP_PROFILE
    out = []
    recs = [r for r in filtered(host, 'view') if r['kind'] == 'measurement' and not r.get('sub')]
    for name in sorted({r['method'] for r in recs}):
        m = method(ap, name)
        qs = [f['q'] for f in (m['output'] if m else [])] or list(ap['browser'])
        qs = [q for q in qs if prof['quantities'].get(q, {}).get('kind') not in ('text', None) or q.startswith('fn:')]
        rows = []
        for q in qs:
            xs = [r['values'][q] for r in recs if r['method'] == name and isinstance(r['values'].get(q), (int, float))]
            if not xs:
                continue
            avg = sum(xs) / len(xs)
            sd = math.sqrt(sum((x - avg) ** 2 for x in xs) / (len(xs) - 1)) if len(xs) > 1 else 0.0
            rows.append(dict(q=q, label=quantity_label(prof, ap, q), n=len(xs), min=min(xs), max=max(xs), avg=avg, sd=sd))
        out.append(dict(method=name, n=len([r for r in recs if r['method'] == name]), quantities=rows))
    return out


def quantity_label(profile, ap, q):
    if q.startswith('fn:'):
        return q[3:]
    return profile['quantities'].get(q, {}).get('label', q)


# ---------------------------------------------------------------- the commands of the device layer
def _caps():
    """Device-layer commands: (input_schema, output_schema, meaning_zh, kind, right, example, effects)."""
    method_name = dict(NAME)
    entry = obj(dict(name={'type': 'string', 'minLength': 0, 'maxLength': 50}, method=NAME, type={'type': 'string', 'enum': list(MEASUREMENT_TYPES)},
                     field1={'type': 'string', 'maxLength': 30}, field2={'type': 'string', 'maxLength': 30}, field3={'type': 'string', 'maxLength': 30}),
                optional=('type', 'field1', 'field2', 'field3'))
    criterion = obj(dict(criterion={'type': 'string', 'enum': list(FILTER_CRITERIA)}, comparator={'type': 'string', 'enum': list(COMPARATORS)},
                         value={'type': 'string', 'maxLength': 50}), optional=('value',))
    fn = obj(dict(name=NAME, type={'type': 'string', 'enum': list(FUNCTION_TYPES)}, quantity={'type': 'string', 'maxLength': 40},
                  unit={'type': 'string', 'maxLength': 20}, comment={'type': 'string', 'maxLength': 100}, protected=BOOL,
                  inputs={'type': 'array', 'items': STR, 'maxItems': 2}, variables={'type': 'array', 'items': STR, 'maxItems': 2},
                  value=num('1'), offset=num('1'), factor=num('1'), coefficients={'type': 'array', 'items': num('1'), 'maxItems': 10},
                  table={'type': 'array', 'items': {'type': 'array', 'items': num('1'), 'minItems': 2, 'maxItems': 2}, 'maxItems': 1000},
                  formula={'type': 'string', 'maxLength': 200}),
             optional=('quantity', 'unit', 'comment', 'protected', 'inputs', 'variables', 'value', 'offset', 'factor', 'coefficients', 'table', 'formula'))
    return {
        'settings.configure': (obj(dict(settings=ANYOBJ)), obj(dict(settings=ANYOBJ)), '修改仪器设置（控制面板、打印、样品列表选项、数据存储、系统安全）；按设置表校验，'
                               '改语言/数据格式/键盘时仪器重启', 'configuration', None, {'settings': {'control.brightness': 80}}, ['device.settings']),
        'clock.set': (obj(dict(date={'type': 'string', 'pattern': r'^\d{4}-\d{2}-\d{2}$'}, time={'type': 'string', 'pattern': r'^\d{2}:\d{2}$'}),
                          optional=('date', 'time')), obj(dict(datetime=STR)), '设定仪器日期与时间（Setup > Control Panel > Date and Time）', 'configuration',
                      'setup_control_panel', {'time': '09:30'}, ['device.datetime']),
        'user.logon': (obj(dict(user=NAME, password={'type': 'string', 'maxLength': 30})), obj(dict(user=STR)),
                       '登录（标题栏用户图标）：用户名与密码；增强安全下三次失败锁定账户', 'device_command', None, {'user': 'Operator', 'password': 'operator'},
                       ['device.users']),
        'user.logoff': (obj({}), obj(dict(user=nullable(STR))), '注销；若有自动登录用户则其自动登录，否则锁定直到有人登录', 'device_command', None, {},
                        ['device.users']),
        'users.save': (obj(dict(name=NAME, original=nullable(STR), group=NAME, active=BOOL, auto_logon=BOOL, role={'type': 'string', 'enum': list(SIGN_ROLES)},
                                sign_name={'type': 'string', 'maxLength': 40}, email={'type': 'string', 'maxLength': 64}, email_active=BOOL),
                           optional=('original', 'role', 'sign_name', 'email', 'email_active')),
                       obj(dict(name=STR)), '新建或编辑用户账户（激活、自动登录、用户名、用户组、电子签名角色、邮件）', 'configuration', 'setup_users',
                       {'name': 'Analyst', 'group': 'Operator', 'active': True, 'auto_logon': False}, ['device.users']),
        'users.delete': (obj(dict(name=NAME)), obj(dict(name=STR)), '删除用户账户（不能删除自己或更高权限组的账户）', 'configuration', 'setup_users',
                         {'name': 'Analyst'}, ['device.users']),
        'users.set_password': (obj(dict(name=NAME, password={'type': 'string', 'maxLength': 30})), obj(dict(name=STR)),
                               '设定/修改密码（每个用户可改自己的；管理员可改所有人）', 'configuration', None, {'name': 'Operator', 'password': 'secret1'},
                               ['device.users']),
        'groups.manage': (obj(dict(action={'type': 'string', 'enum': ['copy', 'rename', 'delete', 'icon', 'rights']}, group=NAME, name=NAME,
                                   icon={'type': 'string', 'enum': list(GROUP_ICONS)}, rights={'type': 'array', 'items': {'type': 'string', 'enum': list(RIGHTS)}}),
                              optional=('name', 'icon', 'rights')),
                          obj(dict(group=STR)), '用户组管理：复制标准组为自定义组、改名、改图标、改权限、删除自定义组', 'configuration', 'setup_users',
                          {'action': 'copy', 'group': 'Operator'}, ['device.users']),
        'messages.acknowledge': (obj(dict(id=nullable(num('1', 1, integer=True)))), obj(dict(remaining=num('1', 0, integer=True))),
                                 '确认消息列表中的一条（X）或全部（Delete all）', 'device_command', None, {'id': None}, ['device.messages']),
        'favorites.configure': (obj(dict(action={'type': 'string', 'enum': ['add', 'rename', 'delete', 'move_up', 'move_down']}, page=STR, name=NAME),
                                    optional=('name',)),
                                obj(dict(count=num('1', 0, integer=True))), '收藏夹：添加（对话框标题栏的收藏键）、改名、删除、排序（固定收藏不能删改）',
                                'configuration', 'favorites', {'action': 'add', 'page': 'quick_settings', 'name': 'Quick Settings'}, ['device.favorites']),
        'methods.select': (obj(dict(method=method_name)), obj(dict(method=STR)), '选择方法（主屏<Method>）：方法温度等写入快速设置并下发到测量单元',
                           'device_command', 'method_select', {'method': 'Refractive Index'}, ['methods.active', 'sample.quick_settings']),
        'methods.configure': (obj(dict(method=method_name, settings=ANYOBJ)), obj(dict(method=STR)),
                              '修改方法设置（测量模式、测量模块参数、显示布局、输出字段、结果输出、限值、快速设置参数、模块/常数）', 'configuration', 'method_settings',
                              {'method': 'Refractive Index', 'settings': {'mode': 'S'}}, ['methods.list']),
        'methods.manage': (obj(dict(action={'type': 'string', 'enum': ['copy', 'rename', 'delete', 'move_up', 'move_down', 'visibility', 'show_all',
                                                                    'hide_all']}, method=method_name,
                                    name=NAME, visible=BOOL), optional=('name', 'visible')),
                           obj(dict(method=STR)), '方法管理：复制、改名、删除、可见性与排序（当前方法不能删除/改名）', 'configuration', 'method_management',
                           {'action': 'copy', 'method': 'Refractive Index'}, ['methods.list']),
        'quick.configure': (obj(dict(settings=ANYOBJ)), obj(dict(settings=ANYOBJ)), '快速设置（主屏<Quick Settings>）：样品名、测量类型与本次测量参数，'
                            '保留到下次修改', 'configuration', 'quick_settings', {'settings': {'sample_name': 'S1'}}, ['sample.quick_settings']),
        'sample_list.edit': (obj(dict(action={'type': 'string', 'enum': ['add', 'update', 'delete', 'clear', 'save', 'load', 'rename']},
                                      index=num('1', 0, 199, integer=True), entry=entry, template=num('1', 0, 9, integer=True), name=NAME),
                                 optional=('index', 'entry', 'template', 'name')),
                             obj(dict(count=num('1', 0, integer=True))), '样品列表：添加/编辑/删除样品，列表管理保存/载入/改名模板与清空', 'configuration',
                             'sample_list', {'action': 'clear'}, ['sample.list', 'device.messages']),
        'measurement.start': (obj({}), obj(dict(phase=STR, operation_id=STR)), '按当前方法与快速设置开始测量（主屏<Start>）：等待恒温、保持时间、'
                              '按精度设定的稳定时间读数，超时中止；MM多次、MF多次装样、TS温度扫描、C核查；结果存入数据存储', 'device_command', 'measure', {},
                              ['cycle.phase', 'run.state']),
        'measurement.continue': (obj({}), obj(dict(phase=STR)), 'MF多次装样：换样后确认继续下一次测量', 'device_command', 'measure', {},
                                 ['run.state', 'cycle.phase']),
        'monitor.set': (obj(dict(active=BOOL)), obj(dict(active=BOOL)), '监视模式（快捷区）：实时显示读数/解冻结果', 'device_command', 'monitor',
                        {'active': True}, ['display.readout']),
        'data.filter': (obj(dict(view={'type': 'string', 'enum': ['view', 'delete']}, action={'type': 'string', 'enum': ['add', 'delete', 'clear']},
                                 criterion=criterion, index=num('1', 0, 7, integer=True)), optional=('criterion', 'index')),
                        obj(dict(count=num('1', 0, integer=True))), '数据存储筛选（<Filter>：添加条件、比较符、值；删除一条或全部；条件之间为AND）',
                        'configuration', 'data_view', {'view': 'view', 'action': 'clear'}, ['data.filters']),
        'data.delete': (obj(dict(kind={'type': 'string', 'enum': ['measured', 'check']}, id=nullable(num('1', 1, integer=True)))),
                        obj(dict(deleted=num('1', 0, integer=True))), '删除测量/核查数据：单条（Details > Delete）或按删除筛选的全部（Delete All）',
                        'device_command', 'data_delete', {'kind': 'measured', 'id': None}, ['data.measured', 'data.checks']),
        'data.sign': (obj(dict(id=num('1', 1, integer=True), positive=BOOL, comment={'type': 'string', 'maxLength': 100},
                               password={'type': 'string', 'maxLength': 30}), optional=('comment', 'password')),
                      obj(dict(signatures=ANYOBJ)), '电子签名：按用户角色对单条测量结果签署正/负（只能签一次）', 'device_command', 'data_view',
                      {'id': 1, 'positive': True}, ['data.measured']),
        'audit.delete': (obj({}), obj(dict(deleted=num('1', 0, integer=True))), '删除审计追踪中已导出的条目（未导出的不能删除）', 'device_command', 'data_delete',
                         {}, ['data.audit_trail']),
        'lists.edit': (obj(dict(list={'type': 'string', 'enum': ['printers', 'reports', 'browser', 'shares', 'extrapolation']}, action={'type': 'string', 'enum': ['save', 'delete',
                                                                                                                                  'move_up', 'move_down']},
                                index=nullable(num('1', 0, integer=True)), item=ANYOBJ), optional=('index', 'item')),
                       obj(dict(count=num('1', 0, integer=True))), '配置列表：打印机管理、报表管理、数据浏览列、网络共享（FTP连接）', 'configuration', None,
                       {'list': 'browser', 'action': 'move_up', 'index': 1}, ['device.lists']),
        'functions.save': (obj(dict(function=fn, original=nullable(STR)), optional=('original',)), obj(dict(name=STR)),
                           '用户函数：新建/编辑常数、公式、线性、一维/二维多项式、表格（输出量可作结果字段）', 'configuration', 'user_functions',
                           {'function': {'name': 'Offset', 'type': 'Constant', 'value': 0.0}}, ['functions.list']),
        'functions.delete': (obj(dict(name=NAME)), obj(dict(name=STR)), '删除用户函数（被方法限值使用时不能删除）', 'configuration', 'user_functions',
                             {'name': 'Offset'}, ['functions.list']),
        'calculator.evaluate': (obj(dict(function=NAME, inputs={'type': 'array', 'items': num('1'), 'maxItems': 2})), obj(dict(value=nullable(num('1')))),
                                '计算器：输入值计算用户函数的输出', 'device_command', 'user_functions', {'function': 'Offset', 'inputs': []},
                                ['functions.calculator']),
        'calculator.group': (obj(dict(inputs={'type': 'object', 'additionalProperties': num('1')})), obj(dict(values={'type': 'object'})),
                             '组计算器：输入各输入量的值，一次算出全部用户函数', 'device_command', 'user_functions', {'inputs': {}}, ['functions.calculator']),
        'functions.table_edit': (obj(dict(name=NAME, action={'type': 'string', 'enum': ['add', 'delete']}, x=num('1'), y=num('1'),
                                          index=num('1', 0, 999, integer=True)), optional=('x', 'y', 'index')), obj(dict(pairs=num('1', 0, integer=True))),
                                 '用户表格函数：逐行添加/删除数据对（只插值，不外推）', 'configuration', 'user_functions',
                                 {'name': 'Table 1', 'action': 'add', 'x': 1.33, 'y': 0.0}, ['functions.list']),
        'checks.manage': (obj(dict(action={'type': 'string', 'enum': ['save', 'delete']}, check=ANYOBJ, original=nullable(STR)), optional=('original',)),
                          obj(dict(name=STR)), '核查管理：核查名称、类型、方法/参考标准、是否GxP相关、提醒间隔、到期禁止测量、容差', 'configuration',
                          'check_settings', {'action': 'delete', 'check': {'name': 'x'}}, ['checks.list']),
        'standards.manage': (obj(dict(action={'type': 'string', 'enum': ['save', 'delete']}, standard=ANYOBJ, original=nullable(STR)), optional=('original',)),
                             obj(dict(name=STR)), '参考标准管理：名称、参考温度、参考值、批号/证书号/有效期', 'configuration', 'check_settings',
                             {'action': 'delete', 'standard': {'name': 'x'}}, ['standards.list']),
        'check.start': (obj(dict(check=NAME)), obj(dict(phase=STR, operation_id=STR)), '执行核查（Checks / Adjustments > Checks Execution）：'
                        '测量参考物质并与参考值比较，结果存入核查数据', 'device_command', 'checks', {'check': 'Water Check'}, ['run.state', 'cycle.phase']),
        'touchscreen.calibrate': (obj(dict(points={'type': 'array', 'items': {'type': 'array', 'items': num('px'), 'minItems': 2, 'maxItems': 2},
                                                   'minItems': 4, 'maxItems': 4})), obj(dict(calibrated=BOOL)),
                                  '触摸屏校准：依次点四个十字，接受新校准', 'device_command', 'setup_control_panel',
                                  {'points': [[40, 40], [760, 40], [760, 440], [40, 440]]}, ['device.touch_calibration']),
        'firmware.check': (obj({}), obj(dict(up_to_date=BOOL)), '模块固件更新（Internal）：检查各模块固件版本，均为最新时不改动', 'device_command', 'service',
                           {}, ['device.system']),
    }


DEVICE_CAPS = _caps()


FREE_WHILE_RUNNING = ('monitor.set', 'favorites.configure', 'data.filter', 'calculator.evaluate', 'calculator.group', 'firmware.check', 'messages.acknowledge',
                      'user.logon', 'user.logoff', 'measurement.continue')
NO_LOGON = ('user.logon', 'user.logoff', 'messages.acknowledge')


def right_of(host, cap, args):
    if cap == 'users.set_password' and args.get('name', '').lower() == str(host.ap['user']).lower():
        return None                                                        # every user may change the own password (abm p.49)
    if cap == 'lists.edit':
        return {'printers': 'setup_printout', 'reports': 'setup_printout', 'browser': 'data_settings', 'shares': 'setup_control_panel',
                'extrapolation': 'setup_measuring'}[args['list']]
    if cap == 'methods.configure':
        return 'output_fields' if set(args['settings']) <= {'fields', 'set_field'} else 'method_settings'
    if cap in DEVICE_CAPS:
        return DEVICE_CAPS[cap][4]
    return host.AP_PROFILE.get('rights', {}).get(cap)


def gate(host, cap, args):
    """Power, logon, rights and measurement locks of a device command (abm p.32: settings cannot be saved while a measurement runs)."""
    ap = host.ap
    need(host.powered, 'POWER_OFF')
    if cap in NO_LOGON:
        return
    need(ap['user'] is not None, 'LOGON_REQUIRED')
    right = right_of(host, cap, args)
    if right:
        need(has_right(ap, right), 'PERMISSION_DENIED')
    if cap not in FREE_WHILE_RUNNING:
        need(host.phase != 'running' and (ap['run'] is None or cap in host.AP_PROFILE.get('free_while_run', ())), 'BUSY')


def device_invoke(host, cap, args):
    """Run one device-layer command on a candidate copy of host.ap and commit it when it succeeds (a _Keep refusal keeps its device-side
    counters, e.g. failed logons)."""
    check(host.AP_CAPS[cap][0], args)
    gate(host, cap, args)
    handler = _HANDLERS.get(cap) or host.AP_PROFILE['handlers'][cap]
    saved = host.ap
    host.ap = copy.deepcopy(saved)
    try:
        out = handler(host, args)
    except _Keep as exc:
        host.revision += 1
        raise ActionRejected(str(exc)) from None
    except BaseException:
        host.ap = saved
        raise
    if cap not in ('calculator.evaluate', 'calculator.group'):
        host.revision += 1
    return out


# ---- handlers
def _settings(host, a):
    ap, prof = host.ap, host.AP_PROFILE
    defs = {f['key']: f for f in prof['settings']}
    ch = a['settings']
    need(bool(ch), 'INVALID_ARGUMENT')
    for k, v in ch.items():
        f = defs.get(k)
        need(f is not None and value_ok(f, v), 'INVALID_ARGUMENT')
        right = SECTION_RIGHT.get(f['section'], prof.get('section_rights', {}).get(f['section'], 'setup_measuring'))
        need(has_right(ap, right), 'PERMISSION_DENIED')
    old = dict(ap['settings'])
    new = dict(old, **copy.deepcopy(ch))
    if 'security.level' in ch and ch['security.level'] != old['security.level']:
        if ch['security.level'] in SECURITY_LEVELS:
            for k, v in SECURITY_LEVELS[ch['security.level']].items():
                if k in new and k not in ch:
                    new[k] = v
        ap['sample_list'] = []                                             # abm p.53: the current sample list is reset
    elif any(k in ch for k in ('security.audit_trail', 'security.esign', 'security.auto_logoff', 'security.pw_expiry', 'security.increased')):
        lvl = next((n for n, d in SECURITY_LEVELS.items() if all(new.get(k) == v for k, v in d.items() if k in ('security.audit_trail',
                    'security.auto_logoff', 'security.pw_expiry', 'security.increased'))), 'User-defined')
        new['security.level'] = lvl                                       # abm p.52: any change of a standard setting -> user-defined level
    if new.get('security.increased') and not old.get('security.increased'):
        for u in ap['users']:                                              # abm p.54: accounts not complying with the rules are deactivated
            if len(u['name']) < 6 or len(u['password']) < 6:
                u['active'] = False
            u['auto_logon'] = False
    ap['settings'] = new
    for k in ch:
        if old.get(k) != new.get(k):
            audit(host, f'Setting "{defs[k]["label"]}" changed from {old.get(k)!r} to {new.get(k)!r}')
    if any(defs[k].get('reboot') and old.get(k) != new.get(k) for k in ch):
        ap['reboot_pending'] = True                                        # abm p.41: the instrument reboots after confirmation
        _reboot(host)
    return dict(settings={k: new[k] for k in ch})


def _reboot(host):
    ap = host.ap
    ap['power_epoch'] += 1
    ap['reboot_pending'] = False
    ap['user'] = auto_logon_user(ap)
    ap['boot_s'] = host.time_s
    ap['sample_list'] = []                                                 # abm p.82: cleared when the instrument is switched off


def _clock(host, a):
    need('date' in a or 'time' in a, 'INVALID_ARGUMENT')
    cur = device_dt(host)
    try:
        d = datetime.date.fromisoformat(a['date']) if 'date' in a else cur.date()
        hh, mm = (int(x) for x in a['time'].split(':')) if 'time' in a else (cur.hour, cur.minute)
        new = datetime.datetime(d.year, d.month, d.day, hh, mm, 0 if 'time' in a else cur.second)
    except ValueError:
        raise ActionRejected('INVALID_ARGUMENT') from None
    host.ap['clock_offset_s'] = (new - EPOCH0).total_seconds() - host.time_s
    audit(host, f'Date and time changed to {new.isoformat()}')
    return dict(datetime=device_iso(host))


def _pw_equal(host, stored, given):
    if host.AP_PROFILE.get('password_case_sensitive', True):
        return stored == given
    return stored.lower() == given.lower()                                 # abm p.48: passwords are not case sensitive (Abbemat 350/550)


def _logon(host, a):
    ap = host.ap
    u = user_record(ap, a['user'])
    need(u is not None, 'WRONG_PASSWORD')
    need(u['active'], 'ACCOUNT_LOCKED')
    if u['password'] and not _pw_equal(host, u['password'], a['password']):
        u['failed'] += 1
        audit(host, f'Incorrect password for user {u["name"]}')
        if ap['settings']['security.increased'] and u['failed'] >= 3:     # abm p.33: deactivated after three failed attempts
            u['active'] = False                                            # the failed attempt and the lock are kept although the logon fails
            raise _Keep('ACCOUNT_LOCKED')
        raise _Keep('WRONG_PASSWORD')
    u['failed'] = 0
    ap['user'] = u['name']
    ap['last_input_s'] = host.time_s
    audit(host, f'User {u["name"]} logged on')
    return dict(user=u['name'])


class _Keep(ActionRejected):
    """A refusal whose side effects are part of the device behaviour (failed logon counter, account lock)."""


def _logoff(host, a):
    ap = host.ap
    audit(host, f'User {ap["user"]} logged off')
    ap['user'] = auto_logon_user(ap)
    return dict(user=ap['user'])


def _users_save(host, a):
    ap = host.ap
    inc = ap['settings']['security.increased']
    orig = a.get('original')
    g = group_record(ap, a['group'])
    need(g is not None, 'NOT_FOUND')
    need(all(32 <= ord(c) < 127 for c in a['name']), 'INVALID_ARGUMENT')       # ASCII names (abm p.47)
    need(not inc or len(a['name']) >= 6, 'NOT_ALLOWED')                      # abm p.48: at least 6 characters with increased security
    need(not (a['auto_logon'] and (inc or ap['settings']['security.audit_trail'])), 'NOT_ALLOWED')
    clash = user_record(ap, a['name'])
    if orig is None:
        need(clash is None, 'IN_USE')
        need(len(ap['users']) < host.AP_PROFILE.get('max_users', 50), 'NOT_ALLOWED')
        u = dict(name=a['name'], group=a['group'], password='', active=a['active'], auto_logon=False, role='None', sign_name='', email='',
                 email_active=False, failed=0, history=[])
        ap['users'].append(u)
    else:
        u = user_record(ap, orig)
        need(u is not None, 'NOT_FOUND')
        need(clash is None or clash is u, 'IN_USE')
        if ap['user'] and u['name'].lower() == ap['user'].lower():
            ap['user'] = a['name']
        u.update(name=a['name'], group=a['group'], active=a['active'])
    if a['auto_logon']:
        for x in ap['users']:
            x['auto_logon'] = False                                         # one auto logon user (abm p.48)
    u['auto_logon'] = a['auto_logon']
    for k in ('role', 'sign_name', 'email', 'email_active'):
        if k in a:
            u[k] = a[k]
    if a['active']:
        u['failed'] = 0
    audit(host, f'User account {u["name"]} {"created" if orig is None else "changed"}')
    return dict(name=u['name'])


def _level(ap, group):
    g = group_record(ap, group)
    return {'Administrator': 3, 'Manager': 2, 'Operator': 1}.get(g['base'] if g else 'Operator', 1)


def _users_delete(host, a):
    ap = host.ap
    u = user_record(ap, a['name'])
    need(u is not None, 'NOT_FOUND')
    need(u['name'].lower() != str(ap['user']).lower(), 'NOT_ALLOWED')       # not the own account (abm p.49)
    me = user_record(ap, ap['user'])
    need(_level(ap, u['group']) <= _level(ap, me['group']), 'NOT_ALLOWED')
    ap['users'].remove(u)
    audit(host, f'User account {u["name"]} deleted')
    return dict(name=u['name'])


def _users_password(host, a):
    ap = host.ap
    u = user_record(ap, a['name'])
    need(u is not None, 'NOT_FOUND')
    pw = a['password']
    s = ap['settings']
    need(all(32 <= ord(c) < 127 for c in pw), 'INVALID_ARGUMENT')
    if s['security.increased'] or s['security.level'] == '21 CFR Part 11':
        need(len(pw) >= 6, 'NOT_ALLOWED')                                  # abm p.48
        need(pw not in u['history'][-5:], 'NOT_ALLOWED')                  # the last 5 passwords cannot be reused
    if s['security.pw_min_active']:
        need(len(pw) >= s['security.pw_min_length'], 'NOT_ALLOWED')
    u['history'] = (u['history'] + [u['password']])[-5:] if u['password'] else u['history']
    u['password'] = pw
    audit(host, f'Password of user {u["name"]} changed')
    return dict(name=u['name'])


def _groups(host, a):
    ap = host.ap
    g = group_record(ap, a['group'])
    need(g is not None, 'NOT_FOUND')
    act = a['action']
    if act == 'copy':
        need(g['base'] in ('Manager', 'Operator'), 'NOT_ALLOWED')           # gsf p.18: copies of manager / operator groups
        n = 1
        while group_record(ap, f'{g["name"]} ({n})'):
            n += 1
        new = dict(name=a.get('name') or f'{g["name"]} ({n})', base=g['base'], icon='Icon 4', rights=list(g['rights']), standard=False)
        need(group_record(ap, new['name']) is None, 'IN_USE')
        ap['groups'].append(new)
        audit(host, f'User group {new["name"]} created')
        return dict(group=new['name'])
    need(not g['standard'], 'NOT_ALLOWED')                                 # standard groups cannot be edited or deleted (gsf p.18)
    if act == 'rename':
        need('name' in a and group_record(ap, a['name']) is None, 'IN_USE')
        for u in ap['users']:
            if u['group'] == g['name']:
                u['group'] = a['name']
        g['name'] = a['name']
    elif act == 'icon':
        need('icon' in a, 'INVALID_ARGUMENT')
        g['icon'] = a['icon']
    elif act == 'rights':
        need('rights' in a, 'INVALID_ARGUMENT')
        base = set(RIGHTS) if g['base'] == 'Administrator' else set(MANAGER_RIGHTS if g['base'] == 'Manager' else OPERATOR_RIGHTS)
        need(set(a['rights']) <= base, 'NOT_ALLOWED')                     # custom groups are based on the manager / operator rights
        g['rights'] = [r for r in RIGHTS if r in a['rights']]
    else:
        need(not any(u['group'] == g['name'] for u in ap['users']), 'IN_USE')
        ap['groups'].remove(g)
    audit(host, f'User group {g["name"]} changed ({act})')
    return dict(group=g['name'])


def _ack(host, a):
    ap = host.ap
    if a['id'] is None:
        ap['messages'] = []
    else:
        need(any(m['id'] == a['id'] for m in ap['messages']), 'NOT_FOUND')
        ap['messages'] = [m for m in ap['messages'] if m['id'] != a['id']]
    return dict(remaining=len(ap['messages']))


def _favorites(host, a):
    ap, prof = host.ap, host.AP_PROFILE
    fixed = [f['page'] for f in prof.get('fixed_favorites', [])]
    pages = [f['page'] for f in ap['favorites']]
    act, page = a['action'], a['page']
    need(page in prof['favorite_pages'] or page in fixed, 'INVALID_ARGUMENT')
    if act == 'add':
        need(page not in pages, 'IN_USE')
        need(len(pages) < 30, 'NOT_ALLOWED')
        ap['favorites'].append(dict(page=page, name=a.get('name') or page))
        return dict(count=len(ap['favorites']))
    need(page in pages, 'NOT_FOUND')
    i = pages.index(page)
    if act in ('rename', 'delete'):
        need(page not in fixed, 'NOT_ALLOWED')                              # abm p.36: Message List / Monitor Mode cannot be renamed or deleted
    if act == 'rename':
        need(bool(a.get('name')), 'INVALID_ARGUMENT')
        ap['favorites'][i]['name'] = a['name']
    elif act == 'delete':
        del ap['favorites'][i]
    else:
        j = i - 1 if act == 'move_up' else i + 1
        need(0 <= j < len(pages), 'NOT_ALLOWED')
        ap['favorites'][i], ap['favorites'][j] = ap['favorites'][j], ap['favorites'][i]
    return dict(count=len(ap['favorites']))


def _select(host, a):
    ap, prof = host.ap, host.AP_PROFILE
    m = method(ap, a['method'])
    need(m is not None and m['visible'], 'NOT_FOUND')
    ap['active'] = m['name']
    ap['quick'] = dict(quick_from_method(prof, m), sample_name=ap['quick']['sample_name'], field1=ap['quick']['field1'],
                       field2=ap['quick']['field2'], field3=ap['quick']['field3'])
    prof['apply_method'](host, m, ap['quick'])
    audit(host, f'Method changed to {m["name"]}')
    return dict(method=m['name'])


def method_keys(profile):
    """Schema of methods.configure settings: instrument module fields plus the generic method parts."""
    qids = list(profile['quantities'])
    fld = obj(dict(q={'type': 'string', 'minLength': 1, 'maxLength': 60}, unit={'type': 'string', 'maxLength': 20}, digits=num('1', 0, 6, integer=True)),
              optional=('unit', 'digits'))
    lim = obj(dict(q={'type': 'string', 'minLength': 1, 'maxLength': 60}, lower=nullable(num('1')), upper=nullable(num('1'))))
    props = {f['key']: field_schema(f) for f in profile['method_fields']}
    props.update(mode={'type': 'string', 'enum': [k for k in MEASUREMENT_TYPES if k in profile['modes']]}, mm_count=num('1', 2, 10, integer=True),
                 mf_count=num('1', 2, profile.get('mf_max', 5), integer=True), ts_start=num('°C', profile['t_min_c'], profile['t_max_c']),
                 ts_step=num('°C', 0.01, profile['t_max_c'] - profile['t_min_c']), ts_end=num('°C', profile['t_min_c'], profile['t_max_c']),
                 check=nullable(NAME), layout={'type': 'string', 'enum': [x[0] for x in profile['layouts']]},
                 fields={'type': 'array', 'items': fld, 'maxItems': 8}, output={'type': 'array', 'items': fld, 'maxItems': 30},
                 limits={'type': 'array', 'items': lim, 'maxItems': 20},
                 quick={'type': 'array', 'items': {'type': 'string', 'enum': list(profile['quick_params'])}, 'maxItems': 20},
                 modules={'type': 'object', 'additionalProperties': BOOL}, constants={'type': 'object', 'additionalProperties': num('1')},
                 # row operations of the method dialogs (one row edited at a time: abm p.76-79)
                 set_field=obj(dict(index=num('1', 0, 7, integer=True), q={'type': 'string', 'maxLength': 60}, unit={'type': 'string', 'maxLength': 20},
                                    digits=num('1', 0, 6, integer=True)), optional=('unit', 'digits')),
                 set_output=obj(dict(index=num('1', 0, 29, integer=True), q={'type': 'string', 'minLength': 1, 'maxLength': 60},
                                     unit={'type': 'string', 'maxLength': 20}, digits=num('1', 0, 6, integer=True)), optional=('unit', 'digits')),
                 delete_output=num('1', 0, 29, integer=True), move_output=obj(dict(index=num('1', 0, 29, integer=True), dir={'type': 'string', 'enum': ['up', 'down']})),
                 set_limit=obj(dict(index=num('1', 0, 19, integer=True), q={'type': 'string', 'minLength': 1, 'maxLength': 60}, lower=nullable(num('1')),
                                    upper=nullable(num('1')))),
                 delete_limit=num('1', 0, 19, integer=True), add_quick={'type': 'string', 'enum': list(profile['quick_params'])},
                 delete_quick={'type': 'string', 'enum': list(profile['quick_params'])},
                 move_quick=obj(dict(param={'type': 'string', 'enum': list(profile['quick_params'])}, dir={'type': 'string', 'enum': ['up', 'down']})),
                 set_constant=obj(dict(name=NAME, value=num('1'))), set_module=obj(dict(name=NAME, active=BOOL)))
    props.update(_mode_keys(profile))
    if 'TTS' in profile['modes']:                                          # dma p.38: the temperature table of a method
        props.update(add_tts=num('°C', profile['t_min_c'], profile['t_max_c']), delete_tts=num('1', 0, 29, integer=True))
    props.update(profile.get('method_keys_extra', {}))
    del qids
    return {'type': 'object', 'properties': props, 'additionalProperties': False, 'minProperties': 1}


def _quantity_ok(profile, ap, q):
    return q in profile['quantities'] or (q.startswith('fn:') and any(f['name'] == q[3:] for f in ap['functions'])) or \
        any(x['id'] == q for x in profile.get('extra_quantities', lambda a: [])(ap))


def _mconfigure(host, a):
    ap, prof = host.ap, host.AP_PROFILE
    m = method(ap, a['method'])
    need(m is not None and m['visible'], 'NOT_FOUND')
    need(not m['protected'] or a['settings'].keys() <= {'fields', 'set_field'}, 'NOT_ALLOWED')
    s = dict(a['settings'])
    check(method_keys(prof), s)
    s = _row_ops(prof, ap, m, s)
    for k in ('fields', 'output'):
        for f in s.get(k, []):
            need(_quantity_ok(prof, ap, f['q']), 'INVALID_ARGUMENT')
            q = prof['quantities'].get(f['q'], {})
            need(not f.get('unit') or f['unit'] in q.get('units', []) or f['q'].startswith('fn:'), 'INVALID_ARGUMENT')
    for lim in s.get('limits', []):
        need(_quantity_ok(prof, ap, lim['q']), 'INVALID_ARGUMENT')
        need(lim['lower'] is None or lim['upper'] is None or lim['lower'] < lim['upper'], 'INVALID_ARGUMENT')
    if 'quick' in s:
        need(all(p in s['quick'] for p in prof['quick_locked']), 'NOT_ALLOWED')   # abm p.79: Sample name and Type cannot be deleted
    if s.get('mode') == 'C' or ('check' in s and s['check'] is not None):
        c = s.get('check', m['check'])
        need(c is not None and any(x['name'] == c and (x.get('method') in (None, m['name'])) for x in ap['checks']), 'NOT_FOUND')
    if 'ts_start' in s or 'ts_end' in s or 'ts_step' in s:
        st, sp, en = s.get('ts_start', m['ts_start']), s.get('ts_step', m['ts_step']), s.get('ts_end', m['ts_end'])
        need(abs(en - st) / sp <= 200 + 1e-9, 'INVALID_ARGUMENT')
    for k, v in s.items():
        if k in m['settings']:
            m['settings'][k] = copy.deepcopy(v)
        else:
            m[k] = copy.deepcopy(v)
    if 'layout' in s:                                                        # abm p.76: the fields of the previous layout move to the new one
        n = dict(prof['layouts'])[s['layout']]
        m['fields'] = (m['fields'] + [f for f in prof['default_fields'] if f['q'] not in [x['q'] for x in m['fields']]])[:n]
        while len(m['fields']) < n:
            m['fields'].append(dict(q='', unit='', digits=0))
    prof.get('validate_method', lambda h, x: None)(host, m)
    if m['name'] == ap['active']:
        q = quick_from_method(prof, m)
        ap['quick'] = dict(q, sample_name=ap['quick']['sample_name'], field1=ap['quick']['field1'], field2=ap['quick']['field2'],
                           field3=ap['quick']['field3'])
        prof['apply_method'](host, m, ap['quick'])
    audit(host, f'Method settings of {m["name"]} changed: {", ".join(sorted(s))}')
    return dict(method=m['name'])


def _row_ops(prof, ap, m, s):
    """Turn the row operations of the method dialogs into whole-list settings (validated like a direct list)."""
    def item(x):
        q = prof['quantities'].get(x['q'], {})
        return dict(q=x['q'], unit=x.get('unit', q.get('unit', '')), digits=x.get('digits', q.get('digits', 2)))
    if 'set_field' in s:
        x = s.pop('set_field')
        need(x['index'] < len(m['fields']), 'NOT_FOUND')
        fields = copy.deepcopy(m['fields'])
        fields[x['index']] = item(x) if x['q'] else dict(q='', unit='', digits=0)
        s['fields'] = fields
    out = copy.deepcopy(s.get('output', m['output']))
    if 'set_output' in s:
        x = s.pop('set_output')
        need(x['index'] <= len(out), 'NOT_FOUND')
        if x['index'] == len(out):
            out.append(item(x))                                            # the empty row at the end adds an item (abm p.77)
        else:
            out[x['index']] = item(x)
        s['output'] = out
    if 'delete_output' in s:
        i = s.pop('delete_output')
        need(i < len(out), 'NOT_FOUND')
        del out[i]
        s['output'] = out
    if 'move_output' in s:
        x = s.pop('move_output')
        i, j = x['index'], x['index'] + (-1 if x['dir'] == 'up' else 1)
        need(i < len(out) and 0 <= j < len(out), 'NOT_ALLOWED')
        out[i], out[j] = out[j], out[i]
        s['output'] = out
    lim = copy.deepcopy(s.get('limits', m['limits']))
    if 'set_limit' in s:
        x = s.pop('set_limit')
        need(x['index'] <= len(lim), 'NOT_FOUND')
        row = dict(q=x['q'], lower=x['lower'], upper=x['upper'])
        lim = lim[:x['index']] + [row] + lim[x['index'] + 1:]
        s['limits'] = lim
    if 'delete_limit' in s:
        i = s.pop('delete_limit')
        need(i < len(lim), 'NOT_FOUND')
        del lim[i]
        s['limits'] = lim
    qk = list(s.get('quick', m['quick']))
    if 'add_quick' in s:
        p = s.pop('add_quick')
        need(p not in qk, 'IN_USE')
        qk.append(p)
        s['quick'] = qk
    if 'delete_quick' in s:
        p = s.pop('delete_quick')
        need(p in qk, 'NOT_FOUND')
        qk.remove(p)
        s['quick'] = qk
    if 'move_quick' in s:
        x = s.pop('move_quick')
        need(x['param'] in qk, 'NOT_FOUND')
        i = qk.index(x['param'])
        j = i + (-1 if x['dir'] == 'up' else 1)
        need(0 <= j < len(qk), 'NOT_ALLOWED')
        qk[i], qk[j] = qk[j], qk[i]
        s['quick'] = qk
    if 'set_constant' in s:
        x = s.pop('set_constant')
        f = next((f for f in ap['functions'] if f['name'] == x['name'] and f['type'] == 'Constant'), None)
        need(f is not None, 'NOT_FOUND')
        s['constants'] = dict(s.get('constants', m['constants']), **{x['name']: x['value']})
    if 'add_tts' in s:                                                     # dma p.38: the temperature table, one entry at a time
        tts = sorted(set(s.get('tts_temps', m['tts_temps'])) | {s.pop('add_tts')}, reverse=True)
        s['tts_temps'] = tts
    if 'delete_tts' in s:
        tts = list(s.get('tts_temps', m['tts_temps']))
        i = s.pop('delete_tts')
        need(i < len(tts), 'NOT_FOUND')
        del tts[i]
        s['tts_temps'] = tts
    if 'set_module' in s:
        x = s.pop('set_module')
        s['modules'] = dict(s.get('modules', m['modules']), **{x['name']: x['active']})
    for k, v in s.get('modules', {}).items():                              # modules: the installed optional modules and the constants (abm p.79)
        need(k in prof.get('modules', []) or any(f['name'] == k and f['type'] == 'Constant' for f in ap['functions']), 'NOT_FOUND')
    need(bool(s), 'INVALID_ARGUMENT')
    return s


def _mmanage(host, a):
    ap, prof = host.ap, host.AP_PROFILE
    m = method(ap, a['method'])
    need(m is not None, 'NOT_FOUND')
    act = a['action']
    i = ap['methods'].index(m)
    if act == 'copy':
        need(len(ap['methods']) < prof['max_methods'], 'NOT_ALLOWED')
        n = 1
        while method(ap, f'{m["name"]} ({n})'):
            n += 1
        name = a.get('name') or f'{m["name"]} ({n})'
        need(method(ap, name) is None, 'IN_USE')
        new = copy.deepcopy(m)
        new.update(name=name, protected=False, visible=True)
        ap['methods'].append(new)                                          # gsf p.23: the copy is saved at the last position
        audit(host, f'Method {name} created as a copy of {m["name"]}')
        return dict(method=name)
    if act in ('rename', 'delete'):
        need(m['name'] != ap['active'], 'IN_USE')                          # gsf p.23: not the current method
        need(not m['protected'], 'NOT_ALLOWED')
    if act == 'rename':
        need('name' in a and method(ap, a['name']) is None, 'IN_USE')
        old = m['name']
        m['name'] = a['name']
        for e in ap['sample_list']:
            if e['method'] == old:
                e['method'] = a['name']
    elif act == 'delete':
        need(len(ap['methods']) > 1, 'NOT_ALLOWED')
        need(not any(e['method'] == m['name'] and e['status'] == 'pending' for e in ap['sample_list']), 'IN_USE')
        ap['methods'].remove(m)
    elif act in ('move_up', 'move_down'):
        j = i - 1 if act == 'move_up' else i + 1
        need(0 <= j < len(ap['methods']), 'NOT_ALLOWED')
        ap['methods'][i], ap['methods'][j] = ap['methods'][j], ap['methods'][i]
    elif act in ('show_all', 'hide_all'):                                   # gsf p.23: all methods except the current one
        for x in ap['methods']:
            if x['name'] != ap['active']:
                x['visible'] = act == 'show_all'
    else:
        need('visible' in a, 'INVALID_ARGUMENT')
        need(a['visible'] or m['name'] != ap['active'], 'IN_USE')
        m['visible'] = a['visible']
    audit(host, f'Method {m["name"]}: {act}')
    return dict(method=m['name'])


def _mode_keys(profile):
    """Method / quick keys of the DMA M modes RM (dma p.36-37) and TTS (dma p.38), only for instruments that have them."""
    out = {}
    if 'RM' in profile['modes']:
        out.update(rm_count=num('1', 2, 5, integer=True), rm_dev=nullable(num('1', 0, 1)), rm_save={'type': 'string', 'enum': list(RM_SAVE)})
    if 'TTS' in profile['modes']:
        out.update(tts_temps={'type': 'array', 'items': num('°C', profile['t_min_c'], profile['t_max_c']), 'maxItems': 30})
    return out


def quick_schema(profile):
    props = dict(sample_name={'type': 'string', 'maxLength': 50}, type={'type': 'string', 'enum': [k for k in MEASUREMENT_TYPES if k in profile['modes']]},
                 mm_count=num('1', 2, 10, integer=True), mf_count=num('1', 2, profile.get('mf_max', 5), integer=True),
                 ts_start=num('°C', profile['t_min_c'], profile['t_max_c']), ts_step=num('°C', 0.01, profile['t_max_c'] - profile['t_min_c']),
                 ts_end=num('°C', profile['t_min_c'], profile['t_max_c']), check=nullable(NAME), field1={'type': 'string', 'maxLength': 30},
                 field2={'type': 'string', 'maxLength': 30}, field3={'type': 'string', 'maxLength': 30}, **_mode_keys(profile))
    props.update({f['key']: field_schema(f) for f in profile['method_fields'] if f['key'] in profile['quick_fields']})
    return {'type': 'object', 'properties': props, 'additionalProperties': False, 'minProperties': 1}


def _quick(host, a):
    ap, prof = host.ap, host.AP_PROFILE
    check(quick_schema(prof), a['settings'])
    q = dict(ap['quick'], **copy.deepcopy(a['settings']))
    if q['type'] == 'C':
        need(q['check'] is not None and any(c['name'] == q['check'] for c in ap['checks']), 'NOT_FOUND')
    need(abs(q['ts_end'] - q['ts_start']) / q['ts_step'] <= 200 + 1e-9, 'INVALID_ARGUMENT')
    ap['quick'] = q
    prof['apply_method'](host, method(ap), q)
    audit(host, f'Quick settings changed: {", ".join(sorted(a["settings"]))}')
    return dict(settings=copy.deepcopy(a['settings']))


def _sample_list(host, a):
    ap = host.ap
    act, sl = a['action'], ap['sample_list']
    if act in ('add', 'update'):
        need('entry' in a, 'INVALID_ARGUMENT')
        e = a['entry']
        need(e.get('type', 'S') in host.AP_PROFILE['modes'], 'INVALID_ARGUMENT')
        m = method(ap, e['method'])
        need(m is not None and m['visible'], 'NOT_FOUND')
        new = dict(name=e['name'], method=e['method'], type=e.get('type', 'S'), field1=e.get('field1', ''), field2=e.get('field2', ''),
                   field3=e.get('field3', ''), status='pending', record=None)
        if act == 'add':
            sl.append(new)
            if len(sl) > 200:                                              # abm p.82: the 201st entry overwrites the oldest
                del sl[0]
        else:
            need(a.get('index') is not None and 0 <= a['index'] < len(sl), 'NOT_FOUND')
            need(sl[a['index']]['status'] == 'pending', 'NOT_ALLOWED')    # measured samples cannot be edited (abm p.83)
            sl[a['index']] = new
        _list_warnings(host)
    elif act == 'delete':
        need(a.get('index') is not None and 0 <= a['index'] < len(sl), 'NOT_FOUND')
        need(sl[a['index']]['status'] == 'pending', 'NOT_ALLOWED')
        del sl[a['index']]
    elif act == 'clear':
        ap['sample_list'] = []
        ap['messages'] = []                                                # abm p.25, p.83: the message list is cleared with the sample list
    elif act == 'save':
        need(a.get('template') is not None, 'INVALID_ARGUMENT')
        t = ap['templates'][a['template']]
        t['entries'] = [dict(e, status='pending', record=None) for e in sl]
        if a.get('name'):
            t['name'] = a['name']
    elif act == 'load':
        need(a.get('template') is not None, 'INVALID_ARGUMENT')
        ap['sample_list'] = copy.deepcopy(ap['templates'][a['template']]['entries'])
    else:
        need(a.get('template') is not None and a.get('name'), 'INVALID_ARGUMENT')
        ap['templates'][a['template']]['name'] = a['name']
    audit(host, f'Sample list: {act}')
    return dict(count=len(ap['sample_list']))


def _list_warnings(host):
    ap = host.ap
    s = ap['settings']
    if s['samples.warn_no_name'] and any(not e['name'] for e in ap['sample_list'] if e['status'] == 'pending'):
        add_message(host, 'warning', 'The sample list contains a sample without a name.')
    if s['samples.warn_temperatures']:
        temps = {host.AP_PROFILE['method_temperature'](method(ap, e['method'])) for e in ap['sample_list'] if e['status'] == 'pending'}
        if len(temps) > 1:
            add_message(host, 'warning', 'The sample list contains methods with different measuring temperatures.')


def _monitor(host, a):
    ap = host.ap
    ap['monitor'] = a['active']
    if a['active']:
        ap['frozen'] = ap['frozen_rec'] = None                             # abm p.85: unfreeze the result
    return dict(active=a['active'])


COMPARATORS_OF = {'Print/Export Status': ('printed/exported', 'not printed/exported'), 'Date Range': ('equals', 'not equal'),
                  'Measuring Value': ('larger than', 'smaller than', 'equals', 'not equal')}


def _filter(host, a):
    lst = host.ap['filters'][a['view']]
    if a['action'] == 'add':
        c = a.get('criterion')
        need(c is not None and len(lst) < 8, 'INVALID_ARGUMENT')
        crit, comp = c['criterion'], c['comparator']
        need(comp in COMPARATORS_OF.get(crit, ('equals', 'not equal', 'containing', 'not containing')), 'INVALID_ARGUMENT')
        need(crit != 'Date Range' or c.get('value') in DATE_RANGES, 'INVALID_ARGUMENT')
        lst.append(dict(criterion=crit, comparator=comp, value=c.get('value', '')))
    elif a['action'] == 'delete':
        need(a.get('index') is not None and a['index'] < len(lst), 'NOT_FOUND')
        del lst[a['index']]
    else:
        lst.clear()
    return dict(count=len(filtered(host, a['view'])))


def _delete(host, a):
    ap = host.ap
    key = 'data' if a['kind'] == 'measured' else 'check_data'
    if ap['settings']['security.increased']:                               # abm Table 9.2 p.55: only exported data can be deleted
        need(False, 'NOT_EXPORTED')
    if a['id'] is not None:
        rec = next((r for r in ap[key] if r['id'] == a['id']), None)
        need(rec is not None, 'NOT_FOUND')
        need(not any(e['record'] == rec['id'] for e in ap['sample_list']), 'IN_USE')   # gsf p.27: samples still in the sample list
        gone = [rec]
    else:
        gone = filtered(host, 'delete') if key == 'data' else list(ap[key])
        gone = [r for r in gone if not any(e['record'] == r['id'] for e in ap['sample_list'])] if key == 'data' else gone
    ids = {r['id'] for r in gone}
    ap[key] = [r for r in ap[key] if r['id'] not in ids]
    if key == 'data' and ap['frozen'] in ids:
        ap['frozen'] = ap['frozen_rec'] = None
    audit(host, f'{len(gone)} {a["kind"]} data deleted ({", ".join(r.get("uid", str(r["id"])) for r in gone[:10])})')
    return dict(deleted=len(gone))


def _sign(host, a):
    ap = host.ap
    s = ap['settings']
    need(s['security.esign'], 'NOT_ALLOWED')
    rec = next((r for r in ap['data'] if r['id'] == a['id']), None)
    need(rec is not None, 'NOT_FOUND')
    u = user_record(ap, ap['user'])
    role = u['role']
    need(role != 'None', 'PERMISSION_DENIED')
    if s['security.esign_password']:
        need(_pw_equal(host, u['password'], a.get('password', '')), 'WRONG_PASSWORD')
    roles = ['submitter', 'reviewer', 'approver'] if s['security.esign_levels'] == '3 roles' else ['submitter', 'approver']
    mine = role.lower()
    need(mine in roles, 'NOT_ALLOWED')
    if mine == 'submitter':
        need(rec['user'] == u['name'], 'NOT_ALLOWED')                       # abm p.60: only the user who measured signs as submitter
    need(rec['signatures'][mine] is None, 'NOT_ALLOWED')                   # a signature cannot be changed
    k = roles.index(mine)
    if k and any(rec['signatures'][r] is None for r in roles[:k]):
        need(s['security.esign_substitute'], 'NOT_ALLOWED')
    for r in roles[:k + 1]:
        if rec['signatures'][r] is None:
            rec['signatures'][r] = dict(positive=a['positive'], user=u['name'], time=device_iso(host))
    if a.get('comment'):
        rec['comments'].append(dict(user=u['name'], text=a['comment']))
    audit(host, f'Sample {rec["uid"]} signed {"positive" if a["positive"] else "negative"} as {role}')
    return dict(signatures=copy.deepcopy(rec['signatures']))


def _audit_delete(host, a):
    ap = host.ap
    n = sum(1 for e in ap['audit'] if e['exported'])
    ap['audit'] = [e for e in ap['audit'] if not e['exported']]            # abm p.57: entries not yet exported cannot be deleted
    return dict(deleted=n)


def _lists(host, a):
    ap = host.ap
    name, act = a['list'], a['action']
    lst = ap[name]
    idx = a.get('index')
    if act in ('move_up', 'move_down', 'delete'):
        need(idx is not None and 0 <= idx < len(lst), 'NOT_FOUND')
    if act == 'delete':
        need(not (name in ('printers', 'reports') and lst[idx].get('default')), 'NOT_ALLOWED')   # the default printer / report cannot be deleted
        del lst[idx]
    elif act in ('move_up', 'move_down'):
        j = idx - 1 if act == 'move_up' else idx + 1
        need(0 <= j < len(lst), 'NOT_ALLOWED')
        lst[idx], lst[j] = lst[j], lst[idx]
    else:
        need('item' in a, 'INVALID_ARGUMENT')
        it = a['item']
        if name == 'printers':
            check(obj(dict(name=NAME, type={'type': 'string', 'enum': list(PRINTER_TYPES)}, default=BOOL, format={'type': 'string', 'enum': ['A4', 'Letter']},
                           port={'type': 'string', 'enum': ['COM1', 'USB', 'Network']}, color=BOOL, ip={'type': 'string', 'maxLength': 15}),
                      optional=('format', 'port', 'color', 'ip')), it)
            need(idx is not None or len(lst) < 5, 'NOT_ALLOWED')              # up to 5 printers (abm p.39)
        elif name == 'extrapolation':
            check(obj(dict(value=num('1', -100, 300), unit={'type': 'string', 'enum': ['°C', '°F']})), it)
            need(idx is not None or len(lst) < 10, 'NOT_ALLOWED')
        elif name == 'shares':
            check(obj(dict(name=NAME, path={'type': 'string', 'maxLength': 64}, user={'type': 'string', 'maxLength': 30},
                           password={'type': 'string', 'maxLength': 30}), optional=('password',)), it)
            need(idx is not None or len(lst) < 10, 'NOT_ALLOWED')
        elif name == 'reports':
            check(obj(dict(name=NAME, type={'type': 'string', 'enum': list(REPORT_TYPES)}, default=BOOL, gxp=BOOL,
                           orientation={'type': 'string', 'enum': ['Portrait', 'Landscape']}, printed_by=BOOL), optional=('gxp', 'orientation', 'printed_by')), it)
            need(idx is not None or len(lst) < 10, 'NOT_ALLOWED')             # up to 10 reports (abm p.43)
        else:
            check(obj(dict(q={'type': 'string', 'minLength': 1, 'maxLength': 60})), it)
            need(_quantity_ok(host.AP_PROFILE, ap, it['q']), 'INVALID_ARGUMENT')
            it = it['q']
        if name not in ('browser', 'extrapolation'):
            clash = [i for i, x in enumerate(lst) if x['name'] == it['name']]
            need(not clash or clash == [idx], 'IN_USE')
            it = dict(it)
            if it.get('default'):
                for x in lst:
                    x['default'] = False
        if idx is None:
            lst.append(it)
        else:
            need(0 <= idx <= len(lst), 'NOT_FOUND')
            if idx == len(lst):
                lst.append(it)
            else:
                if name != 'browser' and lst[idx].get('default') and not it.get('default'):
                    it['default'] = True                                    # one default stays (abm p.39)
                lst[idx] = it
    audit(host, f'{name}: {act}')
    return dict(count=len(lst))


def _fsave(host, a):
    ap, prof = host.ap, host.AP_PROFILE
    f = copy.deepcopy(a['function'])
    t = f['type']
    inputs = f.get('inputs', [])
    for q in inputs:
        need(_quantity_ok(prof, ap, q) and prof['quantities'].get(q, {}).get('kind') != 'text', 'INVALID_ARGUMENT')
    need(len(inputs) == {'Constant': 0, 'Linear Function': 1, 'Polynomial (1D)': 1, 'Polynomial (2D)': 2, 'Table': 1}.get(t, len(inputs)), 'INVALID_ARGUMENT')
    if t == 'Constant':
        need('value' in f, 'INVALID_ARGUMENT')
    elif t == 'Linear Function':
        need('offset' in f and 'factor' in f, 'INVALID_ARGUMENT')
    elif t.startswith('Polynomial'):
        f['coefficients'] = (list(f.get('coefficients', [])) + [0.0] * 10)[:7 if t == 'Polynomial (1D)' else 10]
    elif t == 'Table':
        f['table'] = [list(p) for p in f.get('table', [])]
        need(len({p[0] for p in f['table']}) == len(f['table']), 'INVALID_ARGUMENT')   # pairs may follow row by row (functions.table_edit)
    else:
        need(1 <= len(inputs) <= 2 and len(f.get('variables', [])) == len(inputs) and f.get('formula'), 'INVALID_ARGUMENT')
        need(all(re.match(r'^[A-Za-z_]\w*$', v) for v in f['variables']), 'INVALID_ARGUMENT')
        need(formula_ok(f['formula'], set(f['variables'])), 'INVALID_ARGUMENT')
    f.setdefault('quantity', ''), f.setdefault('unit', ''), f.setdefault('comment', ''), f.setdefault('protected', False)
    f['owner'] = ap['user']
    orig = a.get('original')
    names = [x['name'] for x in ap['functions']]
    if orig is None:
        need(f['name'] not in names, 'IN_USE')
        need(len(ap['functions']) < prof.get('max_functions', 80), 'NOT_ALLOWED')
        ap['functions'].append(f)
    else:
        need(orig in names, 'NOT_FOUND')
        old = ap['functions'][names.index(orig)]
        need(not old['protected'] or old['owner'] == ap['user'], 'NOT_ALLOWED')   # gsf p.35: protected against changes by other users
        if t == 'Table' and 'table' not in a['function'] and old['type'] == 'Table':
            f['table'] = copy.deepcopy(old['table'])                        # the pairs are edited row by row
        need(f['name'] == orig or f['name'] not in names, 'IN_USE')
        ap['functions'][names.index(orig)] = f
        if f['name'] != orig:
            _rename_quantity(ap, 'fn:' + orig, 'fn:' + f['name'])
    audit(host, f'User function {f["name"]} saved')
    return dict(name=f['name'])


def _rename_quantity(ap, old, new):
    for m in ap['methods']:
        for k in ('fields', 'output', 'limits'):
            for x in m[k]:
                if x['q'] == old:
                    x['q'] = new
    ap['browser'] = [new if q == old else q for q in ap['browser']]


def _fdelete(host, a):
    ap = host.ap
    f = next((x for x in ap['functions'] if x['name'] == a['name']), None)
    need(f is not None, 'NOT_FOUND')
    q = 'fn:' + f['name']
    need(not any(lim['q'] == q for m in ap['methods'] for lim in m['limits']), 'IN_USE')   # abm p.136: not when used as a limit
    ap['functions'].remove(f)
    for m in ap['methods']:
        m['fields'] = [x if x['q'] != q else dict(q='', unit='', digits=0) for x in m['fields']]
        m['output'] = [x for x in m['output'] if x['q'] != q]
    ap['browser'] = [x for x in ap['browser'] if x != q]
    audit(host, f'User function {f["name"]} deleted')
    return dict(name=f['name'])


def _calc(host, a):
    ap, prof = host.ap, host.AP_PROFILE
    f = next((x for x in ap['functions'] if x['name'] == a['function']), None)
    need(f is not None, 'NOT_FOUND')
    need(len(a['inputs']) == len(f.get('inputs', [])), 'INVALID_ARGUMENT')
    v = function_value(f, dict(zip(f.get('inputs', []), a['inputs'])), method(ap)['constants'])
    ap['calculator'] = dict(function=f['name'], inputs=list(a['inputs']), value=v)
    del prof
    return dict(value=v)


def _calc_group(host, a):
    ap = host.ap
    vals = {q: float(v) for q, v in a['inputs'].items()}
    out = {f['name']: function_value(f, vals, method(ap)['constants']) for f in ap['functions']}
    ap['calculator'] = dict(function=None, inputs=dict(vals), values=out)
    return dict(values=out)


def _table_edit(host, a):
    ap = host.ap
    f = next((x for x in ap['functions'] if x['name'] == a['name']), None)
    need(f is not None and f['type'] == 'Table', 'NOT_FOUND')
    need(not f['protected'] or f['owner'] == ap['user'], 'NOT_ALLOWED')
    if a['action'] == 'add':
        need('x' in a and 'y' in a and len(f['table']) < 1000, 'INVALID_ARGUMENT')
        need(all(p[0] != a['x'] for p in f['table']), 'IN_USE')
        f['table'] = sorted(f['table'] + [[a['x'], a['y']]])
    else:
        need(a.get('index') is not None and a['index'] < len(f['table']), 'NOT_FOUND')
        del f['table'][a['index']]
    audit(host, f'User function {f["name"]}: table {a["action"]}')
    return dict(pairs=len(f['table']))


def _checks(host, a):
    ap, prof = host.ap, host.AP_PROFILE
    c = a['check']
    names = [x['name'] for x in ap['checks']]
    if a['action'] == 'delete':
        need(c.get('name') in names, 'NOT_FOUND')
        x = ap['checks'][names.index(c['name'])]
        need(not x.get('factory'), 'NOT_ALLOWED')                           # the predefined water check stays (abm p.75)
        need(not any(m['check'] == x['name'] for m in ap['methods']), 'IN_USE')
        ap['checks'].remove(x)
        return dict(name=x['name'])
    check(prof['check_schema'], c)
    c = dict(c, tolerances=[t for t in c.get('tolerances', []) if t.get('q')])   # rows without a quantity are empty rows of the wizard
    for t in c['tolerances']:
        need(t.get('lower') is not None and t.get('upper') is not None, 'INVALID_ARGUMENT')
    orig = a.get('original')
    if c['kind'] == 'Custom Check':
        need(method(ap, c.get('method')) is not None, 'NOT_FOUND')
    if c['kind'] == 'Reference Standard Check':
        need(any(s['name'] == c.get('standard') for s in ap['standards']), 'NOT_FOUND')
    for t in c.get('tolerances', []):
        need(_quantity_ok(prof, ap, t['q']) and t['lower'] < t['upper'], 'INVALID_ARGUMENT')
    new = dict(prof['check_default'], **copy.deepcopy(c))
    if orig is None:
        need(c['name'] not in names, 'IN_USE')
        new['last'] = None
        ap['checks'].append(new)
    else:
        need(orig in names, 'NOT_FOUND')
        old = ap['checks'][names.index(orig)]
        need(c['name'] == orig or c['name'] not in names, 'IN_USE')
        new['last'], new['factory'] = old.get('last'), old.get('factory', False)
        if new['factory']:
            need(new['kind'] == old['kind'], 'NOT_ALLOWED')
        ap['checks'][names.index(orig)] = new
        for m in ap['methods']:
            if m['check'] == orig:
                m['check'] = c['name']
    audit(host, f'Check {c["name"]} saved')
    return dict(name=c['name'])


def _standards(host, a):
    ap, prof = host.ap, host.AP_PROFILE
    s = a['standard']
    names = [x['name'] for x in ap['standards']]
    if a['action'] == 'delete':
        need(s.get('name') in names, 'NOT_FOUND')
        x = ap['standards'][names.index(s['name'])]
        need(not any(c.get('standard') == x['name'] for c in ap['checks']), 'IN_USE')
        ap['standards'].remove(x)
        return dict(name=x['name'])
    check(prof['standard_schema'], s)
    orig = a.get('original')
    if orig is None:
        need(s['name'] not in names, 'IN_USE')
        ap['standards'].append(copy.deepcopy(s))
    else:
        need(orig in names, 'NOT_FOUND')
        need(s['name'] == orig or s['name'] not in names, 'IN_USE')
        ap['standards'][names.index(orig)] = copy.deepcopy(s)
        for c in ap['checks']:
            if c.get('standard') == orig:
                c['standard'] = s['name']
    audit(host, f'Reference standard {s["name"]} saved')
    return dict(name=s['name'])


def _touch_cal(host, a):
    pts = a['points']
    targets = host.AP_PROFILE['touch_targets']                              # the four crosses shown by the calibration page
    need(all(abs(p[0] - t[0]) <= 40 and abs(p[1] - t[1]) <= 40 for p, t in zip(pts, targets)), 'NOT_ALLOWED')   # a tap far off the cross is refused
    host.ap['touch_cal'] = dict(time=device_iso(host), points=copy.deepcopy(pts))
    return dict(calibrated=True)


def _firmware(host, a):
    host.ap['firmware_checked'] = device_iso(host)                         # abm p.105 'Internal': modules already at the supported version
    return dict(up_to_date=True)


def _start(host, a):
    return run_start(host, dict(kind='sample'))


def _continue(host, a):
    return run_continue(host)


def _check_start(host, a):
    return run_start(host, dict(kind='check', check=a['check']))


_HANDLERS = {'settings.configure': _settings, 'clock.set': _clock, 'user.logon': _logon, 'user.logoff': _logoff, 'users.save': _users_save,
             'users.delete': _users_delete, 'users.set_password': _users_password, 'groups.manage': _groups, 'messages.acknowledge': _ack,
             'favorites.configure': _favorites, 'methods.select': _select, 'methods.configure': _mconfigure, 'methods.manage': _mmanage,
             'quick.configure': _quick, 'sample_list.edit': _sample_list, 'measurement.start': _start, 'measurement.continue': _continue,
             'monitor.set': _monitor, 'data.filter': _filter, 'data.delete': _delete, 'data.sign': _sign, 'audit.delete': _audit_delete,
             'lists.edit': _lists, 'functions.save': _fsave, 'functions.delete': _fdelete, 'calculator.evaluate': _calc, 'checks.manage': _checks,
             'standards.manage': _standards, 'calculator.group': _calc_group, 'functions.table_edit': _table_edit, 'check.start': _check_start, 'touchscreen.calibrate': _touch_cal, 'firmware.check': _firmware}


# ---------------------------------------------------------------- measurement bookkeeping shared by the instruments
def sample_name_for(host, base, number):
    """Automatic sample name additions after the measurement (abm p.45, p.84): prefix / postfix text plus a data field."""
    ap = host.ap
    s = ap['settings']

    def part(field):
        if field == 'Date and Time':
            return device_dt(host).strftime('%Y-%m-%d %H:%M')
        if field == 'User Name':
            return ap['user'] or ''
        if field == 'Sample Number':
            return str(number)
        if field == 'Method':
            return ap['active']
        return ''
    name = ''.join(x for x in (s['samples.prefix'], part(s['samples.prefix_field']), base, part(s['samples.postfix_field']), s['samples.postfix']))
    return name[:50] or f'Sample {number}'


def limits_ok(m, values):
    bad = []
    for lim in (m['limits'] if m else []):
        v = values.get(lim['q'])
        if v is None:
            continue
        if (lim['lower'] is not None and v < lim['lower']) or (lim['upper'] is not None and v > lim['upper']):
            bad.append(lim['q'])
    return bad


def finish_measurement(host, run, readings):
    """Store the readings of a run (one record per reading; MM / MF also an average record), apply the method limits, naming, beep and
    automatic printout (abm p.77-78, p.86; gsf p.22)."""
    ap, prof = host.ap, host.AP_PROFILE
    m = method(ap, run['method'])
    number = ap['sample_no']
    name = sample_name_for(host, run['sample_name'], number)
    recs = []
    rdev = None
    if run['type'] == 'RM' and run.get('rm_converged'):                   # dma p.36-37: save all, the valid two or the final one only
        xs = [r['values'].get(prof['main_quantity']) for r in readings[-2:]]
        rdev = round(abs(xs[1] - xs[0]), 7) if len(xs) == 2 and None not in xs else None   # repeat deviation of the two agreeing results
        keep = {'all': readings, 'valid two only': readings[-2:], 'final one only': readings[-1:]}[run.get('rm_save', 'all')]
        readings = keep
    multi = run['type'] in ('MM', 'MF', 'RM') and len(readings) > 1
    kind = 'scan' if run['type'] in ('TS', 'TTS') else 'measurement'
    for k, r in enumerate(readings):
        vals = record_values(prof, ap, r['values'], m)
        if rdev is not None and not multi and 'repeat_dev' in vals:
            vals['repeat_dev'] = rdev
        bad = limits_ok(m, vals)
        rec = store_record(host, dict(time=device_iso(host), sample_name=name + (f' #{k + 1}' if multi or kind == 'scan' else ''), method=run['method'],
                                      type=run['type'], user=ap['user'] or '-', values=vals, condition=r['condition'],
                                      error_state='Error' if r['condition'] != 'valid' or bad else 'No Error', limits_failed=bad, kind=kind,
                                      sub=multi, sample_no=number, fields=[run.get('field1', ''), run.get('field2', ''), run.get('field3', '')]))
        if rec is None:
            break
        recs.append(rec)
        if bad:
            add_message(host, 'warning', f'{name}: result outside the limits ({", ".join(quantity_label(prof, ap, q) for q in bad)}).')
        if r['condition'] != 'valid':
            add_message(host, r.get('level', 'error'), f'{name}: {r["condition"]}.')
        if r.get('camera_image'):
            rec['camera_image'] = True
    if run['type'] == 'RM' and not run.get('rm_converged'):
        add_message(host, 'warning', f'{name}: the deviation of the repeated measurements is too high.')
    if multi and recs:
        avg = {}
        for q in recs[0]['values']:
            xs = [x['values'][q] for x in recs if isinstance(x['values'].get(q), (int, float)) and not isinstance(x['values'].get(q), bool)]
            avg[q] = sum(xs) / len(xs) if xs and len(xs) == len(recs) else recs[-1]['values'][q]
        if rdev is not None and 'repeat_dev' in avg:
            avg['repeat_dev'] = rdev
        bad = limits_ok(m, avg)
        ok = all(x['condition'] == 'valid' for x in recs)
        rec = store_record(host, dict(time=device_iso(host), sample_name=name, method=run['method'], type=run['type'], user=ap['user'] or '-',
                                      values=avg, condition='valid' if ok else recs[-1]['condition'], error_state='Error' if bad or not ok else 'No Error',
                                      limits_failed=bad, kind='measurement', sub=False, avg=True, sample_no=number,
                                      fields=[run.get('field1', ''), run.get('field2', ''), run.get('field3', '')]))
        if rec:
            recs.append(rec)
    ap['sample_no'] += 1
    if recs:
        ap['frozen'] = recs[-1]['id']
        ap['frozen_rec'] = copy.deepcopy(recs[-1])
        ap['monitor'] = False
    if ap['settings']['control.beep_measurement']:
        ap['beeps'] += 1
    if ap['settings']['printout.auto'] and recs:
        if not ap['printers']:
            add_message(host, 'warning', 'Automatic printout: no printer available.')
        else:
            ap['auto_printouts'] += 1
            add_message(host, 'warning', 'Automatic printout: the printer is not connected.')
    if run.get('list_index') is not None and run['list_index'] < len(ap['sample_list']):
        e = ap['sample_list'][run['list_index']]
        e['status'] = 'measured' if recs and all(r['error_state'] == 'No Error' for r in recs) else 'error'
        e['record'] = recs[-1]['id'] if recs else None
    audit(host, f'Measurement of {name} ({run["method"]}) finished: {len(recs)} result(s)')
    return recs


def finish_check(host, run, reading):
    """A check: compare the measured value with the reference / tolerances and store the check data (abm p.63-65)."""
    ap, prof = host.ap, host.AP_PROFILE
    c = next((x for x in ap['checks'] if x['name'] == run['check']), None)
    if c is None:
        return None
    res = prof['evaluate_check'](host, c, reading)
    rec = dict(id=ap['next_check'], time=device_iso(host), check=c['name'], kind=c['kind'], method=run['method'], user=ap['user'] or '-',
               temperature=reading['values'].get(prof['temperature_quantity']), condition=reading['condition'], **res)
    ap['next_check'] += 1
    if len(ap['check_data']) >= prof.get('check_capacity', 100):
        if ap['settings']['memory.auto_delete_checks']:
            del ap['check_data'][:10]
        else:
            add_message(host, 'error', 'Check data memory is full.')
            return None
    ap['check_data'].append(rec)
    c['last'] = dict(time=rec['time'], result=rec['result'], user=rec['user'])
    if rec['result'] != 'passed':
        add_message(host, 'warning', f'Check "{c["name"]}" not passed.')
    audit(host, f'Check {c["name"]}: {rec["result"]}')
    if ap['settings']['control.beep_measurement']:
        ap['beeps'] += 1
    return rec


def check_due(host, c):
    """Days until the next execution of a check with a reminder (None without a reminder); negative when overdue."""
    if not c.get('reminder'):
        return None
    if not c.get('last'):
        return -1
    last = datetime.datetime.fromisoformat(c['last']['time'])
    return c['interval_days'] - (device_dt(host) - last).total_seconds() / 86400.0


# ---------------------------------------------------------------- measurement runs (abm p.75-76, p.86; Appendix C p.133)
# A run: temperature equilibration (with temperature control) -> hold time -> the instrument's reading (its stability time) -> next reading
# (MM), next fill (MF, the operator exchanges the sample and confirms), next temperature (TS) or the end; the timeout aborts a reading cycle
# that has not finished.  The physics stays in the runtime: profile hooks set_temperature / temperature_ok / begin_reading / resume / reading.
def run_start(host, spec):
    """measurement.start / check.start / adjustment.start: build the run from the quick settings, the sample list entry, the check or the
    adjustment standards, check the instrument (profile 'precheck') and start the first cycle."""
    ap, prof = host.ap, host.AP_PROFILE
    need(ap['run'] is None, 'BUSY')
    need(len(ap['data']) < prof['memory_capacity'] or ap['settings']['memory.auto_delete'] or spec['kind'] == 'adjust', 'MEMORY_FULL')
    q = ap['quick']
    m = method(ap)
    run = dict(kind=spec['kind'], method=m['name'], type=q['type'], sample_name=q['sample_name'], field1=q['field1'], field2=q['field2'],
               field3=q['field3'], list_index=None, check=None, k=0, readings=[], stage='equilibrating', cycle_t=host.time_s, stage_t=host.time_s,
               hold_left=0.0, ti=0, prompt=None, adjust=spec.get('adjust'))
    if spec['kind'] == 'sample' and ap['settings']['samples.mode'] == 'Sample List':
        idx = next((i for i, e in enumerate(ap['sample_list']) if e['status'] == 'pending'), None)
        need(idx is not None, 'NOT_FOUND')                                 # nothing left to measure in the sample list (assumption)
        e = ap['sample_list'][idx]
        m = method(ap, e['method'])
        need(m is not None, 'NOT_FOUND')
        q = dict(quick_from_method(prof, m), sample_name=e['name'], type=e['type'], field1=e['field1'], field2=e['field2'], field3=e['field3'])
        run.update(method=m['name'], type=e['type'], sample_name=e['name'], field1=e['field1'], field2=e['field2'], field3=e['field3'], list_index=idx)
    if spec['kind'] == 'sample' and run['type'] == 'C':
        spec = dict(kind='check', check=q['check'] or m['check'])
        run['kind'] = 'check'
    if spec['kind'] == 'check':
        c = next((x for x in ap['checks'] if x['name'] == spec.get('check')), None)
        need(c is not None, 'NOT_FOUND')
        run.update(check=c['name'], type='C', sample_name=c['name'])
        if c['kind'] == 'Custom Check':
            m = method(ap, c['method'])
            need(m is not None, 'NOT_FOUND')
            run['method'] = m['name']
            q = quick_from_method(prof, m)
    s = ap['settings']
    if spec['kind'] == 'sample':
        need(not s['samples.mandatory_name'] or run['sample_name'].strip(), 'MANDATORY_FIELD')
        for i in (1, 2, 3):
            need(not (s[f'samples.field{i}'] and s[f'samples.mandatory_field{i}']) or run[f'field{i}'].strip(), 'MANDATORY_FIELD')
        for c in ap['checks']:                                              # gsf p.24-25: prohibit measurements after the check interval expired
            due = check_due(host, c)
            need(not (c.get('prohibit') and due is not None and due < 0), 'CHECK_OVERDUE')
    run['params'] = prof['run_params'](host, m, q, run)
    if run['type'] == 'TS' and run['kind'] == 'sample':
        st, sp, en = q['ts_start'], q['ts_step'], q['ts_end']
        n = int(math.floor(abs(en - st) / sp + 1e-9)) + 1
        run['temps'] = [round(st + math.copysign(sp, en - st) * i, 6) for i in range(n)]
    elif run['type'] == 'TTS' and run['kind'] == 'sample':
        need(bool(q.get('tts_temps')), 'NOT_FOUND')                       # dma p.38: the temperature table of the method
        run['temps'] = list(q['tts_temps'])
    else:
        run['temps'] = [run['params']['temp_c']]
    counts = dict(MM=q['mm_count'], MF=q['mf_count'], RM=q.get('rm_count', 3))
    run['n'] = counts.get(run['type'], len(run['temps'])) if run['kind'] == 'sample' \
        else len(run['adjust']['standards']) if run['kind'] == 'adjust' and run['adjust'].get('standards') else 1
    run.update(rm_save=q.get('rm_save', 'all'), rm_dev=q.get('rm_dev'))
    pre = prof.get('pre_run', lambda h, r: None)(host, run)             # e.g. the auto air check before a measurement (dma p.24)
    if pre is None:
        prof['precheck'](host, run)
    ap['run'] = run
    out = prof['begin'](host, run)
    run['op'] = host.operation_id                                          # the run's operation (owner_guard), kept while it waits for a fill
    if pre is not None:
        run['pre'] = pre
        run.update(stage='await_fill', prompt=prof['pre_prompt'](host, run))
        host.phase = 'idle' if host.phase == 'running' else host.phase
        return out
    if prof.get('first_fill', lambda h, r: False)(host, run):            # the first standard is filled after the start (dma p.30)
        run.update(stage='await_fill', prompt=prof['fill_prompt'](host, run))
        host.phase = 'idle' if host.phase == 'running' else host.phase
        return out
    _cycle(host, run)
    return out


def _cycle(host, run):
    """Start a reading cycle: temperature of this cycle, then equilibration (only with temperature control) or straight to the hold time."""
    prof = host.AP_PROFILE
    t = run['temps'][min(run['ti'], len(run['temps']) - 1)]
    if run['kind'] == 'adjust' and run['adjust'].get('standards'):
        t = run['adjust']['standards'][min(run['k'], len(run['adjust']['standards']) - 1)].get('temperature_c', t)
    run['temp_c'] = t
    run['cycle_t'] = run['stage_t'] = host.time_s
    if run['params']['control']:
        prof['set_temperature'](host, t)
        run['stage'] = 'equilibrating'
    else:
        run.update(stage='hold', hold_left=run['params']['hold_s'])


def run_tick(host, h):
    """Advance a run by one physics substep of h seconds (called by the runtime's step after the physics of that substep)."""
    ap, prof = host.ap, host.AP_PROFILE
    run = ap['run']
    if run is None or run['stage'] == 'await_fill':
        return
    if host.phase != 'running':                                           # stopped (<Stop>) or switched off
        ap['run'] = None
        return
    if run['stage'] == 'equilibrating':
        if prof['temperature_ok'](host, run['params']['tol_c']):
            run.update(stage='hold', hold_left=run['params']['hold_s'], stage_t=host.time_s, eq_t=host.time_s)
            if run['hold_left'] <= 1e-9:
                _reading(host, run)
            return
    elif run['stage'] == 'hold':
        run['hold_left'] -= h
        if run['hold_left'] <= 1e-9:
            _reading(host, run)
            return
    elif run['stage'] == 'empty':                                         # no sample on the prism: the reading finds nothing
        run['empty_left'] -= h
        if run['empty_left'] <= 1e-9:
            host.phase = 'succeeded'                                       # the reading ends as a physics reading would
            run_reading_done(host, prof['reading'](host, empty=True))
            return
    timeout = run['params'].get('timeout_s')
    if timeout and run['params'].get('timeout_from') == 'equilibrium':     # dma p.35: the timeout count starts at temperature equilibrium
        late = run.get('eq_t') is not None and host.time_s - run['eq_t'] >= timeout - 1e-9 and run['stage'] in ('hold', 'measuring', 'empty')
    else:
        late = timeout and host.time_s - run['cycle_t'] >= timeout - 1e-9 and run['stage'] in ('equilibrating', 'hold')
    if late:
        host.phase = 'cancelled'
        ap['run'] = None
        add_message(host, 'error', prof['timeout_text'])                  # abm p.76: aborted when not finished within the timeout
        audit(host, f'Measurement aborted: {prof["timeout_text"]}')


def _reading(host, run):
    prof = host.AP_PROFILE
    run['stage_t'] = host.time_s
    if prof['begin_reading'](host, run['params']['stability_s']):
        run['stage'] = 'measuring'
    else:
        run.update(stage='empty', empty_left=run['params']['stability_s'])


def run_reading_done(host, reading):
    """One reading finished (physics completion or an empty prism): next reading, next fill, next temperature, or the end of the run."""
    ap, prof = host.ap, host.AP_PROFILE
    run = ap['run']
    if run is None:                                                       # a measurement through acquisition.start: stored like an S measurement
        prof['direct_done'](host, reading)
        return
    if run.get('pre'):                                                    # the pre-run check: then the operator fills the sample
        ok = prof['pre_done'](host, run, reading)
        run['pre']['state'] = 'passed' if ok else 'failed'
        run.update(stage='await_fill', prompt=prof['pre_prompt'](host, run))
        host.phase = 'idle'
        return
    run['readings'].append(dict(reading, temp_c=run['temp_c']))
    run['k'] += 1
    if run['kind'] == 'sample' and run['type'] == 'RM':                   # dma p.36-37: stop when two subsequent results agree
        q = prof['main_quantity']
        xs = [r['values'].get(q) for r in run['readings'][-2:]]
        dev = run.get('rm_dev') if run.get('rm_dev') is not None else run['params'].get('rm_dev_default', 0.0)
        if len(xs) == 2 and None not in xs and abs(xs[1] - xs[0]) <= dev + 1e-12:
            run['rm_converged'] = True
            run['k'] = run['n']
    if run['kind'] == 'sample' and run['type'] == 'MM' and run['k'] < run['n']:
        prof['resume'](host)
        _reading(host, run)                                                # the same sample again (abm p.74)
        return
    if run['kind'] == 'sample' and run['type'] in ('TS', 'TTS') and run['k'] < run['n']:
        run['ti'] = run['k']
        prof['resume'](host)
        _cycle(host, run)
        return
    if (run['kind'] == 'sample' and run['type'] in ('MF', 'RM') or run['kind'] == 'adjust') and run['k'] < run['n']:
        run.update(stage='await_fill', prompt=prof['fill_prompt'](host, run))   # the operator exchanges the sample and confirms
        return
    ap['run'] = None
    if run['kind'] == 'sample':
        finish_measurement(host, run, run['readings'])
    elif run['kind'] == 'check':
        finish_check(host, run, run['readings'][-1])
    else:
        host._procedure_op = run.get('op', host.operation_id)             # the adjustment result belongs to the run (owner_guard)
        prof['adjust_done'](host, run)


def run_continue(host):
    ap, prof = host.ap, host.AP_PROFILE
    run = ap['run']
    need(run is not None and run['stage'] == 'await_fill', 'NO_PROCEDURE')
    if run.get('pre') and run['pre']['state'] == 'passed':
        run['pre'] = None                                                 # the sample is filled now: the measurement proper starts
    if run.get('pre'):
        prof['pre_check_cell'](host, run)
    else:
        prof['precheck'](host, run)
    run['prompt'] = None
    prof['resume'](host)
    _cycle(host, run)
    return dict(phase=host.phase)


def run_cancel(host):
    """<Stop> while a Multi Fill / adjustment waits for the next fill (the instrument is idle then)."""
    if host.ap['run'] is not None:
        host.ap['run'] = None
        return True
    return False


# ---------------------------------------------------------------- clock-driven device behaviour (called from the runtime's step)
def tick(host):
    ap = host.ap
    s = ap['settings']
    if ap['user'] and s['security.auto_logoff'] and host.time_s - ap['last_input_s'] >= s['security.auto_logoff_min'] * 60.0 - 1e-9:
        audit(host, f'User {ap["user"]} logged off automatically')
        ap['user'] = auto_logon_user(ap) if not s['security.increased'] else None
    for c in ap['checks']:
        due = check_due(host, c)
        if due is not None and due < 0 and c.get('last'):
            add_message(host, 'warning', f'Check "{c["name"]}" is overdue.')


def screen_off(host):
    """Screen saver: the display switches off after the set idle time (abm p.34); any touch or a measurement start wakes it."""
    ap = host.ap
    s = ap['settings']
    if not s['control.screen_saver'] or host.phase == 'running':
        return False
    return host.time_s - max(ap['last_input_s'], ap['boot_s']) >= s['control.screen_saver_min'] * 60.0 - 1e-9


# ---------------------------------------------------------------- front panel: host protocol of the engine and the panel.* commands
def panel_engine():
    try:
        from asset_runtime import _panel_ui as engine
        return engine
    except ImportError:                                                     # loaded from its file path
        import importlib.util
        import sys
        if '_chemsim_panel_ui' not in sys.modules:
            spec = importlib.util.spec_from_file_location('_chemsim_panel_ui', Path(__file__).with_name('_panel_ui.py'))
            sys.modules['_chemsim_panel_ui'] = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sys.modules['_chemsim_panel_ui'])
        return sys.modules['_chemsim_panel_ui']


def panel_caps(spec):
    """Panel capabilities in the runtimes' tuple form: (input, output, components, meaning, kind, visibility, example, effects)."""
    out = {}
    for k, d in panel_engine().capability_definitions(spec).items():
        out[k] = (d['input_schema'], d['output_schema'], d['component_ids'], d['function_zh'], d['kind'], d['visibility'], d['example_arguments'], [])
    return out


class PanelHost:
    """The engine's host protocol on top of a runtime: panel_read (agent slots plus host.* values), panel_invoke (the session's invoke path
    of the runtime, refusals as PanelRejected), panel_time.  Built per gesture; the UI state stays in runtime.panel_state (a plain dict)."""

    def __init__(self, rt):
        self.rt = rt

    def panel_read(self):
        return self.rt.panel_read()

    def panel_invoke(self, cap, args):
        engine = panel_engine()
        try:
            sess = getattr(self.rt, '_panel_session', None)
            if sess is not None:                                            # a gesture made through a session keeps that caller's access rules
                sess._guard(cap, args)
            return self.rt.invoke(cap, **args)
        except ActionRejected as exc:
            raise engine.PanelRejected(str(exc)) from None

    def panel_time(self):
        return self.rt.time_s


def owner_guard(host, principal, role, cap, args):
    """Caller rule of the sessions (direct call or panel gesture alike): an agent may not stop or continue (next fill) another agent's run, nor
    apply / reject the adjustment result of another agent's adjustment run.  The rear mains / power switch (power.set) is a hard switch: never
    refused, the instrument reacts as to a power loss (lead rule 2026-10-19)."""
    if role != 'agent':
        return

    def foreign(op):
        owner = host._owners.get(op) if op else None
        return owner is not None and owner != principal
    ops = [host.operation_id] if host.phase == 'running' else []           # a running operation, and a device run (also one waiting for a fill)
    if host.ap['run'] is not None:
        ops.append(host.ap['run'].get('op', host.operation_id))
    run = any(foreign(op) for op in ops)
    result = (host.ap['procedure'] or {}).get('kind') == 'adjust_result' and foreign(getattr(host, '_procedure_op', None))
    if (cap in ('acquisition.stop', 'measurement.continue') and run) or (cap == 'adjustment.decide' and result):
        raise ActionRejected('ACCESS_DENIED')


def panel(rt):
    engine = panel_engine()
    p = engine.PanelUI(rt.PANEL_SPEC, PanelHost(rt), hooks=getattr(rt, 'PANEL_HOOKS', None))   # e.g. the DMA camera picture (drawn when shown)
    if rt.panel_state is not None:
        p.ui = rt.panel_state
    return p


def panel_gesture(rt, kind, args):
    engine = panel_engine()
    schema = rt.AP_CAPS[f'panel.{kind}'][0]
    errs = engine.check_arguments(schema, args)
    need(not errs, 'INVALID_ARGUMENT')
    p = panel(rt)
    woke = kind == 'touch' and screen_off(rt)
    rt.ap['last_input_s'] = rt.time_s
    if woke:                                                                # abm p.34: a touch only switches the display on again
        rt.revision += 1
        return dict(accepted=True)
    try:
        if kind == 'press':
            ok = p.press(args['input_id'], float(args.get('duration_s', 0.1)), tuple(args.get('held', ())))
        elif kind == 'touch':
            to = (args['to_x'], args['to_y']) if 'to_x' in args and 'to_y' in args else None
            ok = p.touch(args['display_id'], args['x'], args['y'], float(args.get('duration_s', 0.1)), to)
        elif kind == 'turn':
            ok = p.turn(args['input_id'], args['detents'])
        else:
            ok = p.switch(args['input_id'], args['position'], tuple(args.get('held', ())))
    except engine.PanelError:
        rt.panel_state = p.ui
        raise ActionRejected('INVALID_ARGUMENT') from None
    rt.panel_state = p.ui
    if rt.ap['settings'].get('control.beep_touch') and ok:
        rt.ap['beeps'] += 1
    rt.revision += 1
    return dict(accepted=bool(ok))


def panel_capture(rt, args):
    engine = panel_engine()
    need(not engine.check_arguments(rt.AP_CAPS['panel.capture'][0], args), 'INVALID_ARGUMENT')
    p = panel(rt)
    data, w, h = p.png_bytes(args['display_id'])
    rt.panel_state = p.ui
    sha = hashlib.sha256(data).hexdigest()
    d = Path(rt.panel_capture_dir) if getattr(rt, 'panel_capture_dir', None) else Path(tempfile.gettempdir()) / 'chemistry_sim_panel' / rt.instance_id
    d.mkdir(parents=True, exist_ok=True)
    path = d / f'{args["display_id"]}_{sha[:16]}.png'
    if not path.exists():
        path.write_bytes(data)
    return dict(display_id=args['display_id'], time_s=rt.time_s, image=dict(path=str(path), format='png', width=w, height=h, sha256=sha))


def panel_frame(rt):
    p = panel(rt)
    f = p.frame()
    rt.panel_state = p.ui
    return dict(frame=f)


def panel_invoke(rt, cap, args):
    if cap == 'panel.capture':
        return panel_capture(rt, args)
    if cap == 'panel.frame':
        need(not args, 'INVALID_ARGUMENT')
        return panel_frame(rt)
    return panel_gesture(rt, cap.split('.', 1)[1], args)


# ---------------------------------------------------------------- slots and contract metadata of the device layer
def device_slots(profile):
    """State slots of the device layer: (schema, unit, origin, visibility, components, write route)."""
    disp = profile['display_component']
    lst = {'type': 'array', 'items': ANYOBJ}
    return {
        'device.settings': (settings_schema(profile['settings'], 0), '1', 'setpoint', 'agent', [disp], None),
        'device.datetime': ({'type': 'string', 'pattern': r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$'}, '1', 'derived', 'agent', [disp], None),
        'device.users': (ANYOBJ, '1', 'derived', 'agent', [disp], None),
        'device.messages': (lst, '1', 'derived', 'agent', [disp], None),
        'device.favorites': (lst, '1', 'setpoint', 'agent', [disp], None),
        'device.lists': (ANYOBJ, '1', 'setpoint', 'agent', [disp], None),
        'device.system': (ANYOBJ, '1', 'derived', 'agent', [disp], None),
        'device.touch_calibration': (nullable(ANYOBJ), '1', 'derived', 'agent', [disp], None),
        'methods.list': (lst, '1', 'setpoint', 'agent', [disp], None),
        'methods.active': (STR, '1', 'setpoint', 'agent', [disp], ('methods.select', 'method')),
        'sample.quick_settings': (ANYOBJ, '1', 'setpoint', 'agent', [disp], None),
        'sample.list': (ANYOBJ, '1', 'setpoint', 'agent', [disp], None),
        'data.measured': (lst, '1', 'derived', 'agent', [disp], None),
        'data.filters': (ANYOBJ, '1', 'setpoint', 'agent', [disp], None),
        'data.statistics': (lst, '1', 'derived', 'agent', [disp], None),
        'data.checks': (lst, '1', 'derived', 'agent', [disp], None),
        'data.adjustments': (lst, '1', 'derived', 'agent', [disp], None),
        'data.audit_trail': (lst, '1', 'derived', 'agent', [disp], None),
        'checks.list': (lst, '1', 'setpoint', 'agent', [disp], None),
        'standards.list': (lst, '1', 'setpoint', 'agent', [disp], None),
        'functions.list': (lst, '1', 'setpoint', 'agent', [disp], None),
        'functions.calculator': (nullable(ANYOBJ), '1', 'derived', 'agent', [disp], None),
        'run.state': (ANYOBJ, '1', 'derived', 'agent', [disp], None),
        'display.readout': (ANYOBJ, '1', 'derived', 'agent', [disp], None),
        'quantities.list': (lst, '1', 'derived', 'agent', [disp], None),
        'display.tables': (ANYOBJ, '1', 'derived', 'agent', [disp], None),
    }


SLOT_MEANING = {
    'device.settings': '仪器设置（控制面板、打印、样品列表选项、数据存储、系统安全）', 'device.datetime': '仪器时钟（ISO本地时间）',
    'device.users': '用户与用户组（无密码）、当前登录用户与权限、增强安全锁定', 'device.messages': '消息列表（警告/错误）与状态图标颜色',
    'device.favorites': '收藏夹列表', 'device.lists': '打印机、报表与数据浏览列配置', 'device.system': '系统信息、重启计数、固件检查、蜂鸣与自动打印计数',
    'device.touch_calibration': '最近一次触摸屏校准', 'methods.list': '方法列表与各方法设置', 'methods.active': '当前方法',
    'sample.quick_settings': '下一次测量的快速设置（样品名、类型、参数）', 'sample.list': '样品列表与10个模板', 'data.measured': '数据存储中的测量结果',
    'data.filters': '数据存储筛选条件', 'data.statistics': '按方法的统计（筛选后的测量结果）', 'data.checks': '核查数据', 'data.adjustments': '调整数据',
    'data.audit_trail': '审计追踪', 'checks.list': '核查定义（含上次结果与到期）', 'standards.list': '参考标准', 'functions.list': '用户函数',
    'functions.calculator': '计算器上次结果', 'run.state': '测量流程状态（阶段、进度、条件、MF等待装样、超时）',
    'display.readout': '主屏显示：标题栏、输出字段文本、进度条、快捷区状态（实时/冻结、数值可见性）',
    'quantities.list': '可选输出量（组、单位；含用户函数）',
    'display.tables': '数据浏览、删除视图、详情、统计、核查/调整/审计列表的显示文本（仪器格式化后的单元格）'}


def quantities_list(profile, ap):
    out = [dict(id=q, label=d['label'], group=d['group'], units=list(d.get('units') or ['']), kind=d.get('kind', 'number'))
           for q, d in profile['quantities'].items()]
    out += [dict(id='fn:' + f['name'], label=f['name'], group='User Functions', units=[f.get('unit') or ''], kind='number') for f in ap['functions']]
    return out + profile.get('extra_quantities', lambda a: [])(ap)


def public_users(ap):
    me = user_record(ap, ap['user']) if ap['user'] else None
    return dict(current=ap['user'], group=me['group'] if me else None, rights=rights_of(ap),
                users=[{k: u[k] for k in ('name', 'group', 'active', 'auto_logon', 'role', 'sign_name', 'email', 'email_active')} | dict(has_password=bool(u['password']))
                       for u in ap['users']],
                groups=copy.deepcopy(ap['groups']))


def _cell(prof, ap, q, rec):
    d = prof['quantities'].get(q, {})
    v = rec['values'].get(q)
    if d.get('kind') == 'text' or q in ('sample_name', 'method', 'user', 'date', 'time', 'unique_id', 'error_state'):
        v = {'sample_name': rec['sample_name'], 'method': rec['method'], 'user': rec['user'], 'unique_id': rec.get('uid', ''),
             'error_state': rec['error_state'], 'date': rec['time'][:10], 'time': rec['time'][11:19]}.get(q, v)
        return '' if v is None else str(v)
    f = dict(q=q, unit=d.get('unit'), digits=d.get('digits', 4))
    m = method(ap, rec['method'])
    for x in (m['output'] if m else []):
        if x['q'] == q:
            f = dict(x)
    return fmt_value(prof, f, v, ap) + (f' {f["unit"]}' if f.get('unit') and v is not None and d.get('kind') != 'text' else '')


def _rows(host, recs, cols):
    ap, prof = host.ap, host.AP_PROFILE
    out = []
    for i, r in enumerate(recs):
        m = method(ap, r['method'])
        qs = [x['q'] for x in (m['output'] if m and m['output'] else [])] or list(ap['browser'])
        det = [['Sample Name', r['sample_name']], ['Method', r['method']], ['Date / Time', r['time'].replace('T', ' ')], ['User', r['user']],
               ['Measurement Type', MEASUREMENT_TYPES.get(r['type'], r['type'])], ['Sample Error State', r['error_state']], ['Unique Sample ID', r['uid']]]
        det += [[quantity_label(prof, ap, q), _cell(prof, ap, q, r)] for q in qs if q not in ('sample_name', 'method', 'user', 'error_state', 'unique_id')]
        if r.get('limits_failed'):
            det.append(['Limits', 'violated: ' + ', '.join(quantity_label(prof, ap, q) for q in r['limits_failed'])])
        sig = r.get('signatures') or {}
        for role in ('submitter', 'reviewer', 'approver'):
            x = sig.get(role)
            if ap['settings'].get('security.esign') or x:
                det.append([f'Signature ({role})', '-' if x is None else ('positive' if x['positive'] else 'negative') + f' ({x["user"]})'])
        out.append(dict(id=r['id'], cells=[_cell(prof, ap, q, r) for q in cols], details=det, prev=recs[i - 1]['id'] if i else None,
                        next=recs[i + 1]['id'] if i + 1 < len(recs) else None, error_state=r['error_state'], kind=r['kind']))
    return out


def _f6(v, sign=''):
    return '-' if v is None else format(v, f'{sign}.6f')


def tables(host):
    """Display texts of the data memory views (abm p.88-93): the data browser columns, single sample details, statistics, check / adjustment
    data and the audit trail."""
    ap, prof = host.ap, host.AP_PROFILE
    cols = list(ap['browser'])
    head = [quantity_label(prof, ap, q) for q in cols]
    view = filtered(host, 'view')
    dele = filtered(host, 'delete')
    stats = statistics(host)
    for srow in stats:
        srow['cells'] = [srow['method'], str(srow['n'])]
        srow['details'] = [[q['label'], str(q['n']), f'{q["min"]:.6g}', f'{q["max"]:.6g}', f'{q["avg"]:.6g}', f'{q["sd"]:.3g}'] for q in srow['quantities']]
    checks = [dict(id=c['id'], cells=[c['time'].replace('T', ' ')[:16], c['check'], c['result']],
                   details=[['Check', c['check']], ['Type', c['kind']], ['Date / Time', c['time'].replace('T', ' ')], ['Method', c['method'] or '-'],
                            ['Result', c['result']], ['User', c['user']], ['Reference value', '-' if c['reference'] is None else f'{c["reference"]:.5f}'],
                            ['Measured value', '-' if c['measured'] is None else f'{c["measured"]:.5f}'],
                            ['Lower / upper limit', '-' if c['lower'] is None else f'{c["lower"]:.5f} / {c["upper"]:.5f}'], ['Condition', c['condition']],
                            ['Temperature', '-' if c['temperature'] is None else f'{c["temperature"]:.2f} °C']] +
                           [[x['q'], f'{x["value"]} ({x["lower"]} .. {x["upper"]})'] for x in c.get('rows', [])])
              for c in ap['check_data']]
    adj = [dict(id=a['id'], cells=[a['time'].replace('T', ' ')[:16], a['type'], a['user']],
                details=[['Type', a['type']], ['Date / Time', a['time'].replace('T', ' ')], ['User', a['user']]] +
                        [[f'{x["standard"]}', f'old {_f6(x["old"])} / new {_f6(x["new"])} ({_f6(x["dev_abs"], "+")})'] for x in a['rows']])
           for a in ap['adjustments']]
    aud = [dict(id=e['id'], cells=[e['time'].replace('T', ' ')[:16], e['user'], e['text']],
                details=[['Date / Time', e['time'].replace('T', ' ')], ['User', e['user']], ['Entry', e['text']], ['Exported', 'yes' if e['exported'] else 'no']])
           for e in ap['audit']]
    return dict(header=head, measured=_rows(host, view, cols), measured_filtered=bool(ap['filters']['view']), delete=_rows(host, dele, cols),
                delete_filtered=bool(ap['filters']['delete']), statistics=stats, checks=checks, adjustments=adj, audit=aud,
                filters={k: [dict(c, cells=[c['criterion'], c['comparator'], c.get('value', '')]) for c in v] for k, v in ap['filters'].items()})


def methods_view(ap):
    """The methods with, per method, the constants of the user functions (active flag and value set in the method, abm p.79, gsf p.35)."""
    out = copy.deepcopy(ap['methods'])
    consts = [f for f in ap['functions'] if f['type'] == 'Constant']
    for m in out:
        m['constants_view'] = [dict(name=f['name'], active=bool(m['modules'].get(f['name'])), value=m['constants'].get(f['name'], f.get('value', 0.0)))
                               for f in consts]
    return out


def device_values(host):
    """The agent-visible slots of the device layer."""
    ap, prof = host.ap, host.AP_PROFILE
    due = {c['name']: check_due(host, c) for c in ap['checks']}
    return {
        'device.settings': copy.deepcopy(ap['settings']), 'device.datetime': device_iso(host), 'device.users': public_users(ap),
        'device.messages': copy.deepcopy(ap['messages']), 'device.favorites': copy.deepcopy(ap['favorites']),
        'device.lists': dict(printers=copy.deepcopy(ap['printers']), reports=copy.deepcopy(ap['reports']), browser=list(ap['browser']),
                             extrapolation=copy.deepcopy(ap['extrapolation']),
                             shares=[{k: x[k] for k in ('name', 'path', 'user')} for x in ap['shares']]),
        'device.system': dict(rows=prof['system_info'](host), power_epoch=ap['power_epoch'], firmware_checked=ap['firmware_checked'], beeps=ap['beeps'],
                              auto_printouts=ap['auto_printouts'], status=status(ap)),
        'device.touch_calibration': copy.deepcopy(ap['touch_cal']), 'methods.list': methods_view(ap), 'methods.active': ap['active'],
        'sample.quick_settings': copy.deepcopy(ap['quick']),
        'sample.list': dict(entries=copy.deepcopy(ap['sample_list']), templates=[dict(t, index=i) for i, t in enumerate(copy.deepcopy(ap['templates']))]),
        'data.measured': copy.deepcopy(ap['data']), 'data.filters': copy.deepcopy(ap['filters']), 'data.statistics': statistics(host),
        'data.checks': copy.deepcopy(ap['check_data']), 'data.adjustments': copy.deepcopy(ap['adjustments']), 'data.audit_trail': copy.deepcopy(ap['audit']),
        'checks.list': [dict(c, due_days=None if due[c['name']] is None else round(due[c['name']], 3)) for c in ap['checks']],
        'standards.list': copy.deepcopy(ap['standards']), 'functions.list': copy.deepcopy(ap['functions']),
        'functions.calculator': copy.deepcopy(ap.get('calculator')), 'run.state': prof['run_state'](host), 'display.readout': prof['readout'](host),
        'quantities.list': quantities_list(prof, ap), 'display.tables': tables(host)}


def device_cap_tuples(profile, components):
    """Device commands in the runtimes' tuple form (input, output, components, meaning, kind, visibility, example, effects)."""
    out = {}
    for k, (inp, outp, meaning, kind, right, example, effects) in DEVICE_CAPS.items():
        if k not in profile['commands']:
            continue
        inp = copy.deepcopy(inp)
        if k == 'settings.configure':
            inp = obj(dict(settings=settings_schema(profile['settings'])))
        if k == 'methods.configure':
            inp = obj(dict(method=NAME, settings=method_keys(profile)))
        if k == 'quick.configure':
            inp = obj(dict(settings=quick_schema(profile)))
        if k == 'sample_list.edit':                                         # measurement types of this instrument
            inp['properties']['entry']['properties']['type']['enum'] = [x for x in MEASUREMENT_TYPES if x in profile['modes']]
        if k == 'lists.edit' and 'extrapolation_default' not in profile:   # the extrapolation temperatures exist on the DMA M only (gsf p.17)
            inp['properties']['list']['enum'].remove('extrapolation')
        if k == 'checks.manage' and 'check_schema' in profile:
            inp = obj(dict(action={'type': 'string', 'enum': ['save', 'delete']}, check=ANYOBJ, original=nullable(STR)), optional=('original',))
        out[k] = (inp, outp, components.get(k, [profile['display_component']]), meaning, kind, 'agent', example, effects)
    return out


def precondition(cap):
    spec = DEVICE_CAPS.get(cap)
    right = spec[4] if spec else None
    text = '仪器通电' + ('' if cap in NO_LOGON else '、已登录') + \
        (f'、登录用户的组有权限{right}' if right else '') + \
        ('' if cap in ('monitor.set', 'favorites.configure', 'data.filter', 'calculator.evaluate', 'firmware.check', 'user.logon', 'user.logoff',
                       'messages.acknowledge') else '、没有测量在进行（测量中不能保存设置）')
    check = '_antonpaar_touch.gate: powered, logged on, group right, not running; then the command validates on a candidate copy'
    if cap in ('measurement.continue', 'adjustment.decide'):                 # caller rule of the sessions (direct call and panel gesture alike)
        text += '；调用者规则：agent不能确认他人运行的下一次装样，也不能应用/放弃他人调整运行的结果（ACCESS_DENIED，状态不变）'
        check += '; session caller rule _antonpaar_touch.owner_guard (direct call and the command a panel gesture calls)'
    return dict(description_zh=text, state_ids=['power.enabled', 'device.users', 'cycle.phase'], enforced_by='runtime', check=check)
