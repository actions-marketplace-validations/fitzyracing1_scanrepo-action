#!/usr/bin/env bash
# End-to-end: generate -> PDF -> rasterize -> decode QR -> restore -> git agrees.
#   PYTHON=/path/to/venv/bin/python tests/e2e.sh [workdir]
# Needs: git, pdftoppm (poppler), python with requirements.txt + pyzbar/pillow (libzbar0).
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
py=${PYTHON:-python3}
work=${1:-$(mktemp -d)}
mkdir -p "$work"
fail() { echo "FAIL: $*"; exit 1; }

roundtrip() {  # name repo ref extra-args...
  local name=$1 repo=$2 ref=$3; shift 3
  local out="$work/$name"
  rm -rf "$out"; mkdir -p "$out"
  echo "== $name: build"
  (cd "$root" && "$py" -m scanrepo build --repo "$repo" --ref "$ref" --out-dir "$out" --github-output "$out/outputs.txt" "$@")
  local sr pdf
  sr=$(sed -n 's/^scanrepo-path=//p' "$out/outputs.txt")
  pdf=$(sed -n 's/^pdf-path=//p' "$out/outputs.txt")
  echo "== $name: rasterize + decode"
  mkdir -p "$out/png"
  pdftoppm -r 300 -gray -png "$pdf" "$out/png/p"
  "$py" "$root/scanrepo-restore.py" "$out/png" "$out/restored" --strict --write-master "$out/from-scan.scanrepo" | grep -v '^scan '
  cmp "$sr" "$out/from-scan.scanrepo" || fail "$name: master from scan differs"
  echo "ok    master rebuilt from QR codes is byte-identical to $(basename "$sr")"
  echo "== $name: git checks"
  local want_commit want_tree
  want_commit=$(git -C "$repo" rev-parse "$ref^{commit}")
  want_tree=$(git -C "$repo" rev-parse "$ref^{tree}")
  [ "$(git -C "$out/restored" rev-parse HEAD)" = "$want_commit" ] || fail "$name: HEAD"
  [ "$(git -C "$out/restored" rev-parse 'HEAD^{tree}')" = "$want_tree" ] || fail "$name: tree"
  [ "$(git -C "$out/restored" cat-file -t "$want_commit")" = commit ] || fail "$name: cat-file"
  cmp <(git -C "$repo" cat-file commit "$want_commit") <(git -C "$out/restored" cat-file commit "$want_commit") || fail "$name: commit object"
  # every restored file hashes (git hash-object) to the id in the source tree
  n=0
  while IFS=$'\t' read -r meta path; do
    set -- $meta
    if [ -e "$out/restored/$path" ] || [ -L "$out/restored/$path" ]; then
      if [ "$1" = 120000 ]; then got=$(printf %s "$(readlink "$out/restored/$path")" | git hash-object --stdin)
      else got=$(git hash-object --no-filters "$out/restored/$path"); fi
      [ "$got" = "$3" ] || fail "$name: $path $got != $3"
      n=$((n+1))
    fi
  done < <(git -C "$repo" ls-tree -r "$want_commit")
  echo "ok    git: HEAD=$want_commit tree=$want_tree, $n files match git hash-object"
  git -C "$out/restored" status --porcelain | sed 's/^/      status: /'
}

roundtrip self "$root" HEAD --name scanrepo-action
fixture="$work/fixture-repo"
"$root/tests/make_fixture.sh" "$fixture" >/dev/null
roundtrip fixture "$fixture" HEAD --name fixture --exclude 'secret.env' --max-file-size 100000

many="$work/many-omitted-repo"   # long OMITTED block -> multi-part cover code
rm -rf "$many"; mkdir -p "$many/assets"
git -C "$many" init -q -b main
python3 -c "
import pathlib
for i in range(90):
    pathlib.Path('$many/assets/photo-%03d-with-a-longish-name.png' % i).write_bytes(bytes([0x89, 0, i]) * 20)
pathlib.Path('$many/README.md').write_text('assets only\\n')"
git -C "$many" add . && git -C "$many" -c user.name=T -c user.email=t@example.com commit -q -m "many binaries"
roundtrip many-omitted "$many" HEAD --name many

echo "== payload text files"
rm -rf "$work/payloads" "$work/from-payloads"; mkdir -p "$work/payloads"
"$py" - "$(sed -n 's/^scanrepo-path=//p' "$work/fixture/outputs.txt")" "$work/payloads" <<'PY'
import sys; sys.path.insert(0, ".")
from pathlib import Path
from scanrepo import core
master = Path(sys.argv[1]).read_bytes(); out = Path(sys.argv[2])
meta, omitted, pages = core.parse_master(master)
cover = master[:master.find(b"## PAGE 1\n")]
for i, p in enumerate(core.cover_payloads(cover, meta["commit"]), 1):
    (out / f"cover-{i}.txt").write_bytes(p)
for n, page in enumerate(pages, 1):
    (out / f"page-{n:03}.txt").write_bytes(core.page_payload(n, len(pages), meta["commit"], core.serialize_page(n, page)))
print(f"wrote {len(list(out.iterdir()))} payload files")
PY
(cd "$root" && "$py" scanrepo-restore.py "$work/payloads" "$work/from-payloads" --strict | tail -3)

echo "== legacy sample.scanrepo (format 1.0)"
rm -rf "$work/sample"
(cd "$root" && "$py" scanrepo-restore.py sample.scanrepo "$work/sample")

echo "== tamper checks"
sr=$(sed -n 's/^scanrepo-path=//p' "$work/fixture/outputs.txt")
sed 's/^    return x \* 7  # line 7$/    return x * 8  # line 7/' "$sr" > "$work/t1.scanrepo"
sed 's/^message: "signed-style third commit\\n"$/message: "signed-style 3rd commit\\n"/' "$sr" > "$work/t2.scanrepo"
sed 's/^\(100644 [0-9a-f]* binary logo.bin\)$/\1x/' "$sr" > "$work/t3.scanrepo"
for t in t1 t2 t3; do
  cmp -s "$sr" "$work/$t.scanrepo" && fail "$t: tamper did not change the file"
  rm -rf "$work/$t-out"
  if "$py" "$root/scanrepo-restore.py" "$work/$t.scanrepo" "$work/$t-out" >/dev/null 2>"$work/$t.err"; then fail "$t accepted"; fi
  echo "ok    $t rejected: $(cat "$work/$t.err")"
done
echo "ALL E2E CHECKS PASSED  (workdir $work)"
