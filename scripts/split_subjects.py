#!/usr/bin/env python3
"""Assign subjects to phases and raters, with blinding, never moving an
existing assignment.

Reads the pools (paths.yaml), raters (raters.json) and phase definitions +
seed + ID naming (phases.yaml) from config/, then writes
config/subjects_split.json. Each source scan is relabeled to an anonymized
workspace ID (e.g. sub-0001) so raters never see the source IDs; the
workspace -> source key lives in the "mapping" field and must be kept private.

The split is append-only. When subjects_split.json exists, every phase in it is
kept exactly as is -- matched by phase name, so renumbering a phase in
phases.yaml keeps its subjects and IDs -- and only phases new to phases.yaml are
allocated, with IDs continuing after the highest existing one. --regenerate
rebuilds everything from the seed instead, which can move subjects raters
already have.

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

For a partitioned phase, an `overlap` fraction of its subjects is given to every
rater (hidden repeats) so reliability can be tracked on the production set.

    python scripts/split_subjects.py --dry-run      # preview; writes nothing
    python scripts/split_subjects.py                # allocate new phases only
    python scripts/split_subjects.py --regenerate   # rebuild all from the seed
"""
import argparse
import json
import random
import re
from pathlib import Path

import utils


def allocate(subjects, n):
    """Take the first n subjects (or all if n == 'all'); return (taken, rest).
    Order is preserved (the pool is already shuffled), so taken is random."""
    if n == "all" or n is None:
        return subjects, []
    n = int(n)
    return subjects[:n], subjects[n:]


def partition(subjects, raters):
    """Divide subjects across raters as evenly as possible (round-robin)."""
    by_rater = {r: [] for r in raters}
    for i, sub in enumerate(subjects):
        by_rater[raters[i % len(raters)]].append(sub)
    return by_rater


def load_pools(paths, config_dir):
    """Read each pool's subject list. Returns {pool_name: [subjects]}."""
    return {name: utils.read_subject_list(config_dir / pdef["subjects"])
            for name, pdef in paths["pools"].items()}


def phase_entry(pcfg, pool_name, ids, raters):
    """Split entry for one phase from its workspace IDs (in draw order)."""
    entry = {"name": pcfg["name"], "pool": pool_name,
             "assignment": pcfg["assignment"], "subjects": sorted(ids)}
    if pcfg["assignment"] == "partitioned":
        frac = float(pcfg.get("overlap", 0) or 0)
        n_overlap = round(frac * len(ids))
        overlap, rest = ids[:n_overlap], ids[n_overlap:]
        part = partition(rest, raters)
        entry["by_rater"] = {r: sorted(part[r] + overlap) for r in raters}
        if overlap:
            entry["overlap"] = sorted(overlap)
    else:                                           # shared: same subjects for all
        entry["by_rater"] = {r: sorted(ids) for r in raters}
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


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config-dir", type=Path, default=utils.CONFIG_DIR)
    ap.add_argument("--seed", type=int, default=None,
                    help="Override the seed from phases.yaml.")
    ap.add_argument("--out", type=Path, default=None,
                    help="Output JSON (default: <config-dir>/subjects_split.json).")
    ap.add_argument("--dry-run", action="store_true", help="Print the split without writing it.")
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
        if old["raters"] != raters or old["seed"] != seed:
            ap.error(f"raters/seed differ from the existing split ({split_path}); "
                     "restore them, or --regenerate to rebuild the whole split")
    elif split_path.exists():
        print(f"WARNING: --regenerate ignores {split_path}; subjects may move.")

    pools = load_pools(paths, args.config_dir)
    seen = {}
    for name, subs in pools.items():
        for s in subs:
            if s in seen:
                ap.error(f"Subject {s} appears in both '{seen[s]}' and '{name}' pools")
            seen[s] = name
    for key in keys:
        if phases[key]["pool"] not in pools:
            ap.error(f"{key}: unknown pool '{phases[key]['pool']}' (not in paths.yaml pools)")
        if "subjects" in phases[key] and "n_subjects" in phases[key]:
            ap.error(f"{key}: set n_subjects or subjects, not both")

    rng = random.Random(seed)
    remaining = {}
    for name in sorted(pools):                      # stable order -> reproducible
        pool = list(dict.fromkeys(pools[name]))     # de-dup, preserve order
        rng.shuffle(pool)
        remaining[name] = pool

    kept = match_existing(old, phases, keys, ap.error) if old else {}
    mapping = dict(old["mapping"]) if old else {}   # workspace ID -> source ID
    entries = {key: old["phases"][old_key] for key, old_key in kept.items()}
    for key in kept:                                # kept draws leave their pool
        if "subjects" not in phases[key]:
            taken = {mapping[w] for w in entries[key]["subjects"]}
            pool_name = phases[key]["pool"]
            remaining[pool_name] = [s for s in remaining[pool_name] if s not in taken]
    counter = 1 + max((int(re.search(r"(\d+)$", w).group(1)) for w in mapping), default=0)

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

        # Relabel to anonymized workspace IDs (random source order -> blind).
        ids = [f"{prefix}{i:0{width}d}" for i in range(counter, counter + len(taken))]
        counter += len(taken)
        mapping.update(zip(ids, taken))
        entries[key] = phase_entry(pcfg, pool_name, ids, raters)

    split = {"seed": seed, "raters": raters, "mapping": mapping,
             "phases": {key: entries[key] for key in keys}}

    if old:                                         # guard: re-read the key from disk
        before = utils.load_json(split_path)
        moved = [f"{k}_{before['phases'][ok]['name']}" for k, ok in kept.items()
                 if split["phases"][k] != before["phases"][ok]]
        moved += [w for w, s in before["mapping"].items() if mapping.get(w) != s]
        if moved:
            raise SystemExit(f"Refusing to write: existing assignments would change: {moved}")

    print(f"Split (seed={seed}, raters={raters}):")
    for key, info in split["phases"].items():
        line = (f"  {key}_{info['name']:<16} pool={info['pool']:<7} "
                f"{info['assignment']:<11} {len(info['subjects']):>3} subjects")
        if "overlap" in info:
            sizes = "/".join(str(len(v)) for v in info["by_rater"].values())
            line += f"  (+{len(info['overlap'])} overlap; per-rater {sizes})"
        status = ("new" if key not in kept else
                  "kept" if kept[key] == key else f"kept, was {kept[key]}")
        print(f"{line}  [{status}]")

    out = args.out or split_path
    if args.dry_run:
        print("Dry run: nothing written.")
    elif old and split == old and out == split_path:
        print(f"No new phases; {split_path} unchanged.")
    else:
        out.write_text(json.dumps(split, indent=2) + "\n")
        print(f"Wrote {out}")
    for key, old_key in kept.items():
        if key != old_key:
            name = split["phases"][key]["name"]
            print(f"NOTE: {old_key}_{name} is now {key}_{name}: rename its workspace "
                  "folder to match (subjects and IDs are unchanged).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
