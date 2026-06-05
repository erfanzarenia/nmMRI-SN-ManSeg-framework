#!/usr/bin/env python3
"""Compute Dice and volume QC metrics for one phase of the workspace.

For the chosen phase it loads every rater's mask (and the expert mask, when the
phase provides one) and writes tidy CSVs under <qc_output_root>/<phase>/:

    dice_pairwise.csv         rater-vs-rater Dice per subject (inter-rater)
    dice_rater_vs_expert.csv  rater-vs-expert Dice per subject (phases 1-2)
    volumes.csv               SN volume (mm^3) per source per subject

    python scripts/qc_metrics.py --phase phase4

Assumes a single SN label (voxel value 1); override with --label.
"""
import argparse
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

import utils


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", default="phase4")
    ap.add_argument("--config-dir", type=Path, default=utils.CONFIG_DIR)
    ap.add_argument("--workspace", type=Path, default=None,
                    help="Override workspace_root from paths.yaml.")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="Override <qc_output_root>/<phase>.")
    ap.add_argument("--label", type=int, default=1, help="SN label value in the masks.")
    ap.add_argument("--subjects", nargs="+", default=None, help="Restrict to these subjects.")
    args = ap.parse_args()

    paths, phases, raters = utils.load_config(args.config_dir)
    pcfg = phases[args.phase]
    workspace = args.workspace or Path(paths["workspace_root"])
    phase_dir = Path(workspace) / utils.phase_dirname(args.phase, pcfg)
    out_dir = args.out_dir or Path(paths["qc_output_root"]) / args.phase
    out_dir.mkdir(parents=True, exist_ok=True)

    subjects = args.subjects or utils.find_subjects(phase_dir, raters)
    if not subjects:
        print(f"No subjects found under {phase_dir}")
        return 1
    print(f"{args.phase}: {len(subjects)} subjects, raters={raters}")

    rve, pw, vol = [], [], []
    for sub in subjects:
        binaries, zooms = {}, None
        for r in raters:
            mp = utils.resolve(utils.rater_mask_pattern(phase_dir, r, sub, pcfg))
            if mp is None:
                continue
            data, _, zooms = utils.load_mask(mp)
            binaries[r] = (data == args.label)

        expert = None
        if pcfg["expert_mask"]:
            for r in raters:
                ep = utils.resolve(utils.input_dir(phase_dir, r, sub) / "expert_mask.nii.gz")
                if ep is not None:
                    edata, _, ez = utils.load_mask(ep)
                    expert = (edata == args.label)
                    zooms = zooms or ez
                    break

        vox = float(np.prod(zooms)) if zooms else float("nan")
        for r, m in binaries.items():
            vol.append({"subject": sub, "source": r, "vol_mm3": int(m.sum()) * vox})
        if expert is not None:
            vol.append({"subject": sub, "source": "expert", "vol_mm3": int(expert.sum()) * vox})
            for r, m in binaries.items():
                rve.append({"subject": sub, "rater": r, "dice": utils.dice(m, expert)})

        for a, b in combinations(sorted(binaries), 2):
            pw.append({"subject": sub, "rater_a": a, "rater_b": b,
                       "dice": utils.dice(binaries[a], binaries[b])})

    wrote = False
    for name, rows in [("dice_pairwise", pw), ("dice_rater_vs_expert", rve), ("volumes", vol)]:
        if rows:
            path = out_dir / f"{name}.csv"
            pd.DataFrame(rows).to_csv(path, index=False)
            print(f"  wrote {path} ({len(rows)} rows)")
            wrote = True
    if not wrote:
        print("  no overlapping masks found — nothing to compare.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
