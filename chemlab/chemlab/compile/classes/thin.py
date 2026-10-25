"""The thin class: one-of-a-kind or manual-workflow instruments that get only the shared verbs (power, status, open / close of
named parts, ack, estop, method / start / stop / result / wait) and keep everything else on `chemlab call`.

Members (configs/adapters/thin.json): wtw_oxitopi (BOD heads: --head h1..h6), inert_puresolvmd (manual valves: cabinet doors
and the gas isolation valve only), vaisala_dmt152 (a transmitter: power with its supply voltage, result = one SEND reading),
cem_libertyblue and cytiva_ktaoligosynt (power, cover / clamp, start / stop / result of the synthesis run).  Their mappings
are reviewed by hand; the generated shared layer leaves them out.
"""
from ..shared import SharedAdapter


class ThinAdapter(SharedAdapter):
    CLASS = 'thin'
    TITLE = 'shared verbs only (one-of-a-kind device)'
    ABOUT = ('This instrument has no class of its own: these are the shared verbs (power, status, open/close, start/stop/result/wait '
             'where it has them); everything else is its own capabilities: `chemlab describe INST`, `chemlab call INST CAP`.')
