# KeyRecs Dataset

Source: https://zenodo.org/records/7886743

Dataset title: `KeyRecs: Keystroke Dynamics Dataset`

License: Creative Commons Attribution 4.0 International.

Raw files are intentionally ignored by Git:

- `raw/fixed-text.csv`
- `raw/free-text.csv`
- `raw/demographics.csv`

Downloaded file checksums:

| File | Rows | MD5 |
| --- | ---: | --- |
| `fixed-text.csv` | 19,772 | `f3c2ef3a42625f4df7183ea36d4543db` |
| `free-text.csv` | 562,583 | `a5ca6fcb0970cfdcd8eb958b3fe9f22a` |
| `demographics.csv` | 99 | `da9be3c6c007c4b4a43008351b095e10` |

The fixed-text file uses precomputed digraph timings in seconds. Run:

```bash
python scripts/import-keyrecs.py
```

This writes `processed/fixed-text.features.json` in the same feature JSON
shape used by the current Cadence trainer. The inferred fixed-text sequence is
`vpwjkeurkb` with length `10`.

`train.py` now defaults to training on both:

- `datasets/cmu/features.json`
- `datasets/keyrecs/processed/fixed-text.features.json`

Example experiment command:

```bash
python train.py \
  --model-path models/cadence_cmu_keyrecs.keras \
  --metrics-path models/cadence_cmu_keyrecs.metrics.json
```

When multiple feature files are loaded, `train.py` namespaces user IDs by
dataset and creates positive/negative pairs within each dataset. That avoids
making validation artificially easy by comparing CMU samples against KeyRecs
samples as negatives.

Free-text data is downloaded but not converted yet. It is a digraph-level file,
not row-per-password-attempt, so we should decide separately whether to sample
sliding windows from it for training.
