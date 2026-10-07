"""Write a .scanrepo master from a git checkout. Needs git on PATH."""
import re
import subprocess
from pathlib import Path

from . import core

DEFAULT_CHUNK = 1800          # bytes of file text per page (= characters for ASCII)
DEFAULT_MAX_LINES = 72        # also cap lines per page so it fits printed
QR_BUDGET = 2331              # QR version 40, error correction M, byte mode


def git(repo, *args, data=None) -> bytes:
    res = subprocess.run(["git", "-C", str(repo), *args], input=data,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        raise core.ScanrepoError(f"git {' '.join(args)}: {res.stderr.decode().strip()}")
    return res.stdout


def glob_to_regex(pattern: str):
    """Glob with '**' (any dirs), '*' and '?' (not crossing '/'). A pattern with no '/'
    matches the basename anywhere, like .gitignore."""
    pattern = pattern.strip()
    anchored = "/" in pattern.rstrip("/")
    pattern = pattern.lstrip("/")
    if pattern.endswith("/"):
        pattern += "**"
    out, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif c == "*":
            out += "[^/]*"
            i += 1
        elif c == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(c)
            i += 1
    prefix = "" if anchored else "(?:.*/)?"
    return re.compile(f"^{prefix}{out}$")


def split_globs(value):
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = re.split(r"[\n,]", value)
    return [glob_to_regex(p) for p in items if p.strip()]


def is_text(data: bytes) -> bool:
    if b"\0" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def chunk(data: bytes, limit: int, max_lines: int):
    """Split on line ends; long lines split at UTF-8 boundaries. Never returns []."""
    if not data:
        return [b""]
    chunks, cur, cur_lines = [], b"", 0
    for line in _lines(data):
        while len(line) > limit:
            cut = limit
            while cut > 0 and (line[cut] & 0xC0) == 0x80:
                cut -= 1
            if cur:
                chunks.append(cur)
                cur, cur_lines = b"", 0
            chunks.append(line[:cut])
            line = line[cut:]
        if cur and (len(cur) + len(line) > limit or cur_lines >= max_lines):
            chunks.append(cur)
            cur, cur_lines = b"", 0
        cur += line
        cur_lines += 1
    if cur:
        chunks.append(cur)
    return chunks


def _lines(data: bytes):
    # split only on \n so \r stays inside lines
    parts = data.split(b"\n")
    out = [p + b"\n" for p in parts[:-1]]
    if parts[-1]:
        out.append(parts[-1])
    return out


def generate(repo=".", ref="HEAD", edition=None, branch=None, name=None,
             max_file_size=102400, include=None, exclude=None,
             chunk_size=DEFAULT_CHUNK, max_lines=DEFAULT_MAX_LINES, log=print):
    """Return (master_bytes, info dict)."""
    repo = Path(repo)
    commit = git(repo, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}").decode().strip()
    raw_commit = git(repo, "cat-file", "commit", commit)
    commit_meta, tree, parents = core.split_commit(raw_commit)
    if tree is None:
        tree = git(repo, "rev-parse", f"{commit}^{{tree}}").decode().strip()
        parents = git(repo, "rev-list", "--parents", "-n1", commit).decode().split()[1:]
    if edition in (None, ""):
        if git(repo, "rev-parse", "--is-shallow-repository").decode().strip() == "true":
            raise core.ScanrepoError(
                "edition defaults to the commit count, but this clone is shallow. "
                "Use actions/checkout with fetch-depth: 0, or set edition explicitly.")
        edition = git(repo, "rev-list", "--count", commit).decode().strip()
    if not branch:
        branch = ref if ref != "HEAD" and not re.fullmatch(r"[0-9a-f]{7,40}", ref) else ""
        if not branch:
            res = subprocess.run(["git", "-C", str(repo), "symbolic-ref", "--short", "-q", "HEAD"],
                                 stdout=subprocess.PIPE)
            branch = res.stdout.decode().strip() or "detached"
    if not name:
        name = repo.resolve().name
    includes, excludes = split_globs(include), split_globs(exclude)

    listing = git(repo, "ls-tree", "-r", "-l", "-z", "--full-tree", commit).split(b"\0")
    entries = []
    for item in listing:
        if not item:
            continue
        head, path = item.split(b"\t", 1)
        mode, kind, sha, size = head.decode().split()
        try:
            path = path.decode("utf-8")
        except UnicodeDecodeError:
            raise core.ScanrepoError(f"path is not UTF-8: {path!r}")
        entries.append((mode, kind, sha, size, path))
    entries.sort(key=lambda e: e[4].encode())   # byte-sorted, git style

    pages, omitted = [], []
    keep = []
    for mode, kind, sha, size, path in entries:
        core.check_path(path)
        reason = None
        if kind == "commit":
            reason = "submodule"
        elif includes and not any(r.match(path) for r in includes):
            reason = "excluded"
        elif any(r.match(path) for r in excludes):
            reason = "excluded"
        elif max_file_size and int(size) > max_file_size:
            reason = "too-large"
        if reason:
            omitted.append((mode, sha, reason, path))
        else:
            keep.append((mode, sha, path))
    blobs = _read_blobs(repo, [sha for _, sha, _ in keep])
    for mode, sha, path in keep:
        data = blobs[sha]
        if core.git_blob(data) != sha:
            raise core.ScanrepoError(f"git returned bad bytes for {path}")
        if not is_text(data):
            omitted.append((mode, sha, "binary", path))
            continue
        # leave room in the QR for the payload header and page header
        overhead = 64 + len(core.serialize_page(99999, {
            "path": path, "mode": mode, "blob": sha, "chunk": 9999, "chunk_of": 9999,
            "size": 99999, "bytes": b""}))
        limit = max(200, min(chunk_size, QR_BUDGET - overhead))
        parts = chunk(data, limit, max_lines)
        for i, part in enumerate(parts, 1):
            pages.append({"path": path, "mode": mode, "blob": sha, "chunk": i,
                          "chunk_of": len(parts), "size": len(part), "bytes": part})
    omitted.sort(key=lambda o: o[3].encode())

    # self-check: the tree we will print must hash to git's tree id
    tree_entries = [(p["mode"], p["path"], p["blob"]) for p in pages if p["chunk"] == 1]
    tree_entries += [(m, path, sha) for m, sha, _, path in omitted]
    got = core.git_tree(tree_entries)
    if got != tree:
        raise core.ScanrepoError(f"internal tree check failed: {got} != {tree}")

    meta = {
        "repo": name,
        "edition": edition,
        "branch": branch,
        "commit": commit,
        "tree": tree,
        "parent": parents[0] if parents else "none",
        "pages": len(pages),
        "format": core.FORMAT,
    }
    if len(parents) > 1:
        meta["parents"] = " ".join(parents)
    meta.update(commit_meta)
    if omitted:
        meta["omitted"] = len(omitted)
    master = core.serialize_master(meta, omitted, pages)

    # self-check: parse what we wrote and verify blob, tree, commit
    m2, o2, p2 = core.parse_master(master)
    _, report = core.verify(m2, o2, p2, log=lambda *_: None)
    if not report["commit_verified"]:
        raise core.ScanrepoError("internal commit check failed")
    for mode, sha, reason, path in omitted:
        log(f"skip  {reason:<9} {path}")
    log(f"{name} edition {edition}: {len(pages)} pages, {len(set(p['path'] for p in pages))} files, "
        f"{len(omitted)} omitted, commit {commit[:12]}")
    return master, {"meta": meta, "omitted": omitted, "pages": pages}


def _read_blobs(repo, shas):
    if not shas:
        return {}
    uniq = list(dict.fromkeys(shas))
    out = git(repo, "cat-file", "--batch", data=("\n".join(uniq) + "\n").encode())
    blobs, pos = {}, 0
    for sha in uniq:
        nl = out.index(b"\n", pos)
        got, kind, size = out[pos:nl].decode().split()
        size = int(size)
        blobs[got] = out[nl + 1:nl + 1 + size]
        pos = nl + 1 + size + 1
    return blobs
