# Phase 5 — Production (Real Dataset)

**Purpose:** generate the final dataset for analysis. Subjects are
**partitioned** across raters so each is segmented once and coverage scales. This
is the actual research output.

> **Important:** Phases advance as a group — a rater never moves to another phase
> ahead of the group, and does not work ahead. A rater stays within their own
> folder and does not open or browse other raters' directories.

## What the rater does

For each assigned subject, the rater:

1. Opens `inputs/<sub>/image.nii.gz` in ITK-SNAP.
2. Creates one SN segmentation, blindly, consistent with Phase 4 behavior.

## Inputs / outputs

```
phase5_production/<RATER>/
  inputs/<sub-XXXX>/image.nii.gz
  outputs/<sub-XXXX>/
```

## Output

```
outputs/<sub-XXXX>/<INIT>_mask.nii.gz
```

## Reliability monitoring

A subset of production subjects is assigned to more than one rater (hidden
repeats) so reliability can be monitored on the production set. Which subjects
these are is not disclosed; every subject is segmented with the same care.

## Key idea

This is where data quality matters most.
