#!/usr/bin/env python3
"""Assign subjects to phases and raters, with blinding, never moving existing
work.

Reads the pools (paths.yaml), raters (raters.json) and phase definitions +
seed + ID naming (phases.yaml) from config/, then writes
config/subjects_split.json. Each source scan is relabeled to an anonymized
workspace ID (e.g. sub-0001) so raters never see the source IDs; the
workspace -> source key lives in the "mapping" field and must be kept private.

The split is append-only. When subjects_split.json exists, every phase in it is
kept as is (matched by phase name, so renumbering a phase in phases.yaml keeps
its subjects and IDs) and only phases new to phases.yaml are allocated, with IDs
continuing after the highest existing one. Raters can be added to raters.json
(never removed): they get every shared phase's subjects, and a partitioned
phase must then be re-divided with --repartition, or left for a later division
with --defer. The previous key is backed up next to it before every write.
--regenerate rebuilds everything from the seed instead, which can move subjects
raters already have.

Nothing is hardcoded: raters come from raters.json, subjects from each pool's
list, the seed/ID naming from phases.yaml. A phase takes its subjects one of
two ways:
  n_subjects  a random draw, reproducible from the seed. Pools are drawn down
              in phase order, so drawn phases sharing a pool get disjoint
              subjects.
  subjects    an explicit list of source IDs from the pool. Listed subjects are
              not drawn from the pool (they may also sit in a drawn phase, under
              another workspace ID) and are numbered after every drawn phase,
              so adding such a phase never shifts a draw.

A partitioned phase gives an `overlap` fraction of its subjects to every rater
(hidden repeats, for reliability): its `overlap_subjects` plus a random pick
among subjects whose source image exists. The rest is divided at random, as
evenly as possible, across raters. --repartition redoes this for existing
phases with the same subjects and IDs; a subject a rater already has output
files for (in the workspace) stays with that rater.

    python scripts/split_subjects.py --dry-run      # preview; writes nothing
    python scripts/split_subjects.py                # allocate new phases only
    python scripts/split_subjects.py --repartition phase6 --workspace <ws>
    python scripts/split_subjects.py --defer phase6  # add raters, divide later
    python scripts/split_subjects.py --regenerate   # rebuild all from the seed
"""
import argparse
import json
import random
import re
import shutil
import time
from pathlib import Path

import utils


def allocate(subjects, n):
    """Take the first n subjects (or all if n == 'all'); return (taken, rest).
    Order is preserved (the pool is already shuffled), so taken is random."""
    if n == "all" or n is None:
        return subjects, []
    n = int(n)
    return subjects[:n], subjects[n:]


def partition(order, raters, n_overlap, required=(), eligible=None, locked=None,
              groups=None):
    """Divide a partitioned phase's workspace IDs, in random `order`, across raters.

    The overlap, given to every rater, is `required` plus the first other IDs of
    `order` that are `eligible` (all when None), up to n_overlap; with `groups`
    ({ID: group}) the picks go to the group with the fewest overlap IDs so far,
    so the overlap is split as evenly as possible across groups. Every other ID
    goes to the rater with the fewest so far (ties by rater order, i.e.
    round-robin), taking eligible IDs before the rest and, within each, one
    group after another, so each rater's count of eligible IDs and of each group
    differs by at most one. IDs in `locked` ({ID: raters}) go to exactly those
    raters. Returns (by_rater, overlap), both sorted."""
    locked, groups = locked or {}, groups or {}
    overlap = list(dict.fromkeys(required))
    picks = [w for w in order if w not in overlap and (eligible is None or w in eligible)]
    while len(overlap) < n_overlap and picks:
        if groups:
            count = {g: sum(groups.get(w) == g for w in overlap)
                     for g in {groups.get(w) for w in picks}}
            want = min(count, key=lambda g: (count[g], str(g)))
            wid = next(w for w in picks if groups.get(w) == want)
        else:
            wid = picks[0]
        picks.remove(wid)
        overlap.append(wid)
    by_rater = {r: list(overlap) for r in raters}
    load = dict.fromkeys(raters, 0)
    rest = [w for w in order if w not in overlap]
    for wid in rest:
        for r in locked.get(wid, ()):
            by_rater[r].append(wid)
            load[r] += 1
    rest.sort(key=lambda w: (eligible is not None and w not in eligible,
                             str(groups.get(w, ""))))       # stable: keeps random order
    for wid in rest:
        if wid not in locked:
            r = min(raters, key=load.get)
            by_rater[r].append(wid)
            load[r] += 1
    return {r: sorted(v) for r, v in by_rater.items()}, sorted(overlap)


def load_pools(paths, config_dir):
    """Read each pool's subject list. Returns {pool_name: [subjects]}."""
    return {name: utils.read_subject_list(config_dir / pdef["subjects"])
            for name, pdef in paths["pools"].items()}


def phase_raters(pcfg, raters):
    """Raters of a phase: its `raters` list when set, else every rater."""
    return [r for r in raters if r in pcfg.get("raters", raters)]


def phase_entry(pcfg, pool_name, order, raters, mapping, eligible=None, locked=None,
                info=None):
    """Split entry for one phase from its workspace IDs in random `order`.
    `info` is the pool's subject info (utils.read_subject_info), used when the
    phase sets `balance_by` to one of its columns (e.g. age_group)."""
    raters = phase_raters(pcfg, raters)
    entry = {"name": pcfg["name"], "pool": pool_name,
             "assignment": pcfg["assignment"], "subjects": sorted(order)}
    if pcfg["assignment"] == "partitioned":
        by_source = {mapping[w]: w for w in order}
        required = [by_source[s] for s in pcfg.get("overlap_subjects", [])]
        frac = float(pcfg.get("overlap", 0) or 0)       # a fraction, or a count when >= 1
        n_overlap = max(int(frac) if frac >= 1 else round(frac * len(order)), len(required))
        col = pcfg.get("balance_by")
        groups = {w: (info or {}).get(mapping[w], {}).get(col, "") for w in order} if col else None
        entry["by_rater"], overlap = partition(order, raters, n_overlap, required,
                                               eligible, locked, groups)
        if overlap:
            entry["overlap"] = overlap
    else:                                           # shared: same subjects for all
        entry["by_rater"] = {r: sorted(order) for r in raters}
    return entry


def match_existing(old, phases, keys, error):
    """Map each phase key to the key of the existing split entry with the same
    name. Refuses to drop an existing phase or to reinterpret its subjects."""
    old_by_name = {e["name"]: k for k, e in old["phases"].items()}
    names = {phases[k]["name"] for k in keys}
    dropped = [f"{k}_{e['name']}" for k, e in old["phases"].items() if e["name"] not in names]
    if dropped:
        error(f"phases.yaml no longer defines {', '.join(dropped)}; refusing to drop "
              "their assignments (--regenerate rebuilds the whole split)")
    kept = {}
    for key in keys:
        pcfg = phases[key]
        old_key = old_by_name.get(pcfg["name"])
        if old_key is None:
            continue
        entry = old["phases"][old_key]
        if (pcfg["pool"], pcfg["assignment"]) != (entry["pool"], entry["assignment"]):
            error(f"{key}_{pcfg['name']}: pool/assignment differ from the existing split")
        if "subjects" in pcfg and (sorted(set(pcfg["subjects"])) !=
                                   sorted(old["mapping"][w] for w in entry["subjects"])):
            error(f"{key}_{pcfg['name']}: subjects differ from the existing split")
        kept[key] = old_key
    return kept


def output_locks(workspace, folders, raters, ids):
    """{workspace ID: raters} for the IDs with any file under
    <workspace>/<folder>/<rater>/outputs/<ID>/, in any of `folders`."""
    locked = {}
    for folder in folders:
        for r in raters:
            for wid in ids:
                out = Path(workspace) / folder / r / "outputs" / wid
                if out.is_dir() and any(p.is_file() for p in out.rglob("*")):
                    locked.setdefault(wid, set()).add(r)
    return locked


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config-dir", type=Path, default=utils.CONFIG_DIR)
    ap.add_argument("--seed", type=int, default=None,
                    help="Override the seed from phases.yaml.")
    ap.add_argument("--out", type=Path, default=None,
                    help="Output JSON (default: <config-dir>/subjects_split.json).")
    ap.add_argument("--dry-run", action="store_true", help="Print the split without writing it.")
    ap.add_argument("--repartition", nargs="+", default=[], metavar="PHASE",
                    help="Re-divide these existing partitioned phases across the current raters.")
    ap.add_argument("--defer", nargs="+", default=[], metavar="PHASE",
                    help="Leave these partitioned phases untouched while raters are added; "
                         "the new raters get subjects there at a later --repartition.")
    ap.add_argument("--workspace", type=Path, default=None,
                    help="Workspace checked for existing outputs (default: workspace_root).")
    ap.add_argument("--regenerate", action="store_true",
                    help="Ignore the existing split and rebuild every phase from the seed "
                         "(can move subjects raters already have).")
    args = ap.parse_args()

    paths, phases, raters = utils.load_config(args.config_dir)
    if len(raters) < 2:
        ap.error("Need at least 2 raters in raters.json")
    seed = args.seed if args.seed is not None else int(phases.get("seed", 42))
    prefix = phases.get("id_prefix", "sub-")
    width = int(phases.get("id_width", 4))
    keys = utils.phase_keys(phases)

    split_path = args.config_dir / "subjects_split.json"
    old = None
    if split_path.exists() and not args.regenerate:
        old = utils.load_json(split_path)
        dropped = [r for r in old["raters"] if r not in raters]
        if dropped or old["seed"] != seed:
            ap.error(f"raters.json drops {dropped} or the seed differs from the existing "
                     f"split ({split_path}); restore them, or --regenerate to rebuild it")
    elif split_path.exists():
        print(f"WARNING: --regenerate ignores {split_path}; subjects may move.")
    added = [r for r in raters if old and r not in old["raters"]]

    pools = load_pools(paths, args.config_dir)
    infos = {name: utils.read_subject_info(args.config_dir / pdef["subjects"])
             for name, pdef in paths["pools"].items()}
    seen = {}
    for name, subs in pools.items():
        for s in subs:
            if s in seen:
                ap.error(f"Subject {s} appears in both '{seen[s]}' and '{name}' pools")
            seen[s] = name
    for key in keys:
        pcfg = phases[key]
        if pcfg["pool"] not in pools:
            ap.error(f"{key}: unknown pool '{pcfg['pool']}' (not in paths.yaml pools)")
        if "subjects" in pcfg and "n_subjects" in pcfg:
            ap.error(f"{key}: set n_subjects or subjects, not both")
        stray = [s for s in pcfg.get("overlap_subjects", []) if s not in pools[pcfg["pool"]]]
        if stray:
            ap.error(f"{key}: overlap_subjects {', '.join(stray)} not in pool '{pcfg['pool']}'")
        unknown = [r for r in pcfg.get("raters", []) if r not in raters]
        if unknown:
            ap.error(f"{key}: raters {', '.join(unknown)} are not in raters.json")
        col = pcfg.get("balance_by")
        if col and not any(col in v for v in infos[pcfg["pool"]].values()):
            ap.error(f"{key}: balance_by '{col}' is not a column of pool "
                     f"'{pcfg['pool']}' subjects ({paths['pools'][pcfg['pool']]['subjects']})")

    rng = random.Random(seed)
    remaining = {}
    for name in sorted(pools):                      # stable order -> reproducible
        pool = list(dict.fromkeys(pools[name]))     # de-dup, preserve order
        rng.shuffle(pool)
        remaining[name] = pool

    kept = match_existing(old, phases, keys, ap.error) if old else {}
    for flag, names in (("--repartition", args.repartition), ("--defer", args.defer)):
        for key in names:
            if key not in kept or phases[key]["assignment"] != "partitioned":
                ap.error(f"{flag} {key}: not a partitioned phase of the existing split")
    both = sorted(set(args.repartition) & set(args.defer))
    if both:
        ap.error(f"{', '.join(both)}: cannot be both --repartition and --defer")
    mapping = dict(old["mapping"]) if old else {}   # workspace ID -> source ID
    entries = {}
    for key, old_key in kept.items():
        entry = old["phases"][old_key]
        if added and entry["assignment"] == "shared":
            entry = entry | {"by_rater": entry["by_rater"] |
                             {r: list(entry["subjects"]) for r in added}}
        elif added and key not in args.repartition and key not in args.defer:
            ap.error(f"{key}_{entry['name']} is partitioned: adding raters needs "
                     f"--repartition {key}, or --defer {key} to divide it later")
        entries[key] = entry
        if "subjects" not in phases[key]:           # kept draws leave their pool
            taken = {mapping[w] for w in entry["subjects"]}
            pool_name = phases[key]["pool"]
            remaining[pool_name] = [s for s in remaining[pool_name] if s not in taken]
    counter = 1 + max((int(re.search(r"(\d+)$", w).group(1)) for w in mapping), default=0)

    def eligible(pool_name, ids):
        """IDs whose source image exists (None when no image is found at all)."""
        pdef = paths["pools"][pool_name]
        have = {w for w in ids if utils.resolve(utils.source_paths(pdef, mapping[w])[0])}
        if not have:
            print(f"WARNING: no '{pool_name}' images found; overlap picks ignore availability.")
        return have or None

    locks = {}
    workspace = Path(args.workspace or paths["workspace_root"])
    changes = {}
    for key in args.repartition:
        pcfg, entry = phases[key], entries[key]
        if not workspace.is_dir():
            ap.error(f"--repartition checks the workspace for existing outputs, but "
                     f"{workspace} does not exist; pass --workspace")
        folders = sorted({utils.phase_dirname(key, pcfg), utils.phase_dirname(kept[key], pcfg)})
        locks[key] = output_locks(workspace, folders, old["raters"], entry["subjects"])
        outside = sorted({r for rs in locks[key].values() for r in rs}
                         - set(phase_raters(pcfg, raters)))
        if outside:
            ap.error(f"{key}: {', '.join(outside)} already have outputs here but are not "
                     "among the phase's raters")
        subjects = list(entry["subjects"])
        if pcfg.get("n_subjects") == "all" and "subjects" not in pcfg:
            # "all" follows the pool list: keep the IDs of subjects still in it,
            # give new subjects new IDs, and drop subjects no longer listed
            # (never one with outputs).
            others = {mapping[w] for k, e in entries.items()
                      if k != key and e["pool"] == pcfg["pool"] and "subjects" not in phases[k]
                      for w in e["subjects"]}
            want = [s for s in dict.fromkeys(pools[pcfg["pool"]]) if s not in others]
            have = {mapping[w]: w for w in subjects}
            gone = [w for s, w in have.items() if s not in want]
            stuck = [w for w in gone if w in locks[key]]
            if stuck:
                ap.error(f"{key}: {', '.join(stuck)} left the pool list but have outputs")
            new = [s for s in want if s not in have]
            random.Random(f"{seed}:{pcfg['name']}:added").shuffle(new)   # blind ID order
            ids = [f"{prefix}{i:0{width}d}" for i in range(counter, counter + len(new))]
            counter += len(new)
            mapping.update(zip(ids, new))
            subjects = [w for w in subjects if w not in gone] + ids
            changes[key] = (ids, gone)
        members = {mapping[w] for w in subjects}
        stray = [s for s in pcfg.get("overlap_subjects", []) if s not in members]
        if stray:
            ap.error(f"{key}: overlap_subjects {', '.join(stray)} are not in this phase")
        order = sorted(subjects)
        random.Random(f"{seed}:{pcfg['name']}:repartition").shuffle(order)
        entries[key] = phase_entry(pcfg, pcfg["pool"], order, raters, mapping,
                                   eligible(pcfg["pool"], order), locks[key],
                                   infos[pcfg["pool"]])
        print(f"{key}: checked {', '.join(folders)} under {workspace}; "
              f"{sum(map(len, locks[key].values()))} rater/subject pair(s) with outputs stay put")
        if key in changes:
            ids, gone = changes[key]
            print(f"{key}: {len(ids)} subject(s) new to the pool list get new IDs; "
                  f"{len(gone)} no longer listed are dropped: {', '.join(sorted(gone)) or 'none'}")

    # New drawn phases are numbered before new listed ones (stable sort keeps
    # phase order), so a listed phase never shifts a draw.
    for key in sorted((k for k in keys if k not in kept),
                      key=lambda k: "subjects" in phases[k]):
        pcfg = phases[key]
        pool_name = pcfg["pool"]
        if "subjects" in pcfg:
            taken = list(dict.fromkeys(pcfg["subjects"]))
            unknown = [s for s in taken if s not in pools[pool_name]]
            if unknown:
                ap.error(f"{key}: {', '.join(unknown)} not in pool '{pool_name}'")
            random.Random(f"{seed}:{pcfg['name']}").shuffle(taken)   # blind ID order
        else:
            taken, remaining[pool_name] = allocate(remaining[pool_name], pcfg.get("n_subjects"))
        if not taken:
            ap.error(f"{key}: pool '{pool_name}' is exhausted; add more subjects.")
        stray = [s for s in pcfg.get("overlap_subjects", []) if s not in taken]
        if stray:
            ap.error(f"{key}: overlap_subjects {', '.join(stray)} are not in this phase")

        # Relabel to anonymized workspace IDs (random source order -> blind).
        ids = [f"{prefix}{i:0{width}d}" for i in range(counter, counter + len(taken))]
        counter += len(taken)
        mapping.update(zip(ids, taken))
        partitioned = pcfg["assignment"] == "partitioned"
        entries[key] = phase_entry(pcfg, pool_name, ids, raters, mapping,
                                   eligible(pool_name, ids) if partitioned else None,
                                   info=infos[pool_name])

    split = {"seed": seed, "raters": raters, "mapping": mapping,
             "phases": {key: entries[key] for key in keys}}

    if old:                                         # guard: re-read the key from disk
        before = utils.load_json(split_path)
        moved = [w for w, s in before["mapping"].items() if mapping.get(w) != s]
        for key, old_key in kept.items():
            b, a = before["phases"][old_key], split["phases"][key]
            if key in args.repartition:                 # subjects may follow the pool
                moved += [f"{key} {r}/{w}" for w, rs in locks[key].items()
                          for r in rs if w not in a["by_rater"].get(r, [])]
            elif a["subjects"] != b["subjects"]:
                moved.append(f"{key} subjects")
            elif any(a["by_rater"].get(r) != v for r, v in b["by_rater"].items()):
                moved.append(f"{key} assignments")
        if moved:
            raise SystemExit(f"Refusing to write: existing work would move: {moved}")

    print(f"Split (seed={seed}, raters={raters}):")
    for key, info in split["phases"].items():
        line = (f"  {key}_{info['name']:<16} pool={info['pool']:<7} "
                f"{info['assignment']:<11} {len(info['subjects']):>3} subjects")
        if info["assignment"] == "partitioned":
            sizes = "/".join(str(len(v)) for v in info["by_rater"].values())
            line += f"  (+{len(info.get('overlap', []))} overlap; per-rater {sizes})"
        if key not in kept:
            status = "new"
        else:
            status = "repartitioned" if key in args.repartition else "kept"
            if kept[key] != key:
                status += f", was {kept[key]}"
            if added and info["assignment"] == "shared":
                status += f", +{len(added)} raters"
            elif added and key in args.defer:
                status += f", deferred for {', '.join(added)}"
        print(f"{line}  [{status}]")

    out = args.out or split_path
    if args.dry_run:
        print("Dry run: nothing written.")
    elif old and split == old and out == split_path:
        print(f"No changes; {split_path} left as is.")
    else:
        if out.exists():
            backup = out.with_name(f"{out.stem}.{time.strftime('%Y%m%d-%H%M%S')}.json")
            shutil.copy2(out, backup)
            print(f"Backed up the previous {out.name} to {backup}")
        out.write_text(json.dumps(split, indent=2) + "\n")
        print(f"Wrote {out}")
    for key, old_key in kept.items():
        if key != old_key:
            name = split["phases"][key]["name"]
            print(f"NOTE: {old_key}_{name} is now {key}_{name}; make_workspace.py builds "
                  "the new folder and leaves any old one untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
