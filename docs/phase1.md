# Phase 1 — Playground (Familiarization)

**Purpose:** familiarization with ITK-SNAP and SN anatomy on NM-MRI, plus a
first, low-pressure pass at producing segmentations. The goal is intuition for
where the SN is, how its boundaries look, and how it differs from surrounding
structures. The structure mirrors Phase 2; only the purpose differs.

> **Important:** Phases advance as a group. A rater never moves to another phase
> ahead of the group, and does not work ahead. A rater stays within their own
> folder and does not open or browse other raters' directories.

## What the rater does

For each assigned subject, the rater:

1. Opens `inputs/<sub>/image.nii.gz` in ITK-SNAP.
2. Overlays `inputs/<sub>/expert_mask.nii.gz`, toggling it and scrolling through
   slices to locate the SN.
3. Creates a segmentation (see [itksnap_instructions.md](itksnap_instructions.md))
   and compares it against the expert mask.

## Inputs / outputs

```
phase1_playground/<RATER>/
  inputs/<sub-XXXX>/image.nii.gz + expert_mask.nii.gz
  outputs/<sub-XXXX>/
```

## Output

Two independent attempts per subject, named with the rater's initials:

```
outputs/<sub-XXXX>/<INIT>_mask_a1.nii.gz
outputs/<sub-XXXX>/<INIT>_mask_a2.nii.gz
```

No evaluation is performed in this phase. Files in `inputs/` are not modified.

## Key idea

Building a mental model of the SN before the real work begins, and getting
comfortable with the create → compare → refine loop.
