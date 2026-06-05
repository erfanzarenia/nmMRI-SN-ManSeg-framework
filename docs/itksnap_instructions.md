# ITK-SNAP Quick Guide

A brief reference for segmenting the SN in ITK-SNAP. Steps describe what the
rater does.

## Launch

From a terminal on the server: `itksnap`.

## Open an image

`File → Open Main Image`, select `image.nii.gz`, then `Open`. The scan appears in
axial, sagittal, and coronal views.

## Adjust visibility

`Tools → Image Contrast` adjusts brightness/contrast until the SN is clearly
visible without washing out the background. Settings are kept consistent across slices with a subject.

## Overlay the expert mask (phases 1 only)

`Segmentation → Open Segmentation`, select `expert_mask.nii.gz`, `OK`. The opacity
slider and visibility toggle control how it is shown.

## Create a new segmentation

1. `Segmentation → New Segmentation` (defaults accepted).
2. Import the shared labels: `Segmentation → Label Definitions → Import`, then
   select [`docs/SN_label_definition.txt`](SN_label_definition.txt) (SN = value
   1). This keeps labels consistent across raters.
3. Editing applies to the rater's own segmentation, not the expert mask.

## Drawing

- **Paintbrush** or **Polygon** mode (Main Toolbar, top-left) adds voxels.
- To erase, **Clear Label** is set as the active label (Segmentation Labels
  panel, bottom-left) and painted over voxels to remove them.
- Axial first, then verification in sagittal and coronal.
- `Continuous Update 3D View` (Ctrl+Shift+U) shows the structure build in real
  time.

## Save the mask

`Segmentation → Save As`, into the subject's `outputs/<sub>/` folder, named with
the rater's initials:

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

## Tutorial

New to ITK-SNAP: <https://youtu.be/OpYJiY_GoeY>

## If something breaks

Restart ITK-SNAP and reload the image. Persistent issues are reported to Erfan
(ezarenia@uwo.ca).
