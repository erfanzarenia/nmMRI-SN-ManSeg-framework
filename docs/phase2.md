# Phase 2: Practice (Calibration)

**Purpose:** produce SN segmentations and calibrate against the expert masks
before blind work begins. The aim is a stable, reproducible approach with reduced
variability.

> **Important:** Phases advance as a group. A rater never moves to another phase
> ahead of the group, and does not work ahead. A rater stays within their own
> folder and does not open or browse other raters' directories.

**Labels:** SN only (label 1); the CP reference label starts in Phase 3.

## What the rater does

For each assigned subject, the rater:

1. Opens `inputs/<sub>/image.nii.gz` in ITK-SNAP.
2. Creates an SN segmentation.
3. Compares it against `inputs/<sub>/expert_mask.nii.gz` after each segmentation attempt is done, reviews differences, and refines the approach.

Attention goes to anatomical continuity across slices, smooth and plausible
boundaries, avoiding isolated noisy voxels, and consistency across subjects.
Where a boundary is uncertain, slightly exclusive labeling is preferred.

## Inputs / outputs

```
phase2_practice/<RATER>/
  inputs/<sub-XXXX>/image.nii.gz + expert_mask.nii.gz
  outputs/<sub-XXXX>/
```

## Output

Two independent attempts per subject:

```
outputs/<sub-XXXX>/<INIT>_mask_a1.nii.gz
outputs/<sub-XXXX>/<INIT>_mask_a2.nii.gz
outputs/<sub-XXXX>/<INIT>_mask_a1_unfilled.nii.gz   # before filling holes
outputs/<sub-XXXX>/<INIT>_mask_a2_unfilled.nii.gz   # before filling holes
```

Each mask also has its unfilled copy, saved before the holes are filled, with
`_unfilled` at the end of the name (see [itksnap_instructions.md](itksnap_instructions.md#save-the-mask)).

All first attempts (`_a1`) are completed across subjects, then the second
attempts (`_a2`) later. This reduces immediate recall bias and gives a better
estimate of consistency and reproducibility. Files in `inputs/` are not modified.

## Key idea

The aim is not only the correct SN location but a consistent, reproducible
segmentation style: superior/inferior extent, edge behavior, continuity across
slices, and how uncertain boundaries are handled. The expert masks serve as
calibration guidance.
