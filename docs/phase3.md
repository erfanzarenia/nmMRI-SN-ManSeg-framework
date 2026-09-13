# Phase 3: Mock Exam (Blind Pilot)

**Purpose:** a low-stakes rehearsal of real exam conditions on a small set before
the scored Exam. Segmentation is fully blind (no expert mask), exactly as in
Phase 4, but the results are not used for the final reliability statistics.

> **Important:** Phases advance as a group. A rater never moves to another phase
> ahead of the group, and does not work ahead. A rater stays within their own
> folder and does not open or browse other raters' directories.

**Labels:** SN (label 1) and CP (label 2), in one mask. CP is new in this phase;
its rules in [segmentation_protocol.md](segmentation_protocol.md) are read
before starting.

## What the rater does

For each assigned subject, the rater:

1. Opens `inputs/<sub>/image.nii.gz` in ITK-SNAP.
2. Creates one segmentation with no reference mask: SN, then CP on the same
   slices.

Conditions match the real Exam, so workflow, timing, and contrast settings can be
settled in advance.

## Inputs / outputs

```
phase3_mock_exam/<RATER>/
  inputs/<sub-XXXX>/image.nii.gz
  outputs/<sub-XXXX>/
```

## Output

```
outputs/<sub-XXXX>/<INIT>_mask.nii.gz
```

## Quality control

Raters do not run QC or evaluate their own results in this phase. QC is performed
centrally; relevant results are shared back when appropriate.

## Key idea

A dry run of the Exam. Ground truth is not sought and earlier masks are not
reused.
