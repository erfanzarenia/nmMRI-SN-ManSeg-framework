# ITK-SNAP Quick Guide

A brief reference for segmenting the SN and CP in ITK-SNAP. Steps describe what
the rater does; what to segment is defined in
[segmentation_protocol.md](segmentation_protocol.md).

## Launch

From a terminal: `itksnap`.

## Open an image

`File → Open Main Image`, select `image.nii.gz` (Phase 6: `image_run_1_combo.nii.gz` or
`image_run_1_echo.nii.gz`, another run only when run 1 is unusable), then `Open`. The scan appears in axial,
sagittal, and coronal views.

## Adjust visibility

`Tools → Image Contrast` adjusts brightness/contrast until the SN is clearly
visible without washing out the background. Settings are kept consistent across slices within a subject.
Combined-echo images (`_combo`, Phases 5 and 6) look different from Echo 1 images
(`_echo` in Phase 6, and Phases 1 to 4), so their contrast is set afresh rather
than reused.

## Overlay the expert mask (phases 1–2 only)

`Segmentation → Open Segmentation`, select `expert_mask.nii.gz`, `OK`. The opacity
slider and visibility toggle control how it is shown.

## Create a new segmentation

1. `Segmentation → New Segmentation` (defaults accepted).
2. Import the shared labels: `Segmentation → Label Definitions → Import`, then
   select [`docs/SN_label_definition.txt`](SN_label_definition.txt): SN = value
   1 (red), CP = value 2 (green). This keeps labels consistent across raters.
3. Editing applies to the rater's own segmentation, not the expert mask.

## Drawing

- The **Active label** (Segmentation Labels panel, bottom-left) sets what is
  drawn: SN, then CP on the same slices (CP from Phase 3). It is checked before
  drawing.
- **Paint over** (same panel) is set to **Clear label** while drawing CP, so CP
  fills only unlabeled voxels and never overwrites SN.
- **Paintbrush** or **Polygon** mode (Main Toolbar, top-left) adds voxels.
- To erase, **Clear Label** is set as the active label and painted over voxels
  to remove them; with Paint over set to a single label, only that label is
  erased.
- Axial first, then verification in sagittal and coronal.
- `Continuous Update 3D View` (Ctrl+Shift+U) shows the structure build in real
  time.

## Save the mask

`Segmentation → Save As`, into the subject's `outputs/<sub>/` folder, named with
the rater's initials. One file holds every label (SN, and CP from Phase 3):

- Phases 3–5 (one attempt): **`<INITIALS>_mask.nii.gz`** (e.g. `R1_mask.nii.gz`).
  A Phase 5 re-segmentation, when requested, is **`<INITIALS>_mask_a2.nii.gz`**.
- Phase 6 (one mask per subject, on one run): always end the name with **`_run_<N>_<image>`**,
  copied from the image the mask was drawn on: `image_run_1_combo.nii.gz` gives
  **`<INITIALS>_mask_run_1_combo.nii.gz`**, `image_run_1_echo.nii.gz` gives
  **`<INITIALS>_mask_run_1_echo.nii.gz`** (see [phase6.md](phase6.md)).
- Phases 1–2 (two attempts): **`<INITIALS>_mask_a1.nii.gz`** then
  **`<INITIALS>_mask_a2.nii.gz`** (see [phase2.md](phase2.md)).

**Unfilled copy (every phase).** Before filling holes in the mask, save it once
as it is, with **`_unfilled`** added to the end of the usual name:
`R1_mask_unfilled.nii.gz`, `R1_mask_a1_unfilled.nii.gz`,
`R4_mask_run_1_combo_unfilled.nii.gz`. Then fill the holes and save the mask under
its usual name, without `_unfilled`. Both files stay in `outputs/<sub>/`. The
unfilled copy is checked on its own and never replaces the filled mask.

## Tips

- Zoom in for precision; verify in all three views.
- Stepping one slice forward/back repeatedly keeps boundaries continuous.
- Masks are kept smooth, avoiding isolated noisy voxels.

## Common mistakes

- Forgetting to create a new segmentation (and editing the expert mask).
- Saving in the wrong directory or with the wrong filename.
- Saving the filled mask with `_unfilled` in its name, or filling the holes before
  saving the unfilled copy.
- Phase 6: leaving `_run_<N>` or `_combo`/`_echo` off the mask name (QC does not
  use such a mask), or copying them from a different image.
- Inconsistent boundaries across slices.
- Drawing with the wrong active label, or CP painted over SN.

## Tutorial

New to ITK-SNAP: <https://youtu.be/OpYJiY_GoeY>

## If something breaks

Restart ITK-SNAP and reload the image. Persistent issues are reported to the
study coordinator.
