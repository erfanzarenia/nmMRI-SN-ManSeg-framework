# Phase 5: SNIT Calibration (Bridge to Production)

**Purpose:** carry the protocol over to the exact images used in production.
Phases 1–4 train on Echo 1 images; SNIT production images combine all echoes.
Five SNIT scans are segmented with the unchanged protocol so that any shift
caused by the new images is caught before production. This is a short bridge,
not another exam.

> **Important:** Phases advance as a group. A rater never moves to another phase
> ahead of the group, and does not work ahead. A rater stays within their own
> folder and does not open or browse other raters' directories.

**Labels:** SN (label 1) and CP (label 2), in one mask (see
[segmentation_protocol.md](segmentation_protocol.md)).

## What changes in SNIT images

Same 7T NM-GRE sequence and the same protocol; only the image differs.

- **Combined echoes.** The three echoes (TE ≈ 4, 9, 13 ms) are combined (sum of
  squares) instead of Echo 1 alone. The image is more T2*-weighted, so vessels
  and tissue edges look darker and more pronounced. SN still follows its
  neuromelanin-bright band and CP still excludes vessels and edge voxels; the
  darker surroundings do not move either boundary.
- **Geometry.** Voxels are 0.23 × 0.23 × 1.5 mm instead of 0.4 × 0.4 × 1.0 mm:
  finer in-plane, thicker slices. The SN spans about two-thirds as many axial
  slices, sagittal and coronal views look blockier, and a brush of the same
  voxel size covers about a third of the area, so the brush is enlarged (about
  1.75×).
- **Contrast.** Echo 1 window settings do not carry over; contrast is set
  afresh on each scan.

## What the rater does

For each of the five subjects, the rater:

1. Opens `inputs/<sub>/image.nii.gz` in ITK-SNAP and sets the contrast.
2. Creates one segmentation (SN, then CP), blindly, exactly as in the Exam.

Calibration scans are not discussed until every rater has submitted all five.

## Inputs / outputs

```
phase5_snit_calibration/<RATER>/
  inputs/<sub-XXXX>/image.nii.gz
  outputs/<sub-XXXX>/
```

## Output

```
outputs/<sub-XXXX>/<INIT>_mask.nii.gz
outputs/<sub-XXXX>/<INIT>_mask_unfilled.nii.gz   # before filling holes
```

Each mask also has its unfilled copy, saved before the holes are filled, with
`_unfilled` at the end of the name (see [itksnap_instructions.md](itksnap_instructions.md#save-the-mask)).

## Passing criteria

Checked centrally once all raters have submitted. With no expert mask, the
criteria measure consistency with the other raters on the same scans, not
accuracy. Each rater must meet all four:

| Criterion    | Requirement |
|--------------|-------------|
| Protocol     | all five masks saved and named correctly, with no rule flags: only labels 1–2, SN and CP in both hemispheres, CP on every SN slice and no other, no CP voxel touching SN |
| SN agreement | median Dice with the other raters ≥ 0.60 and median HD95 ≤ 3.0 mm |
| SN volume    | within ±30% of the scan's median across raters on at least 4 of 5 scans |
| CP reference | mean CP intensity within ±5% of the scan's median across raters on at least 4 of 5 scans |

## After calibration

- One conductor-led review walks through the five scans with all raters'
  contours, focused on boundaries the combined echoes changed.
- When every rater meets all criteria, production opens for the group.
- A rater who misses a criterion gets feedback on the flagged scans and
  re-segments only those, once, saving `<INIT>_mask_a2.nii.gz` next to the
  original; they are re-checked against the same criteria. No new scans, and no
  second full round.

## Key idea

Same protocol, production images: the final check that what was learned on
Echo 1 carries over to combined echoes.
