# Segmentation Protocol

What is segmented, and the rules for each label. Label values come from
[`SN_label_definition.txt`](SN_label_definition.txt), imported in ITK-SNAP (see
[itksnap_instructions.md](itksnap_instructions.md)); one mask file per scan
(per attempt) holds every label.

| Value | Label | Structure                                          | Color | Phases   |
|-------|-------|----------------------------------------------------|-------|----------|
| 1     | SN    | substantia nigra (neuromelanin-bright)             | red   | all      |
| 2     | CP    | cerebral peduncle (crus cerebri), reference region | green | 3 onward |

Phases 1–2 are SN-only, so their masks never contain CP.

## SN: substantia nigra (label 1)

The neuromelanin-bright band in the ventral midbrain, just dorsal (posterior) to
the crus cerebri. It is segmented in both hemispheres, on every axial slice
where it is visible. Attention goes to continuity across slices, smooth and
plausible boundaries, no isolated noisy voxels, and consistency across
subjects; where a boundary is uncertain, slightly exclusive labeling is
preferred. The expert masks of Phases 1–2 are the reference for extent and edge
behavior; an anatomical borders guide or atlas, if the group uses one, sits with
these guides in the workspace `docs/` folder.

## CP: cerebral peduncle (label 2)

**What:** the crus cerebri, the white-matter band forming the anterior part of
the cerebral peduncle, anterolateral to the SN. On NM-MRI it is a fairly
uniform dark band between the SN and the CSF surrounding the midbrain.

**Why:** CP is a background (reference) region. Its mean intensity is the
reference against which SN signal is measured, so a clean, homogeneous region
matters more than covering the whole peduncle.

**Rules:**

1. **Slices:** CP is drawn on exactly the axial slices that contain SN (every
   SN slice and no other), in both hemispheres.
2. **SN side:** at least one unlabeled voxel is left between CP and SN; CP never
   touches or overwrites SN.
3. **CSF side:** CP stays inside tissue. The bright CSF around the peduncle
   (including the interpeduncular fossa medially) and the partial-volume voxels
   at the tissue edge are excluded.
4. **Exclusions:** vessels (bright or dark dots and lines along the midbrain
   surface) and, on the upper slices, the optic tract where it wraps around the
   lateral edge of the peduncle.
5. **When unsure, leave it out.**

SN is segmented first, then CP on the same slices. These rules are checked
centrally during QC.
