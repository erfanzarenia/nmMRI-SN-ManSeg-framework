# Phase 4 — Exam (Reliability Test)

**Purpose:** measure how consistent and accurate raters are under real annotation
conditions. This is the scientifically important phase. Subjects are **shared**
across raters to generate inter-rater reliability data.

> **Important:** Phases advance as a group. A rater never moves to another phase
> ahead of the group, and does not work ahead. A rater stays within their own
> folder and does not open or browse other raters' directories.

## What the rater does

For each assigned subject, the rater:

1. Opens `inputs/<sub>/image.nii.gz` in ITK-SNAP.
2. Creates one SN segmentation, blindly.

Ground truth is not sought, previous masks are not reused, and cases are not
discussed with others.

## Inputs / outputs

```
phase4_exam/<RATER>/
  inputs/<sub-XXXX>/image.nii.gz
  outputs/<sub-XXXX>/
```

## Output

```
outputs/<sub-XXXX>/<INIT>_mask.nii.gz
```

## Quality control

Raters do not run QC or evaluate their own results. Inter-rater metrics are
computed centrally (see [qc.md](qc.md)) and shared back when appropriate.

## Key idea

This measures internal consistency and agreement across raters.
