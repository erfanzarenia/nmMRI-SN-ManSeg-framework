# QC & Inter-Rater Analysis

Two scripts, run centrally (not by raters), per phase. Both read paths from
[`config/paths.yaml`](../config/paths.yaml) and assume a single SN label
(value 1; override with `--label`). Results are shared back with raters only when
appropriate.

## 1. Metrics

```bash
python scripts/qc_metrics.py --phase phase4
```

Loads each rater's mask (and the expert mask, in the expert-guided phases),
matching them by the anonymized workspace ID, and writes to
`<qc_output_root>/<phase>/`:

| File                       | Contents                                          |
|----------------------------|---------------------------------------------------|
| `dice_pairwise.csv`        | rater-vs-rater Dice per subject (inter-rater)     |
| `dice_rater_vs_expert.csv` | rater-vs-expert Dice per subject (phases 1–2)     |
| `volumes.csv`              | SN volume (mm³) per source per subject            |

Rater masks follow the convention in [`scripts/utils.py`](../scripts/utils.py):
`<rater>_mask.nii.gz`, or the latest `<rater>_mask_a*.nii.gz` for multi-attempt
phases. Useful flags: `--subjects sub-0010 sub-0012`, `--label 1`,
`--workspace <path>`, `--out-dir <path>`.

## 2. Summaries

```bash
python scripts/interrater_analysis.py --phase phase4
```

Reads the metrics CSVs and writes clean summaries to the same directory, and
prints them:

| File                     | Contents                                       |
|--------------------------|------------------------------------------------|
| `summary_overall.csv`    | mean/sd Dice for the phase                      |
| `summary_by_pair.csv`    | mean/sd pairwise Dice per rater pair            |
| `summary_by_subject.csv` | mean pairwise Dice per subject (worst first)    |
| `summary_by_rater.csv`   | mean Dice vs expert per rater (phases 1–2)      |

The worst-subject ordering (`--top-n`) flags cases to review first.

## Which phase to QC

- **Phase 2 (Practice)** — rater-vs-expert calibration (shared, has expert
  masks).
- **Phase 3 (Mock exam)** — pairwise inter-rater on the blind rehearsal set; a
  sanity check before the real Exam.
- **Phase 4 (Exam)** — inter-rater reliability (shared, blind). The primary QC
  target.
- **Phase 5 (Production)** — partitioned, so pairwise Dice is computed only on
  the hidden overlap subset, giving an ongoing reliability check on the
  production data. `volumes.csv` covers the rest as a distribution sanity check.
