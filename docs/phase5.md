# Phase 5: Calibration (Bridge to Production)

**Purpose:** carry the protocol over to the exact images used in production.
When production uses other images than Phases 1 to 4 (in the example
configuration, combined echoes instead of Echo 1), a few scans are segmented
with the unchanged protocol so that any shift caused by the new images is caught
before production. This is a short bridge, not another exam.

> **Important:** Phases advance as a group. A rater never moves to another phase
> ahead of the group, and does not work ahead. A rater stays within their own
> folder and does not open or browse other raters' directories.

**Labels:** SN (label 1) and CP (label 2), in one mask (see
[segmentation_protocol.md](segmentation_protocol.md)).

## What changes with the production images

Same protocol; only the image differs. What to watch for:

- **Contrast.** A combined-echo image is more T2*-weighted than Echo 1, so
  vessels and tissue edges look darker and more pronounced. SN still follows its
  neuromelanin-bright band and CP still excludes vessels and edge voxels; darker
  surroundings do not move either boundary. Window settings from earlier images
  do not carry over: contrast is set afresh on each scan.
- **Geometry.** Another voxel size changes how many axial slices the SN spans
  and how blocky the sagittal and coronal views look. With finer in-plane voxels
  a brush of the same size in voxels covers less tissue, so the brush is enlarged
  to cover about the same area.

_For the coordinator: the specifics of your images (sequence, echo times, voxel
size, brush size) go in your own version of this guide (see `config/README.md`
in the repository)._

## What the rater does

For each calibration subject, the rater:

1. Opens `inputs/<sub>/image.nii.gz` in ITK-SNAP and sets the contrast.
2. Creates one segmentation (SN, then CP), blindly, exactly as in the Exam.

Calibration scans are not discussed until every rater has submitted all of them.

## Inputs / outputs

```
phase5_calibration/<RATER>/
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
accuracy. Each rater must meet all four. The thresholds are the phase's
`criteria` in `phases.yaml`; the values below are the example configuration's,
for 5 calibration scans:

| Criterion    | Requirement |
|--------------|-------------|
| Protocol     | every calibration mask saved and named correctly, with no rule flags: only labels 1–2, SN and CP in both hemispheres, CP on every SN slice and no other, no CP voxel touching SN |
| SN agreement | median Dice with the other raters ≥ 0.60 and median HD95 ≤ 3.0 mm |
| SN volume    | within ±30% of the scan's median across raters on at least 4 of the 5 scans |
| CP reference | mean CP intensity within ±5% of the scan's median across raters on at least 4 of the 5 scans |

## After calibration

- A review session walks through the calibration scans with all raters'
  contours, focused on the boundaries the new images changed.
- When every rater meets all criteria, production opens for the group.
- A rater who misses a criterion gets feedback on the flagged scans and
  re-segments only those, once, saving `<INIT>_mask_a2.nii.gz` next to the
  original; they are re-checked against the same criteria. No new scans, and no
  second full round.

## Key idea

Same protocol, production images: the final check that what was learned on the
training images carries over.
