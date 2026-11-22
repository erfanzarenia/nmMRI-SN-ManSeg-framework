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

--rebalance is the gentler option for a phase in progress: it keeps the overlap
and every assignment, tops the overlap up to its new size from subjects nobody
has started, and hands over only as many unstarted subjects as are needed to even
out the raters. Output files show finished work but not a scan open and unsaved,
so --hold RATER:ID pins a subject known to be in progress and --hold-next N pins
each rater's next N subjects. Everything held counts as started, in both modes.

--add-new is the gentlest: for a partitioned phase taking `n_subjects: all`, it
gives subjects new to the pool list new IDs (after the highest existing one) and
hands each to the rater with the fewest own subjects, then the fewest of its
age group (`balance_by`). Every existing ID, assignment and the overlap stay
exactly as they are.

    python scripts/split_subjects.py --dry-run      # preview; writes nothing
    python scripts/split_subjects.py                # allocate new phases only
    python scripts/split_subjects.py --repartition phase6 --workspace <ws>
    python scripts/split_subjects.py --rebalance phase6 --hold-next 5 --hold R1:sub-0076
    python scripts/split_subjects.py --add-new phase6  # new pool subjects only
    python scripts/split_subjects.py --defer phase6  # add raters, divide later
    python scripts/split_subjects.py --regenerate   # rebuild all from the seed
"""
import argparse
import json
import random
import re
import shutil
import time
from collections import Counter
from pathlib import Path

import utils


def allocate(subjects, n):
    """Take the first n subjects (or all if n == 'all'); return (taken, rest).
    Order is preserved (the pool is already shuffled), so taken is random."""
    if n == "all" or n is None:
        return subjects, []
    n = int(n)
    return subjects[:n], subjects[n:]


def top_up_overlap(overlap, picks, n_overlap, groups):
    """Add IDs from `picks` to `overlap` (in place) until it has n_overlap; with
    `groups` each one goes to the group with the fewest overlap IDs so far."""
    picks = list(picks)
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
    top_up_overlap(overlap, [w for w in order if w not in overlap
                             and (eligible is None or w in eligible)], n_overlap, groups)
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
    # Keep the overlap hidden: no rater's first scan (lowest ID) may be an
    # overlap subject, or every rater would open the same scan first. Such an
    # ID trades places with a non-overlap ID of the same group (and
    # eligibility) from the middle of the ID range; loads stay the same.
    owner = {w: r for r, v in by_rater.items() for w in v if w not in overlap}
    ranked = sorted(order)
    for _ in range(len(order)):
        first = next((min(v) for v in by_rater.values()
                      if min(v) in overlap and min(v) not in required), None)
        if first is None:
            break
        mates = [w for w in ranked[len(ranked) // 3: 2 * len(ranked) // 3]
                 if w in owner and w not in locked
                 and groups.get(w) == groups.get(first)
                 and (eligible is None or w in eligible)]
        if not mates:
            break
        swap = mates[len(mates) // 2]
        q = owner.pop(swap)
        overlap[overlap.index(first)] = swap
        owner[first] = q
        for r in raters:
            if r != q:
                by_rater[r].remove(first)
                by_rater[r].append(swap)
    return {r: sorted(v) for r, v in by_rater.items()}, sorted(overlap)


def rebalance(current, overlap, raters, n_overlap, order, eligible=None, locked=None,
              groups=None, required=()):
    """Even out a partitioned phase in progress, moving as little as possible.

    Every current assignment stays. The overlap grows to n_overlap from eligible
    IDs not in `locked` ({ID: raters}), so nobody loses a started subject to it
    and each picked ID simply also goes to every rater. A larger overlap shrinks
    to n_overlap instead: it drops the highest IDs (raters reach them last) that
    nobody has started and that are not `required`, from the group with the most
    overlap IDs each time, and each goes on to one rater only, the one with the
    fewest subjects (then the fewest of its group). Then
    unlocked non-overlap IDs pass from the most to the least loaded rater until
    loads differ by at most one, taking the kind of ID (group and eligibility)
    the giver has most of relative to the taker, in random `order`, so raters
    stay balanced on those too. Returns (by_rater, overlap), both sorted."""
    locked, groups = locked or {}, groups or {}
    overlap = list(overlap)
    top_up_overlap(overlap, [w for w in order if w not in overlap and w not in locked
                             and (eligible is None or w in eligible)], n_overlap, groups)
    dropped = []
    while len(overlap) > n_overlap:
        free = [w for w in overlap if w not in locked and w not in required]
        if not free:
            print("WARNING: every overlap subject is started or required; it stays larger.")
            break
        count = Counter(groups.get(w, "") for w in overlap)
        wid = max(free, key=lambda w: (count[groups.get(w, "")], w))
        overlap.remove(wid)
        dropped.append(wid)
    by_rater = {r: {w for w in current.get(r, ()) if w not in overlap} for r in raters}
    for r in raters:
        by_rater[r] -= set(dropped)
    for wid in dropped:
        g = groups.get(wid, "")
        r = min(raters, key=lambda r: (len(by_rater[r]),
                                       sum(groups.get(w, "") == g for w in by_rater[r])))
        by_rater[r].add(wid)

    def kind(w):
        return groups.get(w, ""), eligible is None or w in eligible

    rank = {w: i for i, w in enumerate(order)}
    while True:
        give = max(raters, key=lambda r: len(by_rater[r]))
        take = min(raters, key=lambda r: len(by_rater[r]))
        if len(by_rater[give]) - len(by_rater[take]) <= 1:
            break
        free = sorted((w for w in by_rater[give] if w not in locked), key=rank.get)
        if not free:
            print(f"WARNING: {give} has only started subjects left; loads stay uneven.")
            break
        have, want = ([kind(w) for w in by_rater[r]] for r in (give, take))
        wid = max(free, key=lambda w: have.count(kind(w)) - want.count(kind(w)))
        by_rater[give].remove(wid)
        by_rater[take].add(wid)
    return {r: sorted(by_rater[r] | set(overlap)) for r in raters}, sorted(overlap)


def add_new(by_rater, overlap, raters, new, groups=None):
    """Hand each ID of `new` (in its random order, one group after another) to
    the rater with the fewest non-overlap IDs, ties going to the one with the
    fewest of that ID's group, then by rater order. Existing IDs never move.
    Returns by_rater with every list sorted."""
    groups = groups or {}
    by_rater = {r: list(by_rater.get(r, [])) for r in raters}
    own = {r: [w for w in v if w not in overlap] for r, v in by_rater.items()}
    for wid in sorted(new, key=lambda w: str(groups.get(w, ""))):   # stable: keeps order
        g = groups.get(wid, "")
        r = min(raters, key=lambda r: (len(own[r]),
                                       sum(groups.get(w, "") == g for w in own[r])))
        own[r].append(wid)
        by_rater[r].append(wid)
    return {r: sorted(v) for r, v in by_rater.items()}


def next_up(by_rater, overlap, started, n):
    """{ID: raters} for each rater's next n non-overlap IDs in ID order (raters
    work up from their lowest ID) that are not in `started`."""
    held = {}
    for r, ids in by_rater.items():
        for w in [w for w in sorted(ids) if w not in overlap
                  and r not in started.get(w, ())][:n]:
            held.setdefault(w, set()).add(r)
    return held


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
    ap.add_argument("--rebalance", nargs="+", default=[], metavar="PHASE",
                    help="Even out these partitioned phases across their raters with as few "
                         "moves as possible, keeping the overlap and growing it to `overlap`.")
    ap.add_argument("--add-new", nargs="+", default=[], metavar="PHASE",
                    help="Give subjects new to the pool list new IDs in these partitioned "
                         "phases and add them to the least loaded raters; nothing else moves.")
    ap.add_argument("--hold", nargs="+", default=[], metavar="RATER:ID",
                    help="With --repartition or --rebalance: subjects a rater has started "
                         "without any output file yet; they stay with that rater.")
    ap.add_argument("--hold-next", type=int, default=0, metavar="N",
                    help="With --repartition or --rebalance: also keep each rater's next N "
                         "unstarted subjects with them, in case one is open and unsaved.")
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
    for flag, names in (("--repartition", args.repartition), ("--rebalance", args.rebalance),
                        ("--defer", args.defer), ("--add-new", args.add_new)):
        for key in names:
            if key not in kept or phases[key]["assignment"] != "partitioned":
                ap.error(f"{flag} {key}: not a partitioned phase of the existing split")
    redivide = list(args.repartition) + list(args.rebalance)
    both = sorted((set(args.repartition) & set(args.rebalance)) |
                  ((set(redivide) | set(args.add_new)) & set(args.defer)) |
                  (set(redivide) & set(args.add_new)))
    if both:
        ap.error(f"{', '.join(both)}: pick one of --repartition, --rebalance, --add-new "
                 "and --defer")
    if (args.hold or args.hold_next) and not redivide:
        ap.error("--hold and --hold-next only apply with --repartition or --rebalance")
    mapping = dict(old["mapping"]) if old else {}   # workspace ID -> source ID
    entries = {}
    for key, old_key in kept.items():
        entry = old["phases"][old_key]
        if added and entry["assignment"] == "shared":
            entry = entry | {"by_rater": entry["by_rater"] |
                             {r: list(entry["subjects"]) for r in added}}
        elif added and key not in redivide and key not in args.defer:
            ap.error(f"{key}_{entry['name']} is partitioned: adding raters needs "
                     f"--repartition {key}, --rebalance {key}, or --defer {key} to "
                     "divide it later")
        entries[key] = entry
        if "subjects" not in phases[key]:           # kept draws leave their pool
            taken = {mapping[w] for w in entry["subjects"]}
            pool_name = phases[key]["pool"]
            remaining[pool_name] = [s for s in remaining[pool_name] if s not in taken]
    counter = 1 + max((int(re.search(r"(\d+)$", w).group(1)) for w in mapping), default=0)

    def eligible(pool_name, ids, runs=None):
        """IDs whose source image exists, for each of `runs` when given (None when
        no image is found at all)."""
        pdef = paths["pools"][pool_name]
        have = {w for w in ids if all(utils.resolve(utils.source_paths(pdef, mapping[w], run)[0])
                                      for run in runs or [None])}
        if not have:
            print(f"WARNING: no '{pool_name}' images found; overlap picks ignore availability.")
        return have or None

    for key in args.add_new:
        pcfg, entry = phases[key], entries[key]
        if pcfg.get("n_subjects") != "all" or "subjects" in pcfg:
            ap.error(f"--add-new {key}: only for a phase with n_subjects: all")
        others = {mapping[w] for k, e in entries.items()
                  if k != key and e["pool"] == pcfg["pool"] and "subjects" not in phases[k]
                  for w in e["subjects"]}
        have = {mapping[w] for w in entry["subjects"]}
        new = [s for s in dict.fromkeys(pools[pcfg["pool"]]) if s not in have | others]
        random.Random(f"{seed}:{pcfg['name']}:added").shuffle(new)   # blind ID order
        ids = [f"{prefix}{i:0{width}d}" for i in range(counter, counter + len(new))]
        counter += len(new)
        mapping.update(zip(ids, new))
        col = pcfg.get("balance_by")
        info = infos[pcfg["pool"]]
        groups = ({w: info.get(mapping[w], {}).get(col, "") for w in entry["subjects"] + ids}
                  if col else None)
        overlap = entry.get("overlap", [])
        by_rater = add_new(entry["by_rater"], overlap, phase_raters(pcfg, raters), ids, groups)
        entries[key] = entry | {"subjects": sorted(entry["subjects"] + ids),
                                "by_rater": by_rater}
        print(f"{key}: {len(ids)} subject(s) new to the pool list get new IDs "
              f"{', '.join(ids) or '(none)'}; nothing else changes")
        for r, v in by_rater.items():
            add = [w for w in v if w in ids]
            print(f"  {r}: +{len(add)} -> {len(v)} subjects ({len(v) - len(overlap)} own)"
                  + (f", {dict(Counter(groups[w] for w in add))}" if groups and add else ""))

    locks = {}
    workspace = Path(args.workspace or paths["workspace_root"])
    changes = {}
    for key in redivide:
        pcfg, entry = phases[key], entries[key]
        if not workspace.is_dir():
            ap.error(f"{key}: dividing a phase checks the workspace for existing outputs, "
                     f"but {workspace} does not exist; pass --workspace")
        folders = sorted({utils.phase_dirname(key, pcfg), utils.phase_dirname(kept[key], pcfg)})
        locks[key] = output_locks(workspace, folders, old["raters"], entry["subjects"])
        outside = sorted({r for rs in locks[key].values() for r in rs}
                         - set(phase_raters(pcfg, raters)))
        if outside:
            ap.error(f"{key}: {', '.join(outside)} already have outputs here but are not "
                     "among the phase's raters")
        n_outputs = sum(map(len, locks[key].values()))
        for hold in args.hold:
            r, _, wid = hold.partition(":")
            if wid not in entry["by_rater"].get(r, []):
                ap.error(f"--hold {hold}: {wid} is not assigned to {r} in {key}")
            locks[key].setdefault(wid, set()).add(r)
        for wid, rs in next_up(entry["by_rater"], entry.get("overlap", []), locks[key],
                               args.hold_next).items():
            locks[key].setdefault(wid, set()).update(rs)
        if key in args.rebalance:
            pool_name = pcfg["pool"]
            if not set(entry["subjects"]) <= {w for w, s in mapping.items()
                                              if s in pools[pool_name]}:
                ap.error(f"{key}: subjects left the pool list; use --repartition")
            members = {mapping[w]: w for w in entry["subjects"]}
            stray = [s for s in pcfg.get("overlap_subjects", []) if s not in members]
            if stray:
                ap.error(f"{key}: overlap_subjects {', '.join(stray)} are not in this phase")
            order = sorted(entry["subjects"])
            random.Random(f"{seed}:{pcfg['name']}:rebalance").shuffle(order)
            frac = float(pcfg.get("overlap", 0) or 0)
            n_overlap = int(frac) if frac >= 1 else round(frac * len(order))
            overlap = list(dict.fromkeys(list(entry.get("overlap", [])) +
                                         [members[s] for s in pcfg.get("overlap_subjects", [])]))
            col = pcfg.get("balance_by")
            info = infos[pool_name]
            groups = {w: info.get(mapping[w], {}).get(col, "") for w in order} if col else None
            by_rater, overlap = rebalance(entry["by_rater"], overlap, phase_raters(pcfg, raters),
                                          n_overlap, order,
                                          eligible(pool_name, order, utils.phase_runs(pcfg)),
                                          locks[key], groups,
                                          [members[s] for s in pcfg.get("overlap_subjects", [])])
            entries[key] = entry | {"by_rater": by_rater, "overlap": overlap}
            print(f"{key}: checked {', '.join(folders)} under {workspace}; {n_outputs} "
                  f"rater/subject pair(s) with outputs and "
                  f"{sum(map(len, locks[key].values())) - n_outputs} held stay put")
            for r in by_rater:
                was, now = set(entry["by_rater"].get(r, [])), set(by_rater[r])
                print(f"  {r}: {len(was)} -> {len(now)} subjects "
                      f"(+{len(now - was)}, -{len(was - now)})")
            continue
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
            if key in redivide:                         # subjects may follow the pool
                moved += [f"{key} {r}/{w}" for w, rs in locks[key].items()
                          for r in rs if w not in a["by_rater"].get(r, [])]
                if key in args.rebalance:                   # a dropped overlap ID stays with one rater
                    moved += [f"{key} subject {w}" for w in b["subjects"]
                              if not any(w in v for v in a["by_rater"].values())]
            elif key in args.add_new:                   # only additions
                moved += [f"{key} subject {w}" for w in b["subjects"] if w not in a["subjects"]]
                moved += [f"{key} overlap" for _ in [0] if a.get("overlap") != b.get("overlap")]
                moved += [f"{key} {r}/{w}" for r, v in b["by_rater"].items() for w in v
                          if w not in a["by_rater"].get(r, [])]
                moved += [f"{key} {r}/{w}" for r, v in a["by_rater"].items() for w in v
                          if w in b["subjects"] and w not in b["by_rater"].get(r, [])]
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
            status = ("repartitioned" if key in args.repartition else
                      "rebalanced" if key in args.rebalance else
                      "subjects added" if key in args.add_new else "kept")
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
