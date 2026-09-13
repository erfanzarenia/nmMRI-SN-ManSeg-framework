# ITK-SNAP Quick Guide

A brief reference for segmenting the SN and CP in ITK-SNAP. Steps describe what
the rater does; what to segment is defined in
[segmentation_protocol.md](segmentation_protocol.md).

## Launch

From a terminal on the server: `itksnap`.

## Open an image

`File → Open Main Image`, select `image.nii.gz`, then `Open`. The scan appears in
axial, sagittal, and coronal views.

## Adjust visibility

`Tools → Image Contrast` adjusts brightness/contrast until the SN is clearly
visible without washing out the background. Settings are kept consistent across slices within a subject.

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
- Phases 1–2 (two attempts): **`<INITIALS>_mask_a1.nii.gz`** then
  **`<INITIALS>_mask_a2.nii.gz`** (see [phase2.md](phase2.md)).

## Tips

- Zoom in for precision; verify in all three views.
- Stepping one slice forward/back repeatedly keeps boundaries continuous.
- Masks are kept smooth, avoiding isolated noisy voxels.

## Common mistakes

- Forgetting to create a new segmentation (and editing the expert mask).
- Saving in the wrong directory or with the wrong filename.
- Inconsistent boundaries across slices.
- Drawing with the wrong active label, or CP painted over SN.

## Tutorial

New to ITK-SNAP: <https://youtu.be/OpYJiY_GoeY>

## If something breaks

Restart ITK-SNAP and reload the image. Persistent issues are reported to Erfan
(ezarenia@uwo.ca).
