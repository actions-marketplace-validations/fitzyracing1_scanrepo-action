# Getting started

## In a release workflow

```yaml
on:
  release:
    types: [published]
permissions:
  contents: write
jobs:
  print:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0          # edition = commit count
      - uses: fitzyracing1/scanrepo-action@v1
        with:
          exclude: "*.lock, dist/**"
```

The release gets `<repo>-ed<edition>-<commit>.scanrepo` and a `.pdf`.

## Locally

```sh
pip install -r requirements.txt
python3 -m scanrepo build --repo . --out-dir out
```

## Restore

```sh
pip install -r requirements-scan.txt         # plus libzbar0 and poppler-utils
python3 scanrepo-restore.py photos/ out/     # page photos, payload .txt files, or the PDF
python3 scanrepo-restore.py sample.scanrepo  # or the digital master
```
