"""The compile layer: class-level verbs for instrument classes, lowered to each instrument's own contract capabilities.

    chemlab run ohaus_px224e tare                 -> weighing.tare
    chemlab run mt_xpr204e tare                   -> balance.tare
    chemlab run julabo_corio_cd200f set-temp 80C  -> thermal.set_temperature {temperature_K: 353.15}

Framework: units.py (units <-> SI), errors.py (error format), verbs.py (verb tables, argument parsing), ctx.py (one request:
native calls, dry-run plan, waiting, steps done), jobs.py (job-N), base.py (ClassAdapter), fragments.py (configs/adapters/*.json,
merged at lab load), mapgen.py (draft mappings from the contracts).  Classes: classes/<class>.py.  Host-side scenes: scenes/.
Acceptance tasks: tasks/.  How to add a class: chemlab/COMPILE_GUIDE.md.
"""
