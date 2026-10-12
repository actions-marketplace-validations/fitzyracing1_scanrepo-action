# Architecture

| Path | Role |
|---|---|
| `scanrepo/core.py` | git object hashing (blob, tree, commit), shared helpers |
| `scanrepo/generate.py` | builds the `.scanrepo` master from a commit |
| `scanrepo/render.py` | PDF booklet with QR codes (reportlab, segno) |
| `scanrepo/restore.py`, `scanrepo-restore.py` | decode QR payloads (cover `C\|`, page `P\|`), rejoin chunks, verify ids, rebuild a shallow git repo |
| `scanrepo/__main__.py` | CLI |
| `scanrepo.kind` | format declaration including the QR payload format |
| `action.yml` | composite action |
| `tests/e2e.sh`, `tests/make_fixture.sh`, `tests/act/` | end-to-end generate → print → scan → restore, and a local `act` run |
