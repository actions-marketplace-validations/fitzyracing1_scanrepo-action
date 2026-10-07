"""python -m scanrepo {build,generate,render,restore} ..."""
import argparse
import os
import re
import sys
from pathlib import Path

from . import core


def _slug(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(s)).strip("-") or "repo"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="scanrepo", description="Printable git snapshots you can scan back.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def gen_args(p):
        p.add_argument("--repo", default=".", help="git checkout (default .)")
        p.add_argument("--ref", default="HEAD", help="commit-ish to print (default HEAD)")
        p.add_argument("--edition", default="", help="edition (default: commit count of ref)")
        p.add_argument("--branch", default="", help="branch name for the cover")
        p.add_argument("--name", default="", help="repo name for the cover (default: folder name)")
        p.add_argument("--max-file-size", type=int, default=102400,
                       help="omit files larger than this many bytes (0 = no limit; default 102400)")
        p.add_argument("--include", default="", help="globs to include (comma or newline separated)")
        p.add_argument("--exclude", default="", help="globs to exclude (comma or newline separated)")
        p.add_argument("--chunk-size", type=int, default=1800, help="max bytes of text per page")

    b = sub.add_parser("build", help="generate the .scanrepo master and render the PDF")
    gen_args(b)
    b.add_argument("--out-dir", default="scanrepo-out")
    b.add_argument("--page-size", default="letter", choices=["letter", "a4"])
    b.add_argument("--font", default=None, help="monospace TTF to use in the PDF")
    b.add_argument("--no-pdf", action="store_true")
    b.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"),
                   help="append outputs here (default: $GITHUB_OUTPUT)")

    g = sub.add_parser("generate", help="write the .scanrepo master only")
    gen_args(g)
    g.add_argument("-o", "--output", required=True)

    r = sub.add_parser("render", help="render a .scanrepo master to PDF")
    r.add_argument("master")
    r.add_argument("-o", "--output", required=True)
    r.add_argument("--page-size", default="letter", choices=["letter", "a4"])
    r.add_argument("--font", default=None)

    sub.add_parser("restore", help="same as scanrepo-restore.py", add_help=False)

    if argv is None:
        argv = sys.argv[1:]
    if argv[:1] == ["restore"]:
        from .restore import main as restore_main
        return restore_main(argv[1:])
    args = ap.parse_args(argv)
    try:
        if args.cmd in ("build", "generate"):
            from .generate import generate
            master, info = generate(args.repo, args.ref, args.edition or None, args.branch or None,
                                    args.name or None, args.max_file_size, args.include, args.exclude,
                                    args.chunk_size)
            meta = info["meta"]
            if args.cmd == "generate":
                Path(args.output).write_bytes(master)
                print(f"wrote {args.output}")
                return 0
            out = Path(args.out_dir)
            out.mkdir(parents=True, exist_ok=True)
            stem = f"{_slug(meta['repo'])}-ed{_slug(meta['edition'])}-{meta['commit'][:7]}"
            sr = out / f"{stem}.scanrepo"
            sr.write_bytes(master)
            print(f"wrote {sr}")
            pdf = out / f"{stem}.pdf"
            if not args.no_pdf:
                from .render import render
                render(master, pdf, args.page_size, args.font)
            if args.github_output:
                with open(args.github_output, "a") as fh:
                    fh.write(f"scanrepo-path={sr}\n")
                    fh.write(f"pdf-path={'' if args.no_pdf else pdf}\n")
                    fh.write(f"commit={meta['commit']}\n")
                    fh.write(f"edition={meta['edition']}\n")
                    fh.write(f"pages={meta['pages']}\n")
                    fh.write(f"omitted={len(info['omitted'])}\n")
            return 0
        if args.cmd == "render":
            from .render import render
            render(Path(args.master).read_bytes(), args.output, args.page_size, args.font)
            return 0
    except core.ScanrepoError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
