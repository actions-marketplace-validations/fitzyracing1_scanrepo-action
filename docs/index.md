# scanrepo-action

This repo holds the GitHub Action and Python tools for the **scanrepo** format: print a commit as a readable PDF booklet whose QR codes scan back to the same blob, tree and commit ids. The format started in [summit](https://github.com/fitzyracing1/summit).

- Format: `.scanrepo`, MIME `application/x-scanrepo`, version 1 (format 1.1 adds optional keys)
- Action: `fitzyracing1/scanrepo-action@v1` (composite, `action.yml`)
- Python deps: `reportlab`, `segno` (`requirements.txt`). For scanning: `pyzbar`, `pillow`, plus `libzbar0` and `poppler-utils` (`requirements-scan.txt`).

Pages: [Getting started](getting-started.md) · [Usage](usage.md) · [Architecture](architecture.md) · [Contributing](contributing.md)

The repo has no CI workflow. Tests run locally (`tests/e2e.sh`).

MIT License. Copyright (c) 2026 Joshua Almeida
