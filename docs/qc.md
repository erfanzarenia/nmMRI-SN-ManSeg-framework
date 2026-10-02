# QC & Inter-Rater Analysis

QC runs centrally (not by raters) with one command. Masks hold SN = 1 and, from
Phase 3, CP = 2, the cerebral-peduncle reference region
([segmentation_protocol.md](segmentation_protocol.md)). Results are shared back
with raters only when appropriate.

## Run

```bash
python scripts/run_qc.py phase2            # one phase
python scripts/run_qc.py phase1 phase2     # several phases, plus a pooled summary
python scripts/run_qc.py                   # every phase that has rater outputs
```

Paths come from [`config/paths.yaml`](../config/paths.yaml); `--workspace` and
`--qc-root` override them, and `--label 2` measures CP instead of SN. Every run
starts clean: whatever the QC folder held is first moved to
`qc/archive/<timestamp>/`, never deleted, so `qc/` always holds exactly one run.

```
qc/
  README.md                    # what this run covers and where to start
  <phase>/
    coverage.md, coverage.csv  # who has which attempt for which scan, plus notes
    a1/  { metrics, plots, worst_slices, all_slices, reports[, protocol] }
    a2/  { … }                 # one folder per attempt found
    attempts_average/ { metrics, plots, reports } + metrics/attempts_reproducibility.csv
  summary/                     # pooled across phases, only when 2+ phases are run
  archive/<timestamp>/         # earlier runs
```

Start with `<phase>/coverage.md`, then the reports in
`<phase>/<attempt>/reports/`; each opens with a Methods note.

## Attempts

Attempts (`a1`, `a2`, …) are detected from the file names, not from the
configured count, and each is analyzed on its own. A rater or scan without an
attempt simply does not contribute to it, so a phase can be QC'd while some
raters are still on a1 and others have finished a2, and a later round (a new
a2, or an a3) is picked up the next time QC runs. With more than one attempt,
`attempts_average/` holds the mean of the per-attempt metrics for each
scan/rater (`n_attempts` records how many were averaged; it is not a combined
mask), and `attempts_reproducibility.csv` each rater's agreement between their
own attempts (a_i vs a_j).

## Coverage and flags

QC never stops for missing or odd files: it computes what it can and notes the
rest. `coverage.md` shows, per rater and scan, the attempts found (`·` for none,
`*` for a scan with a note), a per-rater count against the attempts the phase
expects, and the notes themselves. The same per-rater counts are printed while
QC runs, and each report repeats the gaps for its attempt in one line.

How masks are found in `outputs/<sub>/`:

- **Standard:** `<INIT>_mask_a<N>.nii.gz`, and `<INIT>_mask.nii.gz` as a1 in
  single-attempt phases.
- **Used, with a note:** other names whose meaning is clear: the attempt token
  in any case (`_A1`), `ai` as the a1 typo, and a lone mask with no attempt
  token (read as a1).
- **Ignored, with a note:** copies of the inputs (`image.nii.gz`), extra files
  next to a recognized mask, and files saved outside the subject folders.
- **Set aside, with a note:** a mask that is not on the scan's image grid (the
  note names the scan whose grid it matches, which usually means it was saved
  into the wrong subject folder), and a mask with no voxels of the measured
  label.

## Metrics and figures

Dice and Jaccard, plus Hausdorff distances (max, HD95, ASSD) in mm, rater vs
expert (phases with expert masks) and rater vs rater. Surface distances are
measured between the voxel faces on each mask's boundary, weighted by face
area, not between voxel centres, so they are not pinned to the 1 mm slice
thickness and can fall below 1 mm.

| Area            | Script            | Output                                                        |
|-----------------|-------------------|---------------------------------------------------------------|
| Metrics         | `qc_analysis.py`  | per-scan + per-rater + per-pair + per-scan + grand-average CSVs, volumes, and an **inventory** of the mask used per rater/scan with its notes |
| Figures         | `qc_visualize.py` | Dice & Hausdorff per-scan (each rater + mean), by-rater / by-pair bars |
| Worst/best slices | `qc_visualize.py` | the 3 worst (top row) and 3 best (bottom row) axial slices per rater-scan by per-slice Dice vs expert, rater + expert contours on the NM image (`worst_slices/`) |
| All slices      | `qc_visualize.py` | every SN-bearing axial slice per rater-scan in one figure, rater + expert contours overlaid once per panel (`all_slices/`) |
| Reproducibility | `qc_analysis.py`  | intra-rater agreement between a rater's own attempts (`attempts_average/metrics/attempts_reproducibility.csv`) |
| Reports         | `qc_report.py`    | `<phase>/<attempt>/reports/<phase>_<attempt>_qc_report.md`, and `summary/overall_qc_report.md` when several phases are run |

## Protocol & reference region (CP)

CP is a reference region: what matters is that it follows the rules in
[segmentation_protocol.md](segmentation_protocol.md) and gives a stable
reference intensity, more than its overlap between raters. For phases that
segment CP, `run_qc.py` runs these checks for every attempt into
`<phase>/a<N>/protocol/`; on its own:

```bash
python scripts/qc_protocol.py --phase phase4
```

(attempt 1 unless `--attempt N`; `--latest` takes each rater's highest attempt
per scan and writes to `<phase>/latest/protocol/`):

| File                     | Contents |
|--------------------------|----------|
| `protocol_per_mask.csv`  | per rater × scan: labels present, SN/CP volume, hemispheres covered, CP slice coverage vs SN, CP voxels touching SN, mean SN and CP intensity, contrast `(SN − CP) / CP`, and rule flags |
| `agreement_per_scan.csv` | per rater × scan with ≥ 2 raters: mean SN Dice, SN HD95 and CP Dice vs the other raters; SN volume ÷ scan median (`sn_volume_ratio`); CP mean intensity vs scan median (`cp_mean_dev`); contrast minus scan median (`contrast_diff`) |
| `verdict.csv`, `verdict.md` | only for a phase with `criteria` (Phase 5): pass/review per rater who has started, see below |

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

The SNIT calibration has a `criteria` block in `phases.yaml`, so its checks also
write `verdict.csv` and `verdict.md`: one line per rater with masks, `pass` only
when all four criteria hold.

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
IDs; sub-1093 and sub-5064 are production overlap subjects as well, so every
rater segments them again in Phase 6. The production masks are the ones used for
analysis.

## Quick checks

For a fast Dice-only look at one phase (latest attempt per rater, found the same
way as above), without figures or reports:

```bash
python scripts/qc_metrics.py --phase phase4           # dice_pairwise, dice_rater_vs_expert, volumes
python scripts/interrater_analysis.py --phase phase4  # summary_*.csv, worst subjects first
```

Both write to `<qc_output_root>/<phase>/`, i.e. into the current run's folder.

## Which phase to QC

- **Phase 2 (Practice)**: rater-vs-expert calibration (shared, has expert
  masks).
- **Phase 3 (Mock exam)**: pairwise inter-rater on the blind rehearsal set; a
  sanity check before the real Exam. It is the first phase with CP, so the
  protocol flags are fed back before the Exam.
- **Phase 4 (Exam)**: inter-rater reliability (shared, blind). The primary QC
  target, for SN (metrics) and CP (protocol checks).
- **Phase 5 (SNIT calibration)**: shared, blind, on SNIT combined-echo images;
  the calibration verdict gates production.
- **Phase 6 (Production)**: partitioned, so pairwise Dice is computed only on
  the hidden overlap (about 10% of subjects, given to every rater), giving an
  ongoing reliability check on the production data. Volumes cover the rest as a
  distribution sanity check, and the protocol checks cover every mask.
