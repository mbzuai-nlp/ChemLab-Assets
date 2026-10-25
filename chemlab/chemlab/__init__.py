"""chemlab: an LLM-facing, bash-like command interface for lab instruments (pilot: Opentrons Flex on the fast simulation).

Layers: backend (lab protocol 1.0.0 + time) -> core service / gate (verbs, units, checks, errors, jobs, lock, log) ->
unix-socket server -> thin `chemlab` client inside a Landlock + seccomp sandbox.
"""
__version__ = '0.1.0'
