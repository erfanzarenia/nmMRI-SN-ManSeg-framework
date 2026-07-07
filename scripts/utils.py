"""Shared helpers for the SN manual-segmentation scripts.

Kept deliberately small: config loading, the phase -> rater -> subject path
conventions, and NIfTI/Dice helpers. The on-disk layout has a single source of
truth here, so make_workspace.py and qc_metrics.py never disagree about it.

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


def rater_mask_pattern(phase_dir, rater, subject, pcfg):
    """Expected rater output mask; a glob when the phase has multiple attempts."""
    out = output_dir(phase_dir, rater, subject)
    if int(pcfg.get("attempts", 1)) > 1:
        return out / f"{rater}_mask_a*.nii.gz"   # latest attempt wins
    return out / f"{rater}_mask.nii.gz"


def find_subjects(phase_dir, raters):
    """All sub-* directories present under any rater's inputs/ folder."""
    found = set()
    for rater in raters:
        search = Path(phase_dir) / rater / "inputs"
        if search.is_dir():
            found.update(p.name for p in search.iterdir()
                         if p.is_dir() and p.name.startswith("sub-"))
    return sorted(found)


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
def surface_mask(mask):
    """Boundary voxels of a binary mask: foreground voxels touching background
    (6-connectivity). Empty in -> empty out."""
    import numpy as np
    from scipy import ndimage
    mask = np.asarray(mask, bool)
    if not mask.any():
        return mask
    eroded = ndimage.binary_erosion(mask, border_value=0)
    return mask & ~eroded


def surface_distances(a, b, spacing):
    """Two arrays of nearest-surface distances (mm): a-surface -> b-surface and
    b-surface -> a-surface. Returns (None, None) if either mask is empty."""
    import numpy as np
    from scipy import ndimage
    a = np.asarray(a, bool)
    b = np.asarray(b, bool)
    if not a.any() or not b.any():
        return None, None
    # Crop to a padded bounding box of the union: both surfaces lie fully inside
    # it, so every surface-to-surface distance is identical to the full-volume
    # result, but the distance transforms run on ~1e5 voxels instead of ~1e7.
    # The >=2-voxel pad keeps mask voxels off the crop edge so binary_erosion
    # (border_value=0) does not invent boundary surface voxels.
    idx = np.where(a | b)
    crop = tuple(slice(max(0, i.min() - 2), i.max() + 3) for i in idx)
    a, b = a[crop], b[crop]
    sa, sb = surface_mask(a), surface_mask(b)
    # EDT of the complement gives, at every voxel, the distance to the nearest
    # surface voxel; sample it at the *other* surface.
    dt_to_b = ndimage.distance_transform_edt(~sb, sampling=spacing)
    dt_to_a = ndimage.distance_transform_edt(~sa, sampling=spacing)
    return dt_to_b[sa], dt_to_a[sb]


def surface_distance_metrics(a, b, spacing, percentile=95):
    """Hausdorff and average surface distances between two binary masks.

    Returns a dict (all mm): hd (max symmetric Hausdorff), hd95 (robust
    percentile Hausdorff), assd (average symmetric surface distance), masd
    (mean of the two directed mean distances). NaN when either mask is empty.
    """
    import numpy as np
    d_ab, d_ba = surface_distances(a, b, spacing)
    if d_ab is None or len(d_ab) == 0 or len(d_ba) == 0:
        return {"hd": float("nan"), "hd95": float("nan"),
                "assd": float("nan"), "masd": float("nan")}
    hd = max(d_ab.max(), d_ba.max())
    hd95 = max(np.percentile(d_ab, percentile), np.percentile(d_ba, percentile))
    assd = float(np.concatenate([d_ab, d_ba]).mean())
    masd = max(d_ab.mean(), d_ba.mean())
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
