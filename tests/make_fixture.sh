#!/usr/bin/env bash
# Build a small git repo that exercises the awkward cases.
#   tests/make_fixture.sh /tmp/fixture
set -euo pipefail
dest=${1:?usage: make_fixture.sh DEST}
rm -rf "$dest"
mkdir -p "$dest"
cd "$dest"
git init -q -b main
git config user.name "Fixture Bot"
git config user.email "fixture@example.com"
export GIT_AUTHOR_DATE="2026-01-02T03:04:05+0100" GIT_COMMITTER_DATE="2026-01-02T03:04:05+0100"

mkdir -p src/pkg/deep docs a
printf 'first\n' > README.md
git add . && git commit -q -m "first edition"

# nested dirs, a file well over 1800 chars, a single line over 1800 bytes
python3 - <<'PY'
import pathlib
body = "".join(f"def f{i}(x):\n    return x * {i}  # line {i}\n\n" for i in range(120))
pathlib.Path("src/pkg/deep/long.py").write_text(body)
pathlib.Path("src/pkg/oneline.min.js").write_text("var a=" + "1+" * 1500 + "1;\n")
pathlib.Path("src/pkg/__init__.py").write_text("")
pathlib.Path("docs/unicode.md").write_text("Grüße · naïve café — 日本語 ✓\n\tTabbed line\n")
pathlib.Path("docs/crlf.txt").write_bytes(b"windows\r\nline endings\r\n")
pathlib.Path("docs/no-newline.txt").write_bytes(b"no newline at end")
pathlib.Path("docs/tricky.scanrepo").write_text("## PAGE 1\ntext:\n## END PAGE\n## END PAGE")
pathlib.Path("a.b").write_text("sorts before a/\n")
pathlib.Path("a/x").write_text("inside a/\n")
pathlib.Path("a0").write_text("sorts after a/\n")
pathlib.Path("logo.bin").write_bytes(bytes(range(256)) * 4)
pathlib.Path("big.txt").write_text("x" * 200000 + "\n")
pathlib.Path("secret.env").write_text("TOKEN=not-printed\n")
PY
printf '#!/bin/sh\necho hi\n' > run.sh && chmod +x run.sh
ln -s src/pkg/deep/long.py link-to-long.py
git add .
# a submodule gitlink (id only)
git update-index --add --cacheinfo 160000,1111111111111111111111111111111111111111,vendor/lib
git commit -q -m "second edition: nested dirs

Body line one.
Body line two with ünïcödé."

# a commit object with an extra signed-style header, written by hand
tree=$(git rev-parse HEAD^{tree})
parent=$(git rev-parse HEAD)
signed=$(printf 'tree %s\nparent %s\nauthor Fixture Bot <fixture@example.com> 1767319445 +0100\ncommitter Fixture Bot <fixture@example.com> 1767319445 +0100\ngpgsig -----BEGIN PGP SIGNATURE-----\n \n iQEzBAABCAAdFiEEfakefakefakefakefake\n =abcd\n -----END PGP SIGNATURE-----\n\nsigned-style third commit\n' "$tree" "$parent" | git hash-object -t commit -w --stdin)
git update-ref refs/heads/main "$signed"
git reset -q --hard
echo "fixture at $dest: $(git rev-parse HEAD)"
