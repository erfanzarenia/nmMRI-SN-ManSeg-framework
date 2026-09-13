#!/usr/bin/env python3
"""Build the phase -> rater -> subject workspace from the split + paths config.

Creates inputs/ and outputs/ for every phase and populates each subject's inputs
(image, and expert mask where the phase calls for one) from that pool's
read-only source dataset in config/paths.yaml. The source may be BIDS-style or
flat, with .nii or .nii.gz files; every input is normalized to a gzip-compressed
.nii.gz in the workspace. Output folders are created empty for raters to fill in
ITK-SNAP, and the rater-facing docs are copied into <workspace_root>/docs/
alongside the data.

    python scripts/make_workspace.py                 # build everything
    python scripts/make_workspace.py --phase phase4  # one phase
    python scripts/make_workspace.py --dry-run       # preview the tree only
    python scripts/make_workspace.py --link          # symlink when source is .nii.gz
    python scripts/make_workspace.py --skip-docs     # do not copy the rater docs
"""
import argparse
import gzip
import shutil
from pathlib import Path

import utils

# Docs copied into <workspace_root>/docs/ for raters. QC and README are
# intentionally excluded (conductor-facing).
RATER_DOCS = [
    "overview.md", "segmentation_protocol.md", "itksnap_instructions.md",
    "SN_label_definition.txt",
    "phase1.md", "phase2.md", "phase3.md", "phase4.md", "phase5.md", "phase6.md",
]


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
    """Materialize one source volume into dst_dir as <stem>.nii.gz, always
    gzip-compressed regardless of whether the source is .nii or .nii.gz (and
    regardless of a misleading extension; compression is judged by content).
    With --link, symlink instead, but only when the source is already
    gzip-compressed; otherwise it is converted."""
    dst = Path(dst_dir) / f"{stem}.nii.gz"
    if dry_run:
        log.append(f"    {'link/convert' if link else 'convert'}  {src}  ->  {dst}")
        return True
    resolved = utils.resolve(src)
    if resolved is None:
        log.append(f"    MISSING source: {src}")
        return False
    with open(resolved, "rb") as f:
        is_gz = f.read(2) == b"\x1f\x8b"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    if link and is_gz:
        dst.symlink_to(resolved.resolve())
    elif is_gz:
        shutil.copyfile(resolved, dst)                  # already .nii.gz content
    else:                                               # .nii content -> compress
        with open(resolved, "rb") as fi, gzip.open(dst, "wb") as fo:
            shutil.copyfileobj(fi, fo)
    return True


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config-dir", type=Path, default=utils.CONFIG_DIR)
    ap.add_argument("--split", type=Path, default=None,
                    help="subjects_split.json (default: <config-dir>/subjects_split.json).")
    ap.add_argument("--phase", default=None, help="Build only this phase, e.g. phase4.")
    ap.add_argument("--link", action="store_true",
                    help="Symlink inputs when the source is already .nii.gz; otherwise convert.")
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
        pdef = paths["pools"][pool]
        phase_dir = workspace / utils.phase_dirname(key, pcfg)
        inputs = "image + expert_mask" if pcfg["expert_mask"] else "image"
        run = f" (run {pdef['run']})" if "run" in pdef else ""
        print(f"\n== {phase_dir} ==")
        print(f"   pool={pool}{run}, {info['assignment']}, inputs: {inputs}, "
              f"attempts: {pcfg['attempts']}, labels: {'+'.join(utils.phase_labels(pcfg))}")
        for rater, subs in info["by_rater"].items():
            for sub in subs:                         # sub is an anonymized workspace ID
                log = []
                image_src, expert_src = utils.source_paths(pdef, mapping[sub])
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
