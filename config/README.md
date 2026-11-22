# Config

The files here are examples showing the format. Your own config goes in
`config/local/`, which git ignores:

```bash
mkdir -p config/local && cp config/*.yaml config/*.json config/subjects_* config/local/
```

The scripts read `config/local/` whenever it has a `paths.yaml`, and `config/`
otherwise. `MANSEG_CONFIG_DIR=<dir>` or `--config-dir <dir>` points them elsewhere.

| File | What it holds |
|---|---|
| `paths.yaml` | data pools (dataset roots, layout, file patterns), workspace and QC locations |
| `phases.yaml` | phases, split seed, workspace ID naming, per-phase raters and criteria |
| `raters.json` | rater initials |
| `subjects_labeled.txt` | subjects with expert masks (phases 1-4) |
| `subjects_study.csv` | study subjects with their age group (phases 5-6) |

The scripts also write private files next to the config: `subjects_split.json`
(the workspace to source ID key, with timestamped backups) and the `<phase>_*.csv`
manifests. Never share them with raters.
