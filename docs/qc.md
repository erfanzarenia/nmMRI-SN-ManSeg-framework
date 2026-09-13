# QC & Inter-Rater Analysis

Scripts are run centrally (not by raters), per phase, and read paths from
[`config/paths.yaml`](../config/paths.yaml). Masks hold SN = 1 and, from Phase
3, CP = 2, the cerebral-peduncle reference region
([segmentation_protocol.md](segmentation_protocol.md)). Sections 1–3 measure one
label at a time, SN by default (`--label`); section 4 checks the CP rules and
reference intensity. Results are shared back with raters only when appropriate.

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

For a full QC pass, use the `run_qc.py` driver: it adds **Hausdorff** distances
(max, HD95, ASSD) alongside Dice, the complete reporting hierarchy, publication
figures, worst-slice montages and Markdown reports. It reuses the same
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
assumes a fixed number of attempts: each scan is averaged over only the attempts
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
metric tables and agreement figures but no slice montages, since there is no
single mask to draw):

| Area            | Script            | Output                                                        |
|-----------------|-------------------|---------------------------------------------------------------|
| Metrics         | `qc_analysis.py`  | per-scan + per-rater + per-pair + per-scan + grand-average CSVs, volumes, and an **inventory** flagging missing/odd mask files |
| Figures         | `qc_visualize.py` | Dice & Hausdorff per-scan (each rater + mean), by-rater / by-pair bars |
| Worst/best slices | `qc_visualize.py` | the 3 worst (top row) and 3 best (bottom row) axial slices per rater-scan by per-slice Dice vs expert, rater + expert contours on the NM image (`worst_slices/`) |
| All slices      | `qc_visualize.py` | every SN-bearing axial slice per rater-scan in one figure, rater + expert contours overlaid once per panel (`all_slices/`) |
| Reproducibility | `qc_analysis.py`  | intra-rater agreement between a rater's own attempts (`attempts_average/metrics/attempts_reproducibility.csv`) |
| Reports         | `qc_report.py`    | `<phase>/<attempt>/reports/<phase>_<attempt>_qc_report.md` and `summary/overall_qc_report.md` |

The attempts-average is the per-scan/rater **mean of the per-attempt metrics**
(Dice, Hausdorff, volumes, …), mirroring every other table; it is not a
combined mask. Companion `attempts_reproducibility.csv` records each rater's own
attempt-to-attempt agreement (a_i vs a_j). The original `qc_metrics.py`
(latest-attempt Dice only) still runs into each phase's `legacy_scripts/` as a
cross-check. Each report opens with a Methods note; start at
`summary/overall_qc_report.md`.

## 4. Protocol & reference region (CP)

CP is a reference region: what matters is that it follows the rules in
[segmentation_protocol.md](segmentation_protocol.md) and gives a stable
reference intensity, more than its overlap between raters.

```bash
python scripts/qc_protocol.py --phase phase4 \
    --workspace ~/path/to/ManSeg_Workspace \
    --qc-root   ~/path/to/ManSeg_Workspace/qc
```

Writes to `<qc-root>/<phase>/a<N>/protocol/` (attempt 1 unless `--attempt N`;
`--latest` takes each rater's highest attempt per scan and writes to
`<phase>/latest/protocol/`):

| File                     | Contents |
|--------------------------|----------|
| `protocol_per_mask.csv`  | per rater × scan: labels present, SN/CP volume, hemispheres covered, CP slice coverage vs SN, CP voxels touching SN, mean SN and CP intensity, contrast `(SN − CP) / CP`, and rule flags |
| `agreement_per_scan.csv` | per rater × scan with ≥ 2 raters: mean SN Dice, SN HD95 and CP Dice vs the other raters; SN volume ÷ scan median (`sn_volume_ratio`); CP mean intensity vs scan median (`cp_mean_dev`); contrast minus scan median (`contrast_diff`) |
| `verdict.csv`, `verdict.md` | only for a phase with `criteria` (Phase 5): pass/review per rater, see below |

**CP criteria.** Every mask is expected to raise no flags:

- only the phase's labels are present;
- SN and CP each cover both hemispheres;
- CP is on every axial slice that has SN, and on no other slice;
- no CP voxel touches SN within a slice (at least a one-voxel gap).

Where several raters segment a scan (shared phases, production overlap), each
rater's mean CP intensity is expected within ±5% of the scan median
(`|cp_mean_dev| ≤ 0.05`); larger deviations usually mean CSF, vessels or SN
voxels inside CP. CP Dice is informative only: CP is a reference region, not a
boundary to reproduce.

### Calibration verdict (Phase 5)

The SNIT calibration has a `criteria` block in `phases.yaml`, so its run also
writes `verdict.csv` and `verdict.md`: one line per rater, `pass` only when all
four criteria hold.

| Criterion      | Rule (value in `phases.yaml`) |
|----------------|-------------------------------|
| `protocol`     | a mask for every scan, none flagged |
| `sn_agreement` | median over scans of `sn_dice` ≥ `sn_dice_min` (0.60) and of `sn_hd95` ≤ `sn_hd95_max_mm` (3.0) |
| `sn_volume`    | abs(`sn_volume_ratio` − 1) ≤ `sn_volume_tol` (0.30) on ≥ `min_scans` (4) scans |
| `cp_reference` | abs(`cp_mean_dev`) ≤ `cp_mean_tol` (0.05) on ≥ `min_scans` scans |

The thresholds sit near the edge of the agreement raters already reach (pooled
Phase 1–2 QC, July 2026: inter-rater SN Dice 0.67 ± 0.06, HD95 2.1 ± 0.9 mm),
so they catch real shifts rather than normal variation; the volume rule targets
the systematic over- or under-segmentation a contrast change tends to cause.
`review_scans` lists the scans behind each missed criterion. The rater
re-segments only those, as `<INIT>_mask_a2.nii.gz`, and the re-check runs on
each rater's latest mask:

```bash
python scripts/qc_protocol.py --phase phase5 --latest
```

Calibration scans also keep their production assignment, under other workspace
IDs (the split never moves existing assignments); the production masks are the
ones used for analysis.

## Which phase to QC

- **Phase 2 (Practice)**: rater-vs-expert calibration (shared, has expert
  masks).
- **Phase 3 (Mock exam)**: pairwise inter-rater on the blind rehearsal set; a
  sanity check before the real Exam. It is the first phase with CP, so the
  section 4 flags are fed back before the Exam.
- **Phase 4 (Exam)**: inter-rater reliability (shared, blind). The primary QC
  target, for SN (sections 1–3) and CP (section 4).
- **Phase 5 (SNIT calibration)**: shared, blind, on SNIT combined-echo images;
  the section 4 verdict gates production. `qc_metrics.py --phase phase5` adds
  pairwise SN detail when a verdict needs it.
- **Phase 6 (Production)**: partitioned, so pairwise Dice is computed only on
  the hidden overlap subset, giving an ongoing reliability check on the
  production data. `volumes.csv` covers the rest as a distribution sanity check,
  and section 4 checks the CP rules on every mask.
