"""Rebuild a git repo from a .scanrepo master, QR payload files, page images, or a PDF.

Standard library + git. Image decoding uses pyzbar if installed, else the
zbarimg CLI; PDFs are rasterized with pdftoppm (poppler).
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from . import core
except ImportError:  # run as a loose file next to core.py
    import core  # type: ignore

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".ppm", ".pgm", ".pbm"}


# ------------------------------------------------------------------ decoding

def decode_image(path: Path):
    """Return list of raw payload bytes found in one image."""
    try:
        from PIL import Image
        from pyzbar import pyzbar
        img = Image.open(path)
        found = pyzbar.decode(img, symbols=[pyzbar.ZBarSymbol.QRCODE])
        if not found and img.mode != "L":
            found = pyzbar.decode(img.convert("L"), symbols=[pyzbar.ZBarSymbol.QRCODE])
        return [s.data for s in found]
    except ImportError:
        pass
    if not shutil.which("zbarimg"):
        raise core.ScanrepoError("decoding images needs pyzbar (pip install pyzbar pillow) or zbarimg")
    # --raw -Sbinary gives exact bytes but runs several codes together with no
    # separator, so split on the payload headers. (zbarimg --xml base64 output
    # garbles bytes >= 0x80 in zbar 0.23.x, so it is not used.)
    import re
    res = subprocess.run(["zbarimg", "-q", "--raw", "-Sdisable", "-Sqrcode.enable", "-Sbinary", str(path)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    head = rb"[CP]\|" + core.PAYLOAD_TAG.encode() + rb"\|[0-9a-f]{40}\|\d+/\d+\n"
    starts = [m.start() for m in re.finditer(head, res.stdout)]
    return [res.stdout[a:b] for a, b in zip(starts, starts[1:] + [len(res.stdout)])]


def collect_payloads(source: Path, log=print):
    """Payloads from a directory (text payload files and/or images), an image, or a PDF."""
    files = sorted(p for p in source.iterdir() if p.is_file()) if source.is_dir() else [source]
    payloads = []
    for f in files:
        ext = f.suffix.lower()
        if ext == ".pdf":
            with tempfile.TemporaryDirectory() as tmp:
                subprocess.run(["pdftoppm", "-r", "300", "-gray", "-png", str(f), f"{tmp}/page"], check=True)
                for img in sorted(Path(tmp).iterdir()):
                    got = decode_image(img)
                    log(f"scan  {f.name} {img.stem}: {len(got)} code(s)")
                    payloads += got
        elif ext in IMAGE_EXT:
            got = decode_image(f)
            log(f"scan  {f.name}: {len(got)} code(s)")
            payloads += got
        else:
            data = f.read_bytes()
            if data.startswith((b"C|", b"P|")):
                payloads.append(data)
    if not payloads:
        raise core.ScanrepoError(f"no scanrepo QR payloads found in {source}")
    return payloads


def load_master(source: Path, log=print) -> bytes:
    if source.is_file() and source.suffix.lower() not in IMAGE_EXT | {".pdf"}:
        data = source.read_bytes()
        if data.startswith(core.BANNER):
            return data
        if data.startswith((b"C|", b"P|")):
            return core.master_from_payloads([data])
    return core.master_from_payloads(collect_payloads(source, log))


# ------------------------------------------------------------------- restore

def _git(dest, *args, data=None) -> str:
    res = subprocess.run(["git", "-C", str(dest), *args], input=data,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        raise core.ScanrepoError(f"git {' '.join(args)}: {res.stderr.decode().strip()}")
    return res.stdout.decode().strip()


def write_repo(dest: Path, meta, files, report, log=print):
    """Write files, then git objects; check that git computes the same ids."""
    dest.mkdir(parents=True, exist_ok=True)
    if (dest / ".git").exists() or any(dest.iterdir()):
        raise core.ScanrepoError(f"{dest} is not empty")
    for mode, path, sha, data in files:
        out = dest / path
        out.parent.mkdir(parents=True, exist_ok=True)
        if mode == "120000":
            os.symlink(data.decode("utf-8"), out)
        else:
            out.write_bytes(data)
            if mode == "100755":
                out.chmod(0o755)
    _git(dest, "init", "-q")
    _git(dest, "config", "core.autocrlf", "false")
    for mode, path, sha, data in files:
        got = _git(dest, "hash-object", "-w", "--no-filters", "--stdin", data=data)
        if got != sha:
            raise core.ScanrepoError(f"git disagrees on blob {path}: {got} != {sha}")
    for path, tid, body in report["trees"]:
        got = _git(dest, "hash-object", "-t", "tree", "-w", "--stdin", data=body)
        if got != tid:
            raise core.ScanrepoError(f"git disagrees on tree /{path}: {got} != {tid}")
    log(f"git   blob and tree ids agree ({len(files)} blobs, {len(report['trees'])} trees)")
    if not report["commit_verified"]:
        _git(dest, "read-tree", meta["tree"])
        log(f"git   index holds tree {meta['tree'][:12]}; no commit written (commit not verifiable)")
        return
    cid = _git(dest, "hash-object", "-t", "commit", "-w", "--stdin", data=report["commit_raw"])
    if cid != meta["commit"]:
        raise core.ScanrepoError(f"git disagrees on commit: {cid} != {meta['commit']}")
    if core.meta_parents(meta):
        # parents are not on paper: mark the commit as a shallow boundary
        (dest / ".git" / "shallow").write_text(cid + "\n")
    branch = meta.get("branch") or "main"
    ok = subprocess.run(["git", "check-ref-format", "--branch", branch],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    branch = branch if ok and branch != "detached" else "scanrepo"
    _git(dest, "update-ref", f"refs/heads/{branch}", cid)
    _git(dest, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
    _git(dest, "read-tree", "HEAD")
    _git(dest, "update-index", "-q", "--refresh")
    head = _git(dest, "rev-parse", "HEAD")
    log(f"git   HEAD {head} on {branch} (commit id agrees)")


def restore(source, dest=None, strict=False, no_git=False, write_master=None, log=print):
    source = Path(source)
    master = load_master(source, log)
    if write_master:
        Path(write_master).write_bytes(master)
        log(f"wrote master {write_master}")
    meta, omitted, pages = core.parse_master(master)
    log(f"cover {meta['repo']} edition {meta['edition']}  branch {meta['branch']}  "
        f"commit {meta['commit']}  pages {meta['pages']}")
    files, report = core.verify(meta, omitted, pages, log=log)
    if strict and not report["commit_verified"]:
        raise core.ScanrepoError("--strict: commit id could not be verified")
    dest = Path(dest or f"{meta['repo']}-restored")
    if no_git:
        for mode, path, sha, data in files:
            out = dest / path
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
    else:
        write_repo(dest, meta, files, report, log)
    if omitted:
        log(f"note  {len(omitted)} omitted file(s) are not on paper; ids verified, bytes missing")
    log(f"restored {len(files)} files in {dest}")
    return meta, report


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="scanrepo-restore",
        description="Verify a scanrepo booklet (blob, tree, commit ids) and rebuild the git repo.")
    ap.add_argument("source", nargs="?", default="sample.scanrepo",
                    help=".scanrepo master, a folder of QR payload text files and/or page photos, "
                         "a page image, or the booklet PDF")
    ap.add_argument("dest", nargs="?", help="output directory (default: <repo>-restored)")
    ap.add_argument("--strict", action="store_true", help="fail if the commit id cannot be verified")
    ap.add_argument("--no-git", action="store_true", help="write files only, no git objects")
    ap.add_argument("--write-master", metavar="PATH", help="also save the reassembled .scanrepo master")
    args = ap.parse_args(argv)
    try:
        restore(args.source, args.dest, args.strict, args.no_git, args.write_master)
    except core.ScanrepoError as e:
        print(f"REJECT {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
