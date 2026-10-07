# scanrepo

Physical copy of a git repo. Print it. Read it. Scan it back.

GitHub Action and tools for the `scanrepo` format, which started in [fitzyracing1/summit](https://github.com/fitzyracing1/summit).

Kind: `scanrepo`  
Extension: `.scanrepo`  
MIME: `application/x-scanrepo`  
Version: 1 (format 1.1, optional keys)

Files:

- `scanrepo.kind` — format declaration, including the QR payload format
- `sample.scanrepo` — digital master of a two-file repo (format 1.0)
- `scanrepo-restore.py` — checks blob, tree and commit ids and rebuilds a git repo from the master, page photos, or the PDF
- `scanrepo/` — generator, PDF renderer, restorer
- `action.yml` — the GitHub Action
- `tests/` — end-to-end test (generate, print, scan, restore) and a local `act` run of the action

## What a booklet is

One edition is one commit. The cover carries repo name, edition, branch, commit id, tree id, and parent. Each later page is one file (or one chunk of a file) in monospace, plus a QR code that holds the same bytes.

A new printing does not overwrite the old one. Bump edition, set `parent` to the previous commit, print again. The shelf is the history.

## Action

Print every release and attach the booklet to it:

```yaml
name: print
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

The release gets `<repo>-ed<edition>-<commit>.scanrepo` and `.pdf`.

Inputs: `ref` (HEAD), `path` (.), `edition` (commit count), `branch`, `repo-name`, `output-dir` (scanrepo-out), `max-file-size` (102400), `include`, `exclude`, `page-size` (letter or a4), `attach-to-release` (true), `token` (github.token).

Outputs: `scanrepo-path`, `pdf-path`, `commit`, `edition`, `pages`, `omitted`.

On other events it only writes the files. Upload them with `actions/upload-artifact` if you want them. A shallow checkout needs an explicit `edition`.

Locally:

```sh
pip install -r requirements.txt
python3 -m scanrepo build --repo . --out-dir out
```

## Scan path

1. Photograph every page, cover included. Do not crop the codes.
2. Decode each QR. Cover payload starts with `C|`. Page payload starts with `P|`.
3. Rejoin chunks, check each blob with git's `blob <len>\0` SHA-1.
4. Rebuild the tree (names sorted, git style) and the commit. Reject the booklet if either id does not match the cover.

The restorer does all four:

```sh
pip install -r requirements-scan.txt         # plus libzbar0 and poppler-utils
python3 scanrepo-restore.py photos/ out/     # page photos, payload .txt files, or the PDF
python3 scanrepo-restore.py sample.scanrepo  # or the digital master
```

`out/` is a git repo whose HEAD is the printed commit. Parents are not on paper, so it is a shallow repo. Add `--strict` to fail on a 1.0 master, which has no fields to rebuild the commit.

A full page code holds about 2 KB (QR version 38, error correction M). Print at 100% and shoot each code flat and sharp.

## Limits

This is a snapshot format, not a packfile. Deltas and binary assets are out. A 50 KB text repo is a thin booklet. A photo-heavy repo is the wrong thing to print.

- Binary files (NUL byte or not UTF-8), files over `max-file-size`, excluded files and submodules stay off paper. The cover lists them by id, so the tree and commit still verify, but the restored checkout lacks them.
- About 1800 bytes per page. 100 KB of source is about 60 pages.
- Paths must be UTF-8 without newlines.
- The PDF uses a Unicode monospace font when one is installed (DejaVu, Liberation, Noto). Otherwise Courier prints `?` for characters outside Latin-1. The codes are not affected.
- Tested by rasterizing the PDF and decoding with zbar, not yet with phone photos of paper.

## License

MIT. See `LICENSE`.
