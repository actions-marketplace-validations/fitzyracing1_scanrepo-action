# Usage

## Action inputs (`action.yml`)
`ref` (HEAD), `path` (.), `edition` (commit count), `branch`, `repo-name`, `output-dir` (scanrepo-out), `max-file-size` (102400; 0 = no limit), `include`, `exclude`, `page-size` (letter or a4), `attach-to-release` (true), `token` (github.token).

Outputs: `scanrepo-path`, `pdf-path`, `commit`, `edition`, `pages`, `omitted`. On events other than a release, the action only writes the files. A shallow checkout needs an explicit `edition`.

## CLI (`python3 -m scanrepo`)
| Command | Options |
|---|---|
| `build` | common options + `--out-dir`, `--page-size letter\|a4`, `--font TTF`, `--no-pdf`, `--github-output` |
| `generate -o FILE` | write the `.scanrepo` master only |
| `render MASTER -o PDF` | `--page-size`, `--font` |
| `restore` | same as `scanrepo-restore.py` (`--strict` fails on a 1.0 master) |

Common options: `--repo .`, `--ref HEAD`, `--edition`, `--branch`, `--name`, `--max-file-size 102400`, `--include`, `--exclude`, `--chunk-size 1800`.

## Limits (README)
Binary files, oversized files, excluded files and submodules stay off paper but are listed by id. A page holds about 1800 bytes. Restoring has been tested with zbar on a rasterized PDF, but not yet with phone photos of paper.
