#!/usr/bin/env python3
"""Build the phase -> rater -> subject workspace from the split + paths config.

Creates inputs/ and outputs/ for every phase and populates each subject's inputs
(image, and expert mask where the phase calls for one) from that pool's
read-only source dataset in config/paths.yaml. The source may be BIDS-style or
flat, with .nii or .nii.gz files; every input is normalized to a gzip-compressed
.nii.gz in the workspace. Output folders are created empty for raters to fill in
ITK-SNAP, and the rater-facing docs are copied into <workspace_root>/docs/
alongside the data, each from <config-dir>/docs/ when the group keeps its own
version there. Inputs are always copies, never links: a link would show raters
the source path, and with it the source ID.

A phase with `runs` in phases.yaml gets one image per acquisition,
image_run_<N>.nii.gz; a run the subject does not have is simply left out. With
`echoes` too, each run gets one image per echo label, image_run_<N>_<echo>.nii.gz
(e.g. _combo and _echo), and an echo image only for a run that has the first
one. Masks are never labeled or renamed here:
QC uses a mask only for the run and echo label in its name.

Existing work is never touched: an input already in place is kept (--overwrite
replaces it), folders are only ever created, and --raters limits the build to
some raters, e.g. when new raters join a workspace that is already in use. After
a --rebalance, --archive-unassigned moves the folders of subjects a rater no
longer has to <workspace_root>/archive/; it refuses any with an output file.

    python scripts/make_workspace.py                         # build everything
    python scripts/make_workspace.py --phase phase5 phase6   # some phases
    python scripts/make_workspace.py --raters R1 R2          # some raters
    python scripts/make_workspace.py --workspace <path>      # not workspace_root
    python scripts/make_workspace.py --dry-run               # preview only
    python scripts/make_workspace.py --phase phase6 --archive-unassigned
    python scripts/make_workspace.py --skip-docs             # do not copy the rater docs
"""
import argparse
import gzip
import shutil
import time
from collections import Counter
from pathlib import Path

import utils

# Docs copied into <workspace_root>/docs/ for raters. QC and README are
# intentionally excluded (conductor-facing).
RATER_DOCS = [
    "overview.md", "segmentation_protocol.md", "itksnap_instructions.md",
    "SN_label_definition.txt",
    "phase1.md", "phase2.md", "phase3.md", "phase4.md", "phase5.md", "phase6.md",
]


def copy_docs(docs_dir, workspace, dry_run, own_dir=None):
    """Copy the rater-facing docs into <workspace>/docs/, each from `own_dir` (a
    group's own versions, e.g. config/local/docs/) when it has that file.
    Returns (docs copied, how many of them came from own_dir)."""
    dest = Path(workspace) / "docs"
    n = own = 0
    for name in RATER_DOCS:
        mine = Path(own_dir) / name if own_dir else None
        src = mine if mine is not None and mine.is_file() else Path(docs_dir) / name
        if not src.exists():
            continue
        if dry_run:
            print(f"    copy {src}  ->  {dest / name}")
        else:
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / name)
        n += 1
        own += src == mine
    return n, own


def place(src, dst_dir, stem, overwrite, dry_run, log):
    """Copy one source volume into dst_dir as <stem>.nii.gz, always
    gzip-compressed regardless of whether the source is .nii or .nii.gz (and
    regardless of a misleading extension; compression is judged by content).
    A file already in place is kept unless `overwrite`. Returns "placed", "kept"
    or "missing"."""
    dst = Path(dst_dir) / f"{stem}.nii.gz"
    if (dst.exists() or dst.is_symlink()) and not overwrite:
        return "kept"
    resolved = utils.resolve(src)
    if resolved is None:
        log.append(f"    MISSING source: {src}")
        return "missing"
    if dry_run:
        log.append(f"    copy  {resolved}  ->  {dst}")
        return "placed"
    with open(resolved, "rb") as f:
        is_gz = f.read(2) == b"\x1f\x8b"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    if is_gz:
        shutil.copyfile(resolved, dst)                  # already .nii.gz content
    else:                                               # .nii content -> compress
        with open(resolved, "rb") as fi, gzip.open(dst, "wb") as fo:
            shutil.copyfileobj(fi, fo)
    return "placed"


def place_run_image(pdef, source, in_dir, run, echo, overwrite, dry_run, log):
    """Copy one run's image (of one echo label). A run with no source file is
    expected for some subjects, so it is "absent", not "missing"."""
    src = utils.source_paths(pdef, source, run, echo)[0]
    if utils.resolve(src) is None:
        return "absent"
    return place(src, in_dir, utils.image_name(run, echo), overwrite, dry_run, log)


def archive_unassigned(workspace, phase_dir, info, dry_run):
    """Move each rater's subject folders that the split no longer gives them to
    <workspace>/archive/<phase>_<timestamp>/. Any folder with an output file stays
    and is reported. Returns (moved subjects, refused subjects)."""
    dest = Path(workspace) / "archive" / f"{Path(phase_dir).name}_{time.strftime('%Y%m%d-%H%M%S')}"
    moved, refused = [], []
    for rater, subs in info["by_rater"].items():
        for inputs in sorted((Path(phase_dir) / rater / "inputs").glob("sub-*")):
            sub = inputs.name
            if sub in subs:
                continue
            outputs = utils.output_dir(phase_dir, rater, sub)
            if any(p.is_file() or p.is_symlink() for p in outputs.rglob("*")):
                refused.append(f"{rater}/{sub}")
                continue
            moved.append(f"{rater}/{sub}")
            if dry_run:
                continue
            for folder, kind in ((inputs, "inputs"), (outputs, "outputs")):
                if folder.is_dir():
                    (dest / rater / kind).mkdir(parents=True, exist_ok=True)
                    shutil.move(str(folder), str(dest / rater / kind / sub))
    return moved, refused


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config-dir", type=Path, default=utils.CONFIG_DIR)
    ap.add_argument("--split", type=Path, default=None,
                    help="subjects_split.json (default: <config-dir>/subjects_split.json).")
    ap.add_argument("--workspace", type=Path, default=None,
                    help="Override workspace_root from paths.yaml.")
    ap.add_argument("--phase", nargs="+", default=None,
                    help="Build only these phases, e.g. phase5 phase6.")
    ap.add_argument("--raters", nargs="+", default=None,
                    help="Build only these raters' folders (default: every rater in the split).")
    ap.add_argument("--overwrite", action="store_true",
                    help="Replace inputs that are already in place (default: keep them).")
    ap.add_argument("--archive-unassigned", action="store_true",
                    help="First move folders of subjects a rater no longer has to "
                         "<workspace>/archive/ (never one with an output file).")
    ap.add_argument("--dry-run", action="store_true", help="Print actions without writing.")
    ap.add_argument("--docs-dir", type=Path, default=utils.REPO_ROOT / "docs",
                    help="Source docs copied into <workspace>/docs/ for raters "
                         "(a file in <config-dir>/docs/ is copied instead).")
    ap.add_argument("--skip-docs", action="store_true",
                    help="Do not copy the rater docs into the workspace.")
    args = ap.parse_args()

    paths, phases, _ = utils.load_config(args.config_dir)
    split = utils.load_json(args.split or args.config_dir / "subjects_split.json")
    mapping = split["mapping"]                       # workspace ID -> source ID
    workspace = Path(args.workspace or paths["workspace_root"])
    keys = args.phase or list(split["phases"])
    unknown = ([k for k in keys if k not in split["phases"]] +
               [r for r in args.raters or [] if r not in split["raters"]])
    if unknown:
        ap.error(f"not in the split: {', '.join(unknown)}")

    counts = Counter()
    for key in keys:
        pcfg = phases[key]
        info = split["phases"][key]
        pool = pcfg["pool"]
        pdef = paths["pools"][pool]
        phase_dir = workspace / utils.phase_dirname(key, pcfg)
        inputs = "image + expert_mask" if pcfg["expert_mask"] else "image"
        runs = utils.phase_runs(pcfg)
        echoes = utils.phase_echoes(pcfg) if runs else None
        run = (f" (runs {', '.join(map(str, runs))})" if runs else
               f" (run {pdef['run']})" if "run" in pdef else "")
        print(f"\n== {phase_dir} ==")
        if echoes:
            run += f" x echoes {', '.join(echoes)}"
        print(f"   pool={pool}{run}, {info['assignment']}, inputs: {inputs}, "
              f"attempts: {pcfg['attempts']}, labels: {'+'.join(utils.phase_labels(pcfg))}")
        if args.archive_unassigned:
            moved, refused = archive_unassigned(workspace, phase_dir, info, args.dry_run)
            print(f"   {'would archive' if args.dry_run else 'archived'} {len(moved)} "
                  f"reassigned folder(s): {', '.join(moved) or 'none'}")
            if refused:
                print(f"   KEPT, these hold output files: {', '.join(refused)}")
        for rater, subs in info["by_rater"].items():
            if args.raters and rater not in args.raters:
                continue
            for sub in subs:                         # sub is an anonymized workspace ID
                log = []
                image_src, expert_src = utils.source_paths(pdef, mapping[sub])
                in_dir = utils.input_dir(phase_dir, rater, sub)
                if runs:
                    if not args.dry_run:
                        in_dir.mkdir(parents=True, exist_ok=True)
                    for r in utils.phase_runs(pcfg, mapping[sub]):
                        for i, e in enumerate(echoes or [None]):
                            got = place_run_image(pdef, mapping[sub], in_dir, r, e,
                                                  args.overwrite, args.dry_run, log)
                            counts[got] += 1
                            if got == "absent":       # no echo image without the first
                                counts["absent"] += len(echoes or [None]) - i - 1
                                break
                else:
                    counts[place(image_src, in_dir, "image", args.overwrite,
                                 args.dry_run, log)] += 1
                if pcfg["expert_mask"]:
                    if expert_src is None:
                        log.append(f"    expert_mask requested but pool '{pool}' "
                                   "defines no expert_mask_pattern")
                    else:
                        place(expert_src, in_dir, "expert_mask", args.overwrite,
                              args.dry_run, log)
                out_dir = utils.output_dir(phase_dir, rater, sub)
                if not out_dir.is_dir():
                    if args.dry_run:
                        log.append(f"    mkdir {out_dir}")
                    else:
                        out_dir.mkdir(parents=True, exist_ok=True)
                if log:
                    print(f"  [{rater}] {sub}")
                    print("\n".join(log))

    if not args.skip_docs:
        print(f"\n== {workspace / 'docs'} ==")
        own_dir = Path(args.config_dir) / "docs"
        n, own = copy_docs(args.docs_dir, workspace, args.dry_run, own_dir)
        print(f"   {'would copy' if args.dry_run else 'copied'} {n} rater doc(s)"
              + (f", {own} of them the group's own from {own_dir}." if own else "."))

    verb = "would place" if args.dry_run else "placed"
    print(f"\nDone. {verb} {counts['placed']} subject image(s); kept {counts['kept']} "
          f"already in place; {counts['missing']} missing source(s).")
    if counts["absent"]:
        print(f"Runs: {counts['absent']} run(s) the subject does not have.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
