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

## 3. Comprehensive QC (metrics + figures + reports)

For a full QC pass — adding **Hausdorff** distances (max, HD95, ASSD) alongside
Dice, the complete reporting hierarchy, publication figures, worst-slice
montages and Markdown reports — use the `run_qc.py` driver. It reuses the same
`utils.py` and reads the same workspace; expert-guided phases (1–2) also get
rater-vs-expert outputs.

```bash
python scripts/run_qc.py \
    --workspace ~/path/to/ManSeg_Workspace \
    --qc-root   ~/path/to/ManSeg_Workspace/qc \
    --phases phase1 phase2
```

**Attempts.** Some phases collect more than one attempt per scan
(`<INIT>_mask_a1`, `_a2`, …). `run_qc.py` detects the attempts present in each
phase from the file names (it does not trust the configured count) and runs the
**whole analysis independently for each attempt**, plus an **attempts-average**
that is the **mean of the per-attempt metrics** for each scan/rater. Nothing
assumes a fixed number of attempts — each scan is averaged over only the attempts
it has (`n_attempts` records how many), and a single-attempt phase gets just
`a1/`. Each phase's QC directory is therefore:

```
qc/<phase>/
  a1/  { metrics, plots, worst_slices, all_slices, reports }
  a2/  { … }                       # only if a2 exists (adapts to the data)
  attempts_average/ { metrics, plots, reports } + metrics/attempts_reproducibility.csv
  legacy_scripts/                  # original qc_metrics.py / interrater_analysis.py
qc/summary/                        # pooled across phases (attempts-average where available)
```

Each per-attempt folder mirrors the same outputs (the average folder has the
metric tables and agreement figures but no slice montages — there is no single
mask to draw):

| Area            | Script            | Output                                                        |
|-----------------|-------------------|---------------------------------------------------------------|
| Metrics         | `qc_analysis.py`  | per-scan + per-rater + per-pair + per-scan + grand-average CSVs, volumes, and an **inventory** flagging missing/odd mask files |
| Figures         | `qc_visualize.py` | Dice & Hausdorff per-scan (each rater + mean), by-rater / by-pair bars |
| Worst/best slices | `qc_visualize.py` | the 3 worst (top row) and 3 best (bottom row) axial slices per rater-scan by per-slice Dice vs expert, rater + expert contours on the NM image (`worst_slices/`) |
| All slices      | `qc_visualize.py` | every SN-bearing axial slice per rater-scan in one figure, rater + expert contours overlaid once per panel (`all_slices/`) |
| Reproducibility | `qc_analysis.py`  | intra-rater agreement between a rater's own attempts (`attempts_average/metrics/attempts_reproducibility.csv`) |
| Reports         | `qc_report.py`    | `<phase>/<attempt>/reports/<phase>_<attempt>_qc_report.md` and `summary/overall_qc_report.md` |

The attempts-average is the per-scan/rater **mean of the per-attempt metrics**
(Dice, Hausdorff, volumes, …), mirroring every other table — not a combined
mask. Companion `attempts_reproducibility.csv` records each rater's own
attempt-to-attempt agreement (a_i vs a_j). The original `qc_metrics.py`
(latest-attempt Dice only) still runs into each phase's `legacy_scripts/` as a
cross-check. Each report opens with a Methods note; start at
`summary/overall_qc_report.md`.

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
