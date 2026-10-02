"""Shared helpers for the SN manual-segmentation scripts.

Kept deliberately small: config loading, label values, source-data and
phase -> rater -> subject path conventions, and NIfTI/Dice helpers. The on-disk
layout has a single source of truth here, so make_workspace.py, split_subjects.py
and the QC scripts never disagree about it.

numpy/nibabel are imported lazily, so split_subjects.py and make_workspace.py
run with only PyYAML installed.
"""
from __future__ import annotations

import json
import re
from glob import glob
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"

_PHASE_RE = re.compile(r"^phase(\d+)$")


# ------------------------------ config -------------------------------
def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)


def load_json(path):
    with open(path) as f:
        return json.load(f)


def load_config(config_dir=CONFIG_DIR):
    """Return (paths, phases, raters) loaded from a config directory."""
    config_dir = Path(config_dir)
    paths = load_yaml(config_dir / "paths.yaml")
    phases = load_yaml(config_dir / "phases.yaml")
    raters = load_json(config_dir / "raters.json")["raters"]
    return paths, phases, raters


def read_subject_list(path):
    """Read a subject-list file: one BIDS label per line, '#' starts a comment."""
    subjects = []
    for line in Path(path).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            subjects.append(line)
    return subjects


def phase_keys(phases):
    """Phase keys (phase1, phase2, ...) ordered numerically, ignoring any
    non-phase keys such as the top-level 'seed'."""
    keys = [k for k in phases if _PHASE_RE.match(k)]
    return sorted(keys, key=lambda k: int(_PHASE_RE.match(k).group(1)))


# ------------------------------ labels -------------------------------
# Label values in rater and expert masks, matching the ITK-SNAP label file
# docs/SN_label_definition.txt. CP (cerebral peduncle) is the reference region,
# segmented from Phase 3 on; each phase lists its labels in phases.yaml.
LABELS = {"SN": 1, "CP": 2}


def phase_labels(pcfg):
    """Label names segmented in a phase (SN only when unspecified)."""
    return list(pcfg.get("labels", ["SN"]))


# -------------------------- path conventions -------------------------
# Workspace layout built by make_workspace.py (same for every phase):
#   <ws>/<phaseN_name>/<rater>/inputs/<sub>/image.nii.gz [+ expert_mask.nii.gz]
#   <ws>/<phaseN_name>/<rater>/outputs/<sub>/   (rater saves masks here)
def phase_dirname(key, pcfg):
    return f"{key}_{pcfg['name']}"


def input_dir(phase_dir, rater, subject):
    return Path(phase_dir) / rater / "inputs" / subject


def output_dir(phase_dir, rater, subject):
    return Path(phase_dir) / rater / "outputs" / subject


def find_subjects(phase_dir, raters):
    """All sub-* directories present under any rater's inputs/ folder."""
    found = set()
    for rater in raters:
        search = Path(phase_dir) / rater / "inputs"
        if search.is_dir():
            found.update(p.name for p in search.iterdir()
                         if p.is_dir() and p.name.startswith("sub-"))
    return sorted(found)


# ---------------------------- source data ----------------------------
# Default file patterns per source layout. A pool selects one with `layout:`
# (default "bids") and may override either pattern explicitly in paths.yaml.
LAYOUT_DEFAULTS = {
    # BIDS-like: per-subject sub-dirs; expert masks under derivatives/.
    "bids": {
        "image_pattern": "{subject}/anat/{subject}_acq-NM_T1w.nii.gz",
        "expert_mask_pattern": "derivatives/expert/{subject}/{subject}_acq-NM_label-SN_mask.nii.gz",
    },
    # Flat: every file directly in data_root.
    "flat": {
        "image_pattern": "{subject}.nii.gz",
        "expert_mask_pattern": "{subject}_SN.nii.gz",
    },
}


def pool_patterns(pdef):
    """(image_pattern, expert_mask_pattern) for a pool: explicit patterns win,
    otherwise the `layout` defaults apply."""
    defaults = LAYOUT_DEFAULTS.get(pdef.get("layout", "bids"), {})
    image = pdef.get("image_pattern", defaults.get("image_pattern"))
    if image is None:
        raise KeyError(f"pool needs image_pattern or a known layout {list(LAYOUT_DEFAULTS)}")
    return image, pdef.get("expert_mask_pattern", defaults.get("expert_mask_pattern"))


def source_paths(pdef, subject):
    """(image, expert_mask or None) source paths for one subject of a pool.
    Patterns are filled with the subject and the pool's own keys (e.g. run).
    With several data roots the first root holding the image is used; if none
    has it, the path under the first root is returned (and reported missing)."""
    roots = pdef["data_root"]
    roots = [roots] if isinstance(roots, str) else roots
    image_pat, expert_pat = pool_patterns(pdef)
    fields = {**pdef, "subject": subject}
    image = image_pat.format(**fields)
    root = next((Path(r) for r in roots if resolve(Path(r) / image)), Path(roots[0]))
    expert = root / expert_pat.format(**fields) if expert_pat else None
    return root / image, expert


# ------------------------------ NIfTI --------------------------------
def resolve(path):
    """Resolve a path that may contain '*' (glob, highest match wins) and/or a
    .nii <-> .nii.gz extension mismatch. Returns a Path or None."""
    s = str(path)
    if "*" in s:
        matches = sorted(glob(s))
        return Path(matches[-1]) if matches else None
    p = Path(s)
    if p.exists():
        return p
    if s.endswith(".nii.gz"):
        alt = Path(s[:-7] + ".nii")
    elif s.endswith(".nii"):
        alt = Path(s + ".gz")
    else:
        return None
    return alt if alt.exists() else None


def load_nii(path):
    """Load a NIfTI as (data, affine, zooms). Tolerant of files whose extension
    misreports compression (detected by magic bytes, not the name)."""
    import gzip
    import nibabel as nib
    path = str(path)
    with open(path, "rb") as f:
        is_gz = f.read(2) == b"\x1f\x8b"
    if is_gz == path.endswith(".gz"):
        img = nib.load(path)                       # extension matches contents
    else:                                          # extension lies -> load by bytes
        raw = open(path, "rb").read()
        raw = gzip.decompress(raw) if is_gz else raw
        try:
            img = nib.Nifti1Image.from_bytes(raw)
        except Exception:
            img = nib.Nifti2Image.from_bytes(raw)
    return img.get_fdata(), img.affine, img.header.get_zooms()[:3]


def load_mask(path):
    """Load a mask as (int16 array, affine, zooms)."""
    import numpy as np
    data, aff, zooms = load_nii(path)
    return data.astype(np.int16), aff, zooms


def dice(a, b):
    """Dice overlap of two boolean arrays. NaN if both are empty."""
    import numpy as np
    a = np.asarray(a, bool)
    b = np.asarray(b, bool)
    denom = int(a.sum()) + int(b.sum())
    if denom == 0:
        return float("nan")
    return 2.0 * int(np.logical_and(a, b).sum()) / denom


def jaccard(a, b):
    """Jaccard (IoU) of two boolean arrays. NaN if both are empty."""
    import numpy as np
    a = np.asarray(a, bool)
    b = np.asarray(b, bool)
    union = int(np.logical_or(a, b).sum())
    if union == 0:
        return float("nan")
    return int(np.logical_and(a, b).sum()) / union


# ------------------------ surface / Hausdorff ------------------------
# Surface-distance metrics in physical units. Spacing is the voxel size
# (zooms) so anisotropic scans (e.g. 0.4x0.4x1.0 mm) give mm distances, not
# voxel counts. scipy is imported lazily to keep the lighter scripts dependency
# free, matching the numpy/nibabel pattern above.
#
# A mask's surface is the set of voxel faces between foreground and background,
# each taken at its centre and weighted by its area. Measuring between voxel
# centres instead puts every surface point half a voxel inside the mask and
# counts a 0.16 mm2 through-plane face like a 0.4 mm2 in-plane one, so on 1 mm
# slices a one-slice difference at either end of the SN pins HD95 at the slice
# thickness.
def _surface_faces(mask, spacing):
    """Boundary faces of a binary mask on the half-voxel lattice (voxel i sits
    at 2i+1, the face between voxels i and i+1 at 2i+2): (face points, area in
    mm2 at each point). The mask must not touch its own edge."""
    import numpy as np
    pts = np.zeros(tuple(2 * n + 1 for n in mask.shape), bool)
    area = np.zeros(pts.shape)
    for ax in range(mask.ndim):
        lo, hi = [slice(None)] * mask.ndim, [slice(None)] * mask.ndim
        at = [slice(1, None, 2)] * mask.ndim
        lo[ax], hi[ax], at[ax] = slice(None, -1), slice(1, None), slice(2, -1, 2)
        face = mask[tuple(lo)] != mask[tuple(hi)]
        pts[tuple(at)] = face
        area[tuple(at)] = face * np.prod(np.delete(spacing, ax))
    return pts, area


def surface_distances(a, b, spacing):
    """Nearest-surface distances (mm) a-surface -> b-surface and b-surface ->
    a-surface, with the area (mm2) of each surface element:
    (d_ab, d_ba, area_a, area_b). All None if either mask is empty."""
    import numpy as np
    from scipy import ndimage
    a = np.asarray(a, bool)
    b = np.asarray(b, bool)
    if not a.any() or not b.any():
        return None, None, None, None
    # Crop to a padded bounding box of the union: both surfaces lie fully inside
    # it, so every surface-to-surface distance is identical to the full-volume
    # result, but the distance transforms run on a small block. The 1-voxel pad
    # keeps every boundary face inside the block.
    idx = np.where(a | b)
    crop = tuple(slice(max(0, i.min() - 1), i.max() + 2) for i in idx)
    a, b = np.pad(a[crop], 1), np.pad(b[crop], 1)
    spacing = np.asarray(spacing, float)
    fa, wa = _surface_faces(a, spacing)
    fb, wb = _surface_faces(b, spacing)
    # EDT of the complement gives, at every lattice point, the distance to the
    # nearest face; sample it at the *other* mask's faces.
    dt_to_b = ndimage.distance_transform_edt(~fb, sampling=spacing / 2)
    dt_to_a = ndimage.distance_transform_edt(~fa, sampling=spacing / 2)
    return dt_to_b[fa], dt_to_a[fb], wa[fa], wb[fb]


def _weighted_percentile(d, w, percentile):
    """Smallest distance that covers `percentile`% of the surface area."""
    import numpy as np
    order = np.argsort(d, kind="stable")
    cum = np.cumsum(w[order]) / w.sum()
    return float(d[order][min(np.searchsorted(cum, percentile / 100), len(d) - 1)])


def surface_distance_metrics(a, b, spacing, percentile=95):
    """Hausdorff and average surface distances between two binary masks,
    area-weighted over the surface faces.

    Returns a dict (all mm): hd (max symmetric Hausdorff), hd95 (robust
    percentile Hausdorff), assd (average symmetric surface distance), masd
    (mean of the two directed mean distances). NaN when either mask is empty.
    """
    import numpy as np
    d_ab, d_ba, w_a, w_b = surface_distances(a, b, spacing)
    if d_ab is None or len(d_ab) == 0 or len(d_ba) == 0:
        return {"hd": float("nan"), "hd95": float("nan"),
                "assd": float("nan"), "masd": float("nan")}
    hd = max(d_ab.max(), d_ba.max())
    hd95 = max(_weighted_percentile(d_ab, w_a, percentile),
               _weighted_percentile(d_ba, w_b, percentile))
    assd = float((d_ab @ w_a + d_ba @ w_b) / (w_a.sum() + w_b.sum()))
    masd = max(np.average(d_ab, weights=w_a), np.average(d_ba, weights=w_b))
    return {"hd": float(hd), "hd95": float(hd95), "assd": assd, "masd": float(masd)}


def per_slice_dice(a, b, axis=2, min_union=1):
    """Per-slice Dice along `axis`. Returns dict {slice_index: dice} over slices
    whose union has >= min_union voxels (slices both empty are skipped)."""
    import numpy as np
    a = np.asarray(a, bool)
    b = np.asarray(b, bool)
    out = {}
    for k in range(a.shape[axis]):
        sa = np.take(a, k, axis=axis)
        sb = np.take(b, k, axis=axis)
        union = int(np.logical_or(sa, sb).sum())
        if union < min_union:
            continue
        denom = int(sa.sum()) + int(sb.sum())
        out[k] = (2.0 * int(np.logical_and(sa, sb).sum()) / denom) if denom else float("nan")
    return out
