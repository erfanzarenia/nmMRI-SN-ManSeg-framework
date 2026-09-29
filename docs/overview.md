# Workflow Overview

Manual segmentation of the substantia nigra (SN) on neuromelanin-sensitive MRI
(NM-MRI), with the cerebral peduncle (CP) segmented as a reference region. The
work is organized into six phases that gradually train raters, ensure
consistency, and produce reliable data for analysis. Each phase builds on the
previous one; later phases are not opened until the earlier ones are complete.

## Big picture

- Early phases (1–2): learning and calibration against expert masks.
- Middle phases (3–4): a blind rehearsal, then the reliability measurement.
- Bridge phase (5): calibration on SNIT production images (combined echoes).
- Final phase (6): generation of the research dataset at scale.

In one line: phase 1 learns what correct looks like, phase 2 calibrates, phase 3
rehearses blind, phase 4 measures reliability, phase 5 transfers to SNIT images,
phase 6 scales.

## Phases

| Phase | Name             | Pool    | Expert mask | Labels  | Rater output        | Assignment  |
|-------|------------------|---------|-------------|---------|---------------------|-------------|
| 1     | Playground       | labeled | yes         | SN      | `<INIT>_mask_a1/a2` | shared      |
| 2     | Practice         | labeled | yes         | SN      | `<INIT>_mask_a1/a2` | shared      |
| 3     | Mock exam        | labeled | no          | SN + CP | `<INIT>_mask`       | shared      |
| 4     | Exam             | labeled | no          | SN + CP | `<INIT>_mask`       | shared      |
| 5     | SNIT calibration | study   | no          | SN + CP | `<INIT>_mask`       | shared      |
| 6     | Production       | study   | no          | SN + CP | `<INIT>_mask`       | partitioned |

- **shared**: every rater segments the same subjects (enables inter-rater
  comparison).
- **partitioned**: subjects are divided across raters (scale; each subject
  segmented once), with a hidden overlap kept for reliability (see Phase 6).
- **labeled** vs **study**: two disjoint subject pools that live in different
  places. The labeled (training) set has expert masks and Echo 1 images; the
  SNIT study set has no expert masks and combined-echo images. Each is a *pool*
  in [`config/paths.yaml`](../config/paths.yaml).
- **SN / CP**: label 1 = substantia nigra; label 2 = cerebral peduncle, a
  reference region added from Phase 3 (Phases 1–2 are SN-only). One mask file
  holds both; the rules are in
  [segmentation_protocol.md](segmentation_protocol.md).

Phase guides: [phase1](phase1.md) · [phase2](phase2.md) · [phase3](phase3.md) ·
[phase4](phase4.md) · [phase5](phase5.md) · [phase6](phase6.md). Protocol:
[segmentation_protocol](segmentation_protocol.md). Tool:
[itksnap_instructions](itksnap_instructions.md). QC: [qc](qc.md).

## Scan naming and blinding

- Source scans are relabeled to anonymized workspace IDs (e.g. `sub-0001`) that
  do **not** match the source IDs, so a rater cannot trace a scan back to the
  source dataset or its expert mask. The assignment is random but reproducible
  from the seed in [`config/phases.yaml`](../config/phases.yaml).
- Each workspace subject folder holds `image.nii.gz` (plus `expert_mask.nii.gz`
  in the expert-guided phases). Rater outputs are `<INIT>_mask.nii.gz` (single
  attempt) or `<INIT>_mask_a1.nii.gz … _aN.nii.gz` (multiple attempts), each
  holding every label of the phase.
- The source dataset may be BIDS-style or a single flat folder, with `.nii` or
  `.nii.gz` files (set `layout` per pool in
  [`config/paths.yaml`](../config/paths.yaml)); every workspace input is
  normalized to gzip-compressed `.nii.gz`.
- The workspace → source key is written to `config/subjects_split.json`. It is
  kept private, never shared with raters, and is git-ignored.

## Workspace layout

`make_workspace.py` builds this tree under `workspace_root` (outside the repo),
identically for every phase, and copies the rater-facing guides into a shared
`docs/` folder so they sit next to the data:

```
<workspace_root>/
  docs/                            # copies of this overview, the segmentation
                                   # protocol, the phase guides, the ITK-SNAP
                                   # guide, and the label file
  <phaseN_name>/
    <RATER>/
      inputs/<sub-XXXX>/image.nii.gz [+ expert_mask.nii.gz]
      outputs/<sub-XXXX>/          # rater saves masks here
```

## Run

```bash
conda env create -f environment.yml
conda activate nmmri-manseg

# 1. Configure config/: paths.yaml (pools + workspace), raters.json,
#    phases.yaml, and the subject lists.
# 2. Assign subjects to phases/raters (seed from phases.yaml). Append-only:
#    re-running allocates new phases and never moves existing assignments.
python scripts/split_subjects.py

# 3. Build the workspace (--dry-run previews it). Existing inputs are never
#    overwritten, so re-running only adds what is missing.
python scripts/make_workspace.py

# 4. Raters segment in ITK-SNAP (see itksnap_instructions.md).

# 5. QC, run centrally (exam shown).
python scripts/run_qc.py phase4
```

## Rules

These apply throughout and are repeated in every phase guide:

- **Phases advance as a group.** Raters who start together form a group, and a
  rater never starts the next phase until their whole group is advanced
  together. Raters who join later start at Phase 1 as a new group. No working
  ahead, and no opening or exploring later phases.
- **A rater stays in their own folder.** A rater never opens, browses, or copies
  from another rater's directories. segmentations are produced independently.
- A rater works only within their own rater folder, identified by initials.
- External or expert labels are not sought on the server.
- Files inside `inputs/` are never modified.
