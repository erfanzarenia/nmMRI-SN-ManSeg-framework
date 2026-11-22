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
  unfilled/                    # the same layout for the _unfilled masks (see below)
  archive/<timestamp>/         # earlier runs
```

Start with `<phase>/coverage.md`, then the reports in
`<phase>/<attempt>/reports/`; each opens with a Methods note.

## Unfilled masks

Raters fill the holes in a mask before its final save, and first save it as it
was with `_unfilled` at the end of the usual name (`R1_mask_a1_unfilled`,
`R4_mask_run_1_combo_unfilled`; also read: `_Unfilled`, `-unfilled`,
`_un-filled`, anywhere in the name). The two are kept entirely apart:

- The usual QC in `qc/` never sees a file named `_unfilled`.
- The unfilled masks get a QC of their own, with the same workflow and criteria
  (coverage, run check, attempts, metrics, figures, protocol and calibration
  verdicts, reports, summary), in `qc/unfilled/`, laid out exactly like `qc/`
  with its own README, phases, summary and `archive/`. Archiving `qc/` at the
  start of a run never moves `unfilled/`. Inside it each mask is read as if `_unfilled` were not
  in its name, so the run, image and attempt rules below apply unchanged.
- `run_qc.py` runs both, in that order. The unfilled QC covers every phase (of
  those asked for) with at least one `_unfilled` mask, and is skipped, leaving
  `qc/unfilled/` as it is, when there are none. `--masks filled` or `--masks
  unfilled` runs only one of them.

```bash
python scripts/run_qc.py phase5 phase6                    # both QCs
python scripts/run_qc.py phase6 --masks unfilled          # only qc/unfilled/
```

## Run check (gate)

In a phase with `runs` (Phase 6), runs are segmented in order: run 1, then run 2,
and so on. The raters of an overlap subject are then always comparable on the
runs they share, even when some are further along. `run_qc.py` checks this for
every subject and writes `<phase>/run_check.csv`, a "Run check" section at the
end of `coverage.md`, a line in every report and the result on the console:

- **FAIL**: on an overlap subject, a rater skipped a run (e.g. saved run 2 but no
  run 1), so their runs do not line up with the other raters'. This is often a
  mask saved under the wrong run number; ask the rater which image it was drawn on.
  With `echoes` (Phase 6) it also fails when a rater segmented another image of a
  run than the other raters (e.g. run 1 on `_echo` while the others used
  `_combo`): masks are only compared on the same run and image, so that run cannot
  be compared. Ask the rater to also segment the image the others used. Each line
  lists the runs with their image, e.g. `run 1 combo, 2 echo`.
- **Check**: a skipped run on a subject only that rater has, or on an overlap
  subject where every rater skipped the same run (e.g. all have run 2 only), so
  they still line up. Worth asking whether the run was skipped on purpose.

A failing check does not stop QC. The metrics still run, so resolve the listed
scans before the final analysis.

## SN volume and extent

Every report has an "SN volume and extent" section, from `metrics/volumes.csv`
(per rater and scan: SN voxels, volume in mm³, and how many axial, coronal and
sagittal slices the SN spans, also in mm):

- per rater over all their scans (`volume_by_rater.csv`);
- on the overlap scans (2+ raters): the average over those scans, each rater's
  values on them (`volume_overlap_by_rater.csv`), and each scan across its raters
  with the volume CV, SD / mean (`volume_overlap_by_scan.csv`,
  `volume_overlap_summary.csv`).

Each table gives mean, SD, min and max. A high CV or a wide range of axial slices
on an overlap scan means the raters disagree on how far the SN extends.

## Phase summary

Each report's headline gives the **phase summary** first: the average of
per-scan averages. Each metric is averaged within a scan first (over its rater
pairs, or over its raters against the expert), then over the scans, so every
scan counts once however many raters it has; `±` is the SD across scans. The
grand mean over all pair-scans (or rater-scans) follows; the two agree while
every scan has the same raters. `metrics/rvr_phase_summary.csv` (and
`rve_phase_summary.csv`) hold both, one row each, told apart by the `summary`
column (`average_of_scan_means`, `grand_mean`). With 2+ phases,
`summary/overall_qc_report.md` lists the phase summary of each phase and of all
of them together.

**Medians:** every summary table also has a `<metric>_median` column next to
`<metric>_mean` (in the metric CSVs). The reports show the two together as
`mean (median)`, and the headlines add the median in parentheses. A median well
off the mean points to a few scans or pairs pulling the mean.

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

In a phase with `runs` (Phase 6), each acquisition is its own scan: tables,
figures and coverage list `sub-0029_run_1` and `sub-0029_run_2` separately, and
raters are compared run by run. With `echoes`, a run is offered as two images
(`_combo`, `_echo`) and the rater segments one of them. Each image is then its own
scan in the metrics (`sub-0029_run_1_combo`, `sub-0029_run_1_echo`): a mask is only
compared with masks of the same run and the same image, never a combined-echo mask
with an Echo 1 mask. Coverage keeps one row per run and names the image segmented
(`a1 combo`), and the run check fails an overlap subject whose raters chose
different images for a run.

A mask counts for the run its name gives: the number after "run", with any
separator or none and leading zeros allowed (`_run_2`, `_run2`, `_run-02`,
`Run 2`, `_run.2`), or else the only number left once the attempt (`_a1`), Echo 1
(`_echo1`) and subject ID (`sub-0029`) tokens are set aside (`R4_mask_2`,
`R7_mask_a1_2`). A file with no such number, or with two different ones, is never
assumed to be a run: it is not used, and coverage notes it as "not used, no run in
the name". The image label is read from `_combo` (also `_comb`, `_combined`) or
`_echo` (also `_echo1`, `_echo-1`), with any separator and in any case. Raters
must name both the run and the image: a mask without an image label is not used,
and coverage notes it as "not used, no image (_combo or _echo) in the name". A
rater who saved both images of one run has a mask on each image's scan. Masks saved before runs were added have a
`<INIT>_mask_run_1_combo` symlink next to them; that symlink is the run 1 mask, and
the file it points to is counted once. Runs of a subject usually share one image
grid, so the grid check below cannot catch a mask saved under the wrong run: only
its name says which run it is. A file whose name is not `.nii` or `.nii.gz` (e.g.
`R7_mask_run_2_echo2nii.gz`) is not read at all and is noted as "not used, not a
.nii.gz file name". A run number right before the image label (`_run_1combo`)
counts as a separator.

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
expert and rater vs rater. Rater vs expert runs for every scan that has an
expert mask, including the blind phases (3 and 4). QC always reads the expert
from the source data through the private key (raters never get it in blind
phases); a copy in a rater's `inputs/` is used only if the key or the source is
unavailable. No source ID or file name appears in the QC outputs. Surface distances are
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
IDs; those also listed in `overlap_subjects` are production overlap subjects as
well, so every rater segments them again in Phase 6. The production masks are the ones used for
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
- **Phase 3 (Mock exam)**: pairwise inter-rater and rater vs expert (read from
  the source data, never given to raters) on the blind rehearsal set; a sanity
  check before the real Exam. It is the first phase with CP, so the
  protocol flags are fed back before the Exam.
- **Phase 4 (Exam)**: inter-rater reliability (shared, blind). The primary QC
  target, for SN (metrics) and CP (protocol checks).
- **Phase 5 (SNIT calibration)**: shared, blind, on SNIT combined-echo images;
  the calibration verdict gates production.
- **Phase 6 (Production)**: partitioned, so pairwise Dice is computed only on
  the hidden overlap (about 10% of subjects, given to every rater), giving an
  ongoing reliability check on the production data. Volumes cover the rest as a
  distribution sanity check, and the protocol checks cover every mask.
