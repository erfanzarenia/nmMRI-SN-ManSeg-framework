#!/usr/bin/env python3
"""Write and check the private manifests of a partitioned, multi-run phase.

Writes, next to the split key (git-ignored; they hold source IDs, never share
them with raters):
  config/<phase>_assignments.csv   per subject: raters, overlap, runs, who started
  config/<phase>_acquisitions.csv  per scan (run, and echo label with `echoes`):
                                   source file and its checksum
  config/<phase>_output_labels.csv per mask labeled run 1: the file it points to

Then checks the workspace against the split, reads only, and exits non-zero on a
problem: every subject assigned, the overlap with every rater, loads balanced,
each rater's folders matching their assignment, every acquisition in place and
byte-identical to its own source file, no scan duplicated across subjects or runs,
and no mask outside its rater's assignment.

    python scripts/phase_manifest.py phase6
    python scripts/phase_manifest.py phase6 --check-only
"""
import argparse
import csv
import hashlib
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import utils


def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_csv(path, rows, columns):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, columns)
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase", help="A partitioned phase with `runs`, e.g. phase6.")
    ap.add_argument("--config-dir", type=Path, default=utils.CONFIG_DIR)
    ap.add_argument("--workspace", type=Path, default=None,
                    help="Override workspace_root from paths.yaml.")
    ap.add_argument("--check-only", action="store_true", help="Check, writing no manifest.")
    args = ap.parse_args()

    paths, phases, _ = utils.load_config(args.config_dir)
    split = utils.load_json(args.config_dir / "subjects_split.json")
    pcfg, entry = phases[args.phase], split["phases"][args.phase]
    runs = utils.phase_runs(pcfg)
    if not runs or entry["assignment"] != "partitioned":
        ap.error(f"{args.phase} is not a partitioned phase with `runs` in phases.yaml")
    pdef = paths["pools"][pcfg["pool"]]
    info = utils.read_subject_info(args.config_dir / pdef["subjects"])
    mapping, by_rater = split["mapping"], entry["by_rater"]
    overlap = set(entry.get("overlap", []))
    subjects = entry["subjects"]
    phase_dir = Path(args.workspace or paths["workspace_root"]) / utils.phase_dirname(args.phase, pcfg)
    problems = []

    # Assignment.
    assigned = Counter(w for v in by_rater.values() for w in v)
    problems += [f"{w} is assigned to nobody" for w in subjects if not assigned[w]]
    problems += [f"{w}, not in the overlap, has {assigned[w]} raters" for w in subjects
                 if w not in overlap and assigned[w] > 1]
    problems += [f"overlap {w} is not with {r}" for w in overlap for r, v in by_rater.items()
                 if w not in v]
    loads = {r: len(v) for r, v in by_rater.items()}
    if max(loads.values()) - min(loads.values()) > 1:
        problems.append(f"loads differ by more than one: {loads}")

    # Sources: which file is each run (and echo image) of each subject. An echo
    # image is only used for a run that has the first one.
    echoes = utils.phase_echoes(pcfg) or [None]
    sources = {(w, run, e): utils.resolve(utils.source_paths(pdef, mapping[w], run, e)[0])
               for w in subjects for run in utils.phase_runs(pcfg, mapping[w]) for e in echoes}
    sources = {(w, run, e): p for (w, run, e), p in sources.items()
               if p is not None and sources[(w, run, echoes[0])] is not None}
    with ThreadPoolExecutor(8) as ex:
        checksum = dict(zip(sources, ex.map(sha1, sources.values())))
    same = Counter(checksum.values())
    problems += [f"{k} has the same source bytes as another scan" for k, h in checksum.items()
                 if same[h] > 1]
    problems += [f"{w} has no run 1 source" for w in subjects
                 if (w, 1, echoes[0]) not in sources]
    problems += [f"{w}: run {run} has no {e} image" for w in subjects
                 for run in utils.phase_runs(pcfg, mapping[w]) for e in echoes[1:]
                 if (w, run, echoes[0]) in sources and (w, run, e) not in sources]
    runs_of = {w: [r for r in runs if (w, r, echoes[0]) in sources] for w in subjects}

    # Workspace folders against the split; every image against its own source.
    def in_place(job):
        rater, w, run, e = job
        what = f"run {run}" + (f" {e}" if e else "")
        path = utils.input_dir(phase_dir, rater, w) / f"{utils.image_name(run, e)}.nii.gz"
        if not path.exists():
            return f"{rater}/{w}: {what} image missing"
        if sha1(path) != checksum[(w, run, e)]:
            return f"{rater}/{w}: {what} image differs from its source"
        return None

    problems += [f"{r}/{p.parent.name}: image without a run label"
                 for r in by_rater for p in (phase_dir / r / "inputs").glob("sub-*/image.nii.gz")]
    problems += [f"{r}/{w}: {p.name} is not one of its images"
                 for r, v in by_rater.items() for w in v
                 for p in utils.input_dir(phase_dir, r, w).glob("image_run_*.nii.gz")
                 if p.name not in {f"{utils.image_name(run, e)}.nii.gz" for run in runs_of[w]
                                   for e in echoes if (w, run, e) in sources}]

    jobs = [(r, w, run, e) for r, v in by_rater.items() for w in v for run in runs_of[w]
            for e in echoes if (w, run, e) in sources]
    with ThreadPoolExecutor(8) as ex:
        problems += [p for p in ex.map(in_place, jobs) if p]
    labels, started = [], {}
    for r, v in by_rater.items():
        inputs = phase_dir / r / "inputs"
        have = {p.name for p in inputs.glob("sub-*")}
        problems += [f"{r}/{w}: folder not in the assignment" for w in sorted(have - set(v))]
        problems += [f"{r}/{w}: folder missing" for w in sorted(set(v) - have)]
        for sub in sorted((phase_dir / r / "outputs").glob("sub-*")):
            masks = [p for p in sub.iterdir() if p.is_file()]
            if not masks:
                continue
            started.setdefault(sub.name, []).append(r)
            if sub.name not in v:
                problems.append(f"{r}/{sub.name}: outputs but not assigned")
            link = sub / f"{r}_mask_run_1.nii.gz"
            label_names = {link.name, f"{r}_mask_run_1_{echoes[0]}.nii.gz"}
            for p in masks:
                if p.is_symlink() and p.name in label_names:
                    labels.append({"workspace_id": sub.name, "rater": r, "run": 1,
                                   "label": p.name, "mask": p.readlink().name,
                                   "sha1": sha1(p)})
            if not any((sub / n).exists() for n in label_names) and not any(
                    re.search(r"_run[_-]?\d", p.name) for p in masks):
                problems.append(f"{r}/{sub.name}: mask not labeled with a run")

    # Manifests.
    if not args.check_only:
        rows = [{"workspace_id": w, "participant": mapping[w],
                 "age_group": info.get(mapping[w], {}).get("age_group", ""),
                 "raters": " ".join(r for r, v in by_rater.items() if w in v),
                 "overlap": "yes" if w in overlap else "",
                 "n_runs": len(runs_of[w]), "runs": " ".join(map(str, runs_of[w])),
                 "started_by": " ".join(started.get(w, [])),
                 "note": info.get(mapping[w], {}).get("note", "") or ""}
                for w in sorted(subjects, key=lambda w: (w not in overlap, mapping[w]))]
        write_csv(args.config_dir / f"{args.phase}_assignments.csv", rows, list(rows[0]))
        acq = [{"workspace_id": w, "participant": mapping[w], "run": f"run_{run}",
                "echo": e or "", "source": sources[(w, run, e)], "sha1": checksum[(w, run, e)]}
               for (w, run, e) in sorted(sources, key=lambda k: (k[0], k[1], str(k[2])))]
        write_csv(args.config_dir / f"{args.phase}_acquisitions.csv", acq, list(acq[0]))
        write_csv(args.config_dir / f"{args.phase}_output_labels.csv", labels,
                  ["workspace_id", "rater", "run", "label", "mask", "sha1"])

    # Summary.
    n_group = Counter(info.get(mapping[w], {}).get("age_group", "") for w in overlap)
    print(f"{args.phase}: {len(subjects)} subjects, {sum(map(len, runs_of.values()))} scans "
          f"({len(sources)} images) "
          f"({Counter(map(len, runs_of.values()))} subjects by run count)")
    print(f"overlap: {len(overlap)} ({100 * len(overlap) / len(subjects):.1f}%), {dict(n_group)}: "
          f"{', '.join(sorted(overlap))}")
    print(f"{'rater':<6}{'subjects':>9}{'overlap':>9}{'own':>6}{'scans':>7}{'started':>9}")
    for r, v in by_rater.items():
        print(f"{r:<6}{len(v):>9}{len(overlap & set(v)):>9}{len(set(v) - overlap):>6}"
              f"{sum(len(runs_of[w]) for w in v):>7}{sum(r in s for s in started.values()):>9}")
    print(f"masks labeled run 1: {len(labels)}")
    for n in sorted(set(map(len, runs_of.values())) - {2}):
        print(f"subjects with {n} run(s): {', '.join(w for w in subjects if len(runs_of[w]) == n)}")
    for p in problems:
        print(f"PROBLEM: {p}")
    print(f"{len(problems)} problem(s).")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
