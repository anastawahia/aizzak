# Synthetic WireTwin fixtures (INC-1, BT28, BT39)

Not drawings. `inspections/<sha256>.json` are hand-built ele-parse `inspect` outputs (schema 1).
Each file name is the SHA-256 of a label string (`synthetic-<name>`), and `labels.json` is the
matching label file for `tests/eval/electrical/m32_profile_detection.py`. The labels are WRONG ON
PURPOSE for two documents so that M-32 is below 1:

| document | proposal | label | right on |
|---|---|---|---|
| iec-eplan-en | iec_60617 / iec_81346 | same | both |
| iec-plain-en | iec_60617 / iec_81346 | gbt_4728 / iec_81346 | convention only |
| cn-wps-zh | gbt_4728 / cn_letter_codes | same | both |
| cn-autocad-zh | gbt_4728 / cn_letter_codes | gbt_4728 / gbt_5094 | symbol only |
| tiny-sample | iec_60617 / iec_81346 (confidence 0) | same | both |

Both fields right: 3/5 = 0.6; convention 4/5; symbol 4/5. The owner's real samples never enter
the repository (LLD section 1.4).
