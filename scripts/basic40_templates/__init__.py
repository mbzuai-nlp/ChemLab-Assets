"""Template-backed (real public data) measurement helpers for the basic40_batch02 runtimes.

One module per instrument slug; each module reads libraries under assets/reactions/ and is imported lazily
by the matching scripts/basic40_runtime_<tier>.py hook.
"""
