# Phase 6: Production (Real Dataset)

**Purpose:** generate the final dataset for analysis. Subjects are
**partitioned** across raters so each is segmented once and coverage scales. This
is the actual research output.

> **Important:** Phases advance as a group. A rater never moves to another phase
> ahead of the group, and does not work ahead. A rater stays within their own
> folder and does not open or browse other raters' directories.

> **Always save with the run and the image in the name:**
> `<INIT>_mask_run_<N>_<image>.nii.gz`, copied from the name of the image the mask
> was drawn on: `image_run_2_combo.nii.gz` gives `R4_mask_run_2_combo.nii.gz`, and
> `image_run_2_echo.nii.gz` gives `R4_mask_run_2_echo.nii.gz`. Do not save as
> `<INIT>_mask.nii.gz`, `<INIT>_mask_a1.nii.gz` or `<INIT>_mask_run_<N>.nii.gz`:
> those names do not say which run and image a mask came from, so QC does not use
> a mask saved under them. Check the name before every save. QC cannot catch a wrong run or image label, because all images
> of a subject usually share one image grid.

**Labels:** SN (label 1) and CP (label 2), in one mask (see
[segmentation_protocol.md](segmentation_protocol.md)).

**Images:** each NM acquisition of a subject is a run, so a subject scanned more
than once has `run_1`, `run_2` and so on, and one scanned once has only `run_1`.
Each run comes as two images of the same acquisition:

- `image_run_<N>_combo.nii.gz`: the combined echoes, as in Phase 5
  (Calibration), with the same contrast and brush adjustments.
- `image_run_<N>_echo.nii.gz`: Echo 1, the kind of image used in Phases 1 to 4.
  Set its contrast afresh.

**One run per subject is enough.** Segment run 1, and use run 2 (or run 3) only
when run 1 is unusable, for example because of motion. A subject with a mask on
any one run is done: no second run is needed, and a subject already segmented on
another run is not redone. That run is segmented once, on whichever of its two
images shows the SN and CP better. The Echo 1 image is an option, not extra work:
one mask per subject, never one per image.

## What the rater does

For each assigned subject, the rater:

1. Opens `inputs/<sub>/image_run_1_combo.nii.gz` or `image_run_1_echo.nii.gz` in
   ITK-SNAP, whichever gives the better contrast (run 2 instead only when run 1
   is unusable).
2. Creates one segmentation (SN, then CP), blindly, consistent with the Exam
   and the calibration, from scratch: a mask is never copied from, or
   loaded onto, another run.
3. Saves it as **`<INIT>_mask_run_<N>_<image>.nii.gz`**, with the run number and
   the image label (`combo` or `echo`) of the image it was drawn on.
   Before filling any holes it first saves the mask as it is, with `_unfilled`
   added at the end: `<INIT>_mask_run_<N>_<image>_unfilled.nii.gz`. Then it fills
   the holes and saves under the name without `_unfilled`.

## Inputs / outputs

```
phase6_production/<RATER>/
  inputs/<sub-XXXX>/image_run_1_combo.nii.gz
                    image_run_1_echo.nii.gz
                    image_run_2_combo.nii.gz   # when the subject has a second run
                    image_run_2_echo.nii.gz
                    image_run_3_combo.nii.gz   # when the subject has a third run
                    image_run_3_echo.nii.gz
  outputs/<sub-XXXX>/
```

## Output

One mask per subject, for example:

```
outputs/<sub-XXXX>/<INIT>_mask_run_1_combo.nii.gz   # run 1 drawn on the combined echoes
outputs/<sub-XXXX>/<INIT>_mask_run_1_echo.nii.gz    # or: run 1 drawn on Echo 1
outputs/<sub-XXXX>/<INIT>_mask_run_2_combo.nii.gz   # or: run 2, when run 1 was unusable
```

It also has its unfilled copy, saved before the holes are filled:

```
outputs/<sub-XXXX>/<INIT>_mask_run_1_combo_unfilled.nii.gz
```

## Reliability monitoring

A subset of production subjects is assigned to more than one rater (hidden
repeats) so reliability can be monitored on the production set. Which subjects
these are is not disclosed; every subject is segmented with the same care.

## Key idea

This is where data quality matters most.
