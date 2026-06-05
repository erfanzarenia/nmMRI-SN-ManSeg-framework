#!/usr/bin/env python3
"""Build the phase -> rater -> subject workspace from the split + paths config.

Creates inputs/ and outputs/ for every phase and populates each subject's inputs
(image, and expert mask where the phase calls for one) from that pool's
read-only source dataset in config/paths.yaml. Output folders are created empty
for raters to fill in ITK-SNAP. The rater-facing docs are also copied into
<workspace_root>/docs/ so raters have the instructions alongside the data.

    python scripts/make_workspace.py                 # build everything
    python scripts/make_workspace.py --phase phase4  # one phase
    python scripts/make_workspace.py --dry-run       # preview the tree only
    python scripts/make_workspace.py --link          # symlink inputs instead of copy
    python scripts/make_workspace.py --skip-docs     # do not copy the rater docs
"""
import argparse
import shutil
from pathlib import Path

import utils

# Docs copied into <workspace_root>/docs/ for raters. QC and README are
# intentionally excluded (conductor-facing).
RATER_DOCS = [
    "overview.md", "itksnap_instructions.md", "SN_label_definition.txt",
    "phase1.md", "phase2.md", "phase3.md", "phase4.md", "phase5.md",
]


def source_paths(paths, pool, subject):
    """(image_src, expert_src_or_None) for a subject in the given pool."""
    pdef = paths["pools"][pool]
    root = Path(pdef["data_root"])
    image = root / pdef["image_pattern"].format(subject=subject)
    expert = None
    if "expert_mask_pattern" in pdef:
        expert = root / pdef["expert_mask_pattern"].format(subject=subject)
    return image, expert


def copy_docs(docs_dir, workspace, dry_run):
    """Copy the rater-facing docs into <workspace>/docs/. Returns the count."""
    dest = Path(workspace) / "docs"
    docs_dir = Path(docs_dir)
    n = 0
    for name in RATER_DOCS:
        src = docs_dir / name
        if not src.exists():
            continue
        if dry_run:
            print(f"    copy {src}  ->  {dest / name}")
        else:
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / name)
        n += 1
    return n


def place(src, dst_dir, stem, link, dry_run, log):
    """Copy or symlink one source volume into dst_dir as <stem>.<ext>."""
    if dry_run:
        log.append(f"    {'link' if link else 'copy'}  {src}  ->  {dst_dir}/{stem}.nii.gz")
        return True
    resolved = utils.resolve(src)
    if resolved is None:
        log.append(f"    MISSING source: {src}")
        return False
    ext = ".nii.gz" if str(resolved).endswith(".gz") else ".nii"
    dst = Path(dst_dir) / f"{stem}{ext}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    if link:
        dst.symlink_to(resolved.resolve())
    else:
        shutil.copy2(resolved, dst)
    return True


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config-dir", type=Path, default=utils.CONFIG_DIR)
    ap.add_argument("--split", type=Path, default=None,
                    help="subjects_split.json (default: <config-dir>/subjects_split.json).")
    ap.add_argument("--phase", default=None, help="Build only this phase, e.g. phase4.")
    ap.add_argument("--link", action="store_true", help="Symlink inputs instead of copying.")
    ap.add_argument("--dry-run", action="store_true", help="Print actions without writing.")
    ap.add_argument("--docs-dir", type=Path, default=utils.REPO_ROOT / "docs",
                    help="Source docs copied into <workspace>/docs/ for raters.")
    ap.add_argument("--skip-docs", action="store_true",
                    help="Do not copy the rater docs into the workspace.")
    args = ap.parse_args()

    paths, phases, _ = utils.load_config(args.config_dir)
    split = utils.load_json(args.split or args.config_dir / "subjects_split.json")
    mapping = split["mapping"]                       # workspace ID -> source ID
    workspace = Path(paths["workspace_root"])
    keys = [args.phase] if args.phase else list(split["phases"])

    placed = missing = 0
    for key in keys:
        pcfg = phases[key]
        info = split["phases"][key]
        pool = pcfg["pool"]
        phase_dir = workspace / utils.phase_dirname(key, pcfg)
        inputs = "image + expert_mask" if pcfg["expert_mask"] else "image"
        print(f"\n== {phase_dir} ==")
        print(f"   pool={pool}, {info['assignment']}, inputs: {inputs}, "
              f"attempts: {pcfg['attempts']}")
        for rater, subs in info["by_rater"].items():
            for sub in subs:                         # sub is an anonymized workspace ID
                log = []
                image_src, expert_src = source_paths(paths, pool, mapping[sub])
                in_dir = utils.input_dir(phase_dir, rater, sub)
                ok = place(image_src, in_dir, "image", args.link, args.dry_run, log)
                if pcfg["expert_mask"]:
                    if expert_src is None:
                        log.append(f"    expert_mask requested but pool '{pool}' "
                                   "defines no expert_mask_pattern")
                    else:
                        place(expert_src, in_dir, "expert_mask", args.link, args.dry_run, log)
                out_dir = utils.output_dir(phase_dir, rater, sub)
                if args.dry_run:
                    log.append(f"    mkdir {out_dir}")
                else:
                    out_dir.mkdir(parents=True, exist_ok=True)
                placed += ok
                missing += (not ok)
                if log:
                    print(f"  [{rater}] {sub}")
                    print("\n".join(log))

    if not args.skip_docs:
        print(f"\n== {workspace / 'docs'} ==")
        n = copy_docs(args.docs_dir, workspace, args.dry_run)
        print(f"   {'would copy' if args.dry_run else 'copied'} {n} rater doc(s).")

    verb = "would place" if args.dry_run else "placed"
    print(f"\nDone. {verb} {placed} subject image(s)" +
          (f"; {missing} missing source(s)." if missing else "."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
