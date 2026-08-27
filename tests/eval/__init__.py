"""``P-38``'s evaluation set and the harness that measures against it.

Kept beside the tests rather than under ``docs/`` because it is executable
evidence, not prose: every number in docs/rag-fidelity-audit.md §4-و came out
of ``run_calibration.py`` reading ``hr_handbook_set.py``, and re-deriving them
means re-running exactly that. Neither module is named ``test_*``, so pytest
collects nothing here -- the harness needs a running stack and an indexed
corpus, which is a runbook step and not a test's to arrange. What IS collected
is ``tests/unit/test_evaluation_set.py``, which pins the set's shape so it
cannot quietly decay into questions with no reference answer.
"""
