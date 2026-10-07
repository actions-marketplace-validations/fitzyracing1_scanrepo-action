"""scanrepo core: format, git object hashing, QR payloads. Standard library only.

This module is deliberately self-contained so it can be copied (or typed in
from paper) next to a pile of scanned pages and still work.
"""
import base64
import hashlib
import json
import re

BANNER = b"# scanrepo v1"
FORMAT = "1.1"
REQUIRED_META = ("repo", "edition", "branch", "commit", "tree", "parent", "pages")
REQUIRED_PAGE = ("path", "mode", "blob", "chunk", "chunk_of")
FILE_MODES = ("100644", "100755", "120000")
OMIT_MODES = FILE_MODES + ("160000",)
TREE_MODE = "40000"
PAYLOAD_TAG = "scanrepo1"
END_PAGE = b"## END PAGE"
# Fields of the commit object that can be stored as text or as <key>_b64.
COMMIT_TEXT_KEYS = ("author", "committer")
COMMIT_JSON_KEYS = ("message", "commit_extra")


class ScanrepoError(Exception):
    pass


# ---------------------------------------------------------------- git hashing

def git_object_id(kind: str, body: bytes) -> str:
    return hashlib.sha1(f"{kind} {len(body)}\0".encode() + body).hexdigest()


def git_blob(data: bytes) -> str:
    return git_object_id("blob", data)


def check_path(path: str) -> None:
    """Reject paths that git would never store or that could escape a checkout."""
    if not path or path.startswith("/") or path.endswith("/") or "\n" in path or "\0" in path:
        raise ScanrepoError(f"unsafe path: {path!r}")
    for part in path.split("/"):
        if part in ("", ".", "..") or part.lower() == ".git":
            raise ScanrepoError(f"unsafe path: {path!r}")


def build_tree(entries):
    """entries: iterable of (mode, path, sha). Returns nested dict.

    A dict value is a subtree; a tuple value is (mode, sha) for a leaf.
    """
    root = {}
    for mode, path, sha in entries:
        check_path(path)
        parts = path.split("/")
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise ScanrepoError(f"path conflict at {path!r}")
        if parts[-1] in node:
            raise ScanrepoError(f"duplicate path {path!r}")
        node[parts[-1]] = (mode, sha)
    return root


def _tree_sort_key(item):
    name, value = item
    raw = name.encode()
    # git compares directory names as if they ended with "/"
    return raw + b"/" if isinstance(value, dict) else raw


def tree_objects(node, out=None, prefix=""):
    """Hash a nested tree. Returns (tree_id, list of (dir_path, tree_id, body))."""
    if out is None:
        out = []
    body = b""
    for name, value in sorted(node.items(), key=_tree_sort_key):
        if isinstance(value, dict):
            sub, _ = tree_objects(value, out, f"{prefix}{name}/")
            mode, sha = TREE_MODE, sub
        else:
            mode, sha = value
        body += f"{mode} {name}\0".encode() + bytes.fromhex(sha)
    tid = git_object_id("tree", body)
    out.append((prefix.rstrip("/"), tid, body))
    return tid, out


def git_tree(entries) -> str:
    """Tree id for (mode, path, sha) entries; paths may contain '/'."""
    return tree_objects(build_tree(entries))[0]


def commit_body(meta: dict) -> bytes:
    """Rebuild the raw commit object from cover fields."""
    if "commit_object_b64" in meta:
        return base64.b64decode(meta["commit_object_b64"])
    fields = {}
    for key in COMMIT_TEXT_KEYS:
        if key + "_b64" in meta:
            fields[key] = base64.b64decode(meta[key + "_b64"])
        elif key in meta:
            fields[key] = meta[key].encode()
        else:
            return None
    for key in COMMIT_JSON_KEYS:
        if key + "_b64" in meta:
            fields[key] = base64.b64decode(meta[key + "_b64"])
        elif key in meta:
            fields[key] = json.loads(meta[key]).encode()
        elif key == "message":
            return None
        else:
            fields[key] = b""
    parents = meta_parents(meta)
    out = f"tree {meta['tree']}\n".encode()
    for p in parents:
        out += f"parent {p}\n".encode()
    out += b"author " + fields["author"] + b"\n"
    out += b"committer " + fields["committer"] + b"\n"
    out += fields["commit_extra"]
    out += b"\n" + fields["message"]
    return out


def meta_parents(meta: dict):
    if meta.get("parents"):
        return meta["parents"].split()
    return [] if meta["parent"] == "none" else [meta["parent"]]


def split_commit(raw: bytes):
    """Split a raw commit object into cover fields. Falls back to the whole object."""
    head, sep, message = raw.partition(b"\n\n")
    lines = head.split(b"\n")
    fields = {"parents": []}
    i = 0
    ok = bool(sep) and lines[0].startswith(b"tree ")
    if ok:
        fields["tree"] = lines[0][5:].decode()
        i = 1
        while i < len(lines) and lines[i].startswith(b"parent "):
            fields["parents"].append(lines[i][7:].decode())
            i += 1
        ok = (i + 1 < len(lines) and lines[i].startswith(b"author ")
              and lines[i + 1].startswith(b"committer "))
    if ok:
        fields["author"] = lines[i][7:]
        fields["committer"] = lines[i + 1][10:]
        extra = lines[i + 2:]
        fields["commit_extra"] = b"".join(l + b"\n" for l in extra)
        fields["message"] = message
    meta = {}
    if ok:
        for key in COMMIT_TEXT_KEYS:
            meta.update(_text_or_b64(key, fields[key], as_json=False))
        meta.update(_text_or_b64("message", fields["message"], as_json=True))
        if fields["commit_extra"]:
            meta.update(_text_or_b64("commit_extra", fields["commit_extra"], as_json=True))
    probe = dict(meta, tree=fields.get("tree", ""), parent="none",
                 parents=" ".join(fields.get("parents", [])))
    if not ok or commit_body(probe) != raw:
        meta = {"commit_object_b64": base64.b64encode(raw).decode()}
    return meta, fields.get("tree"), fields.get("parents", [])


def _text_or_b64(key, value: bytes, as_json: bool):
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError:
        return {key + "_b64": base64.b64encode(value).decode()}
    if as_json:
        return {key: json.dumps(text, ensure_ascii=False)}
    if "\n" in text or "\r" in text:
        return {key + "_b64": base64.b64encode(value).decode()}
    return {key: text}


# -------------------------------------------------------------- master format

def serialize_cover(meta: dict, omitted) -> bytes:
    """Everything before '## PAGE 1': banner, frontmatter, optional OMITTED block."""
    out = [BANNER, b"---"]
    for key, value in meta.items():
        value = str(value)
        if "\n" in value or "\r" in value:
            raise ScanrepoError(f"frontmatter value for {key} has a newline")
        out.append(f"{key}: {value}".encode())
    out.append(b"---")
    if omitted:
        out.append(b"## OMITTED")
        for mode, sha, reason, path in omitted:
            out.append(f"{mode} {sha} {reason} {path}".encode())
        out.append(b"## END OMITTED")
    return b"\n".join(out) + b"\n"


def serialize_page(n: int, page: dict) -> bytes:
    """One page block, from '## PAGE n' through '## END PAGE' (no trailing newline)."""
    head = [f"## PAGE {n}"]
    for key in REQUIRED_PAGE + ("size",):
        head.append(f"{key}: {page[key]}")
    head.append("text:")
    return "\n".join(head).encode() + b"\n" + page["bytes"] + b"\n" + END_PAGE


def serialize_master(meta: dict, omitted, pages) -> bytes:
    out = serialize_cover(meta, omitted)
    for n, page in enumerate(pages, 1):
        out += serialize_page(n, page) + b"\n"
    return out


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def eof(self):
        return self.pos >= len(self.data)

    def line(self) -> bytes:
        end = self.data.find(b"\n", self.pos)
        if end < 0:
            end = len(self.data)
        out = self.data[self.pos:end]
        self.pos = end + 1
        return out


def _kv(line: bytes, where: str):
    text = line.decode("utf-8")
    if ": " not in text:
        raise ScanrepoError(f"bad {where} line: {text!r}")
    k, v = text.split(": ", 1)
    return k, v


def parse_master(data: bytes):
    """Parse a .scanrepo master. Returns (meta, omitted, pages).

    Each page dict carries 'bytes' (exact when a size key is present) and,
    for legacy pages without size, 'raw' (newline before END PAGE kept).
    """
    r = _Reader(data)
    if r.line() != BANNER:
        raise ScanrepoError("not a scanrepo v1 file (line 1 must be '# scanrepo v1')")
    if r.line() != b"---":
        raise ScanrepoError("missing frontmatter")
    meta = {}
    while True:
        if r.eof():
            raise ScanrepoError("unterminated frontmatter")
        line = r.line()
        if line == b"---":
            break
        k, v = _kv(line, "frontmatter")
        meta[k] = v
    missing = [k for k in REQUIRED_META if k not in meta]
    if missing:
        raise ScanrepoError(f"frontmatter missing: {', '.join(missing)}")
    omitted, pages = [], []
    while not r.eof():
        line = r.line()
        if line == b"## OMITTED":
            while True:
                if r.eof():
                    raise ScanrepoError("unterminated OMITTED block")
                line = r.line()
                if line == b"## END OMITTED":
                    break
                mode, sha, reason, path = line.decode("utf-8").split(" ", 3)
                omitted.append((mode, sha, reason, path))
        elif line.startswith(b"## PAGE "):
            n = int(line[8:])
            page = {"n": n}
            while True:
                if r.eof():
                    raise ScanrepoError(f"page {n}: no text: line")
                line = r.line()
                if line == b"text:":
                    break
                k, v = _kv(line, f"page {n} header")
                page[k] = v
            missing = [k for k in REQUIRED_PAGE if k not in page]
            if missing:
                raise ScanrepoError(f"page {n} missing: {', '.join(missing)}")
            if "size" in page:
                size = int(page["size"])
                page["bytes"] = r.data[r.pos:r.pos + size]
                r.pos += size
                tail = b"\n" + END_PAGE
                if r.data[r.pos:r.pos + len(tail)] != tail or r.data[r.pos + len(tail):r.pos + len(tail) + 1] not in (b"", b"\n"):
                    raise ScanrepoError(f"page {n}: size {size} does not land on '## END PAGE'")
                r.pos += len(tail) + 1
            else:
                # legacy rule: body runs to the first line that is exactly ## END PAGE
                body_start = r.pos
                end = None
                probe = r.pos
                while probe <= len(r.data):
                    nl = r.data.find(b"\n", probe)
                    stop = len(r.data) if nl < 0 else nl
                    if r.data[probe:stop] == END_PAGE:
                        end = probe
                        break
                    if nl < 0:
                        break
                    probe = nl + 1
                if end is None:
                    raise ScanrepoError(f"page {n}: no '## END PAGE'")
                raw = r.data[body_start:end]
                page["raw"] = raw
                page["bytes"] = raw[:-1] if raw.endswith(b"\n") else raw
                r.pos = end + len(END_PAGE) + 1
            pages.append(page)
        elif line.strip() == b"":
            continue
        else:
            raise ScanrepoError(f"unexpected line: {line[:60]!r}")
    if [p["n"] for p in pages] != list(range(1, int(meta["pages"]) + 1)):
        raise ScanrepoError(f"pages must be numbered 1..{meta['pages']} with no gaps; "
                            f"found {len(pages)} page blocks")
    return meta, omitted, pages


# ---------------------------------------------------------------- QR payloads
#
#   cover: C|scanrepo1|<commit>|<part>/<parts>\n<segment>
#   page:  P|scanrepo1|<commit>|<page>/<pages>\n<page block>
#
# Cover segments concatenate to the cover text (banner .. frontmatter ..
# OMITTED block). A page block is exactly the master's '## PAGE n' ..
# '## END PAGE'. cover text + each page block + '\n' == the master, byte for byte.

def cover_payloads(cover: bytes, commit: str, limit: int = 2200):
    """Split the cover text into one or more C| payloads (never inside a UTF-8 char)."""
    segs, pos = [], 0
    while pos < len(cover) or not segs:
        cut = min(pos + limit, len(cover))
        while cut < len(cover) and cut > pos and (cover[cut] & 0xC0) == 0x80:
            cut -= 1
        segs.append(cover[pos:cut])
        pos = cut
    total = len(segs)
    return [f"C|{PAYLOAD_TAG}|{commit}|{i}/{total}\n".encode() + seg
            for i, seg in enumerate(segs, 1)]


def page_payload(n: int, pages: int, commit: str, block: bytes) -> bytes:
    return f"P|{PAYLOAD_TAG}|{commit}|{n}/{pages}\n".encode() + block


_PAYLOAD_RE = re.compile(rb"^([CP])\|" + PAYLOAD_TAG.encode() + rb"\|([0-9a-f]{40})\|(\d+)/(\d+)\n")


def parse_payload(data: bytes):
    m = _PAYLOAD_RE.match(data)
    if not m:
        raise ScanrepoError(f"not a scanrepo payload: {data[:40]!r}")
    kind, commit, i, total = m.group(1).decode(), m.group(2).decode(), int(m.group(3)), int(m.group(4))
    return kind, commit, i, total, data[m.end():]


def master_from_payloads(payloads):
    """Reassemble the master from decoded QR payloads (any order, duplicates ok)."""
    covers, pages, commits = {}, {}, set()
    cover_total = page_total = None
    for data in payloads:
        kind, commit, i, total, body = parse_payload(data)
        commits.add(commit)
        if kind == "C":
            cover_total = total
            covers[i] = body
        else:
            page_total = total
            pages[i] = body
    if len(commits) > 1:
        raise ScanrepoError(f"payloads from more than one edition: {sorted(commits)}")
    if cover_total is None:
        raise ScanrepoError("no cover code (C|) found")
    missing_c = [i for i in range(1, cover_total + 1) if i not in covers]
    if missing_c:
        raise ScanrepoError(f"missing cover parts: {missing_c}")
    cover = b"".join(covers[i] for i in range(1, cover_total + 1))
    meta_pages = None
    for line in cover.split(b"\n"):
        if line.startswith(b"pages: "):
            meta_pages = int(line[7:])
            break
    want = meta_pages if meta_pages is not None else (page_total or 0)
    missing = [i for i in range(1, want + 1) if i not in pages]
    if missing:
        raise ScanrepoError(f"missing pages: {missing}")
    return cover + b"".join(pages[i] + b"\n" for i in range(1, want + 1))


# ----------------------------------------------------------------- verification

def rejoin(chunks):
    """Rejoin chunks for one path. Returns list of candidate byte strings, best first."""
    chunks = sorted(chunks, key=lambda p: int(p["chunk"]))
    want = list(range(1, int(chunks[0]["chunk_of"]) + 1))
    if [int(c["chunk"]) for c in chunks] != want:
        raise ScanrepoError(f"{chunks[0]['path']}: chunks {[c['chunk'] for c in chunks]} != {want}")
    for c in chunks:
        if c["blob"] != chunks[0]["blob"] or c["mode"] != chunks[0]["mode"]:
            raise ScanrepoError(f"{c['path']}: chunks disagree on blob/mode")
    exact = b"".join(c["bytes"] for c in chunks)
    if all("raw" not in c for c in chunks):
        return [exact]
    raws = [c.get("raw", c["bytes"]) for c in chunks]
    return [
        exact,                                            # spec rule: strip each
        b"".join(raws),                                   # keep newline before END PAGE
        b"".join(raws[:-1]) + chunks[-1]["bytes"],        # old restorer behaviour
    ]


def verify(meta, omitted, pages, log=print):
    """Check every blob, the tree, and the commit. Returns (files, report).

    files: list of (mode, path, sha, data). Raises ScanrepoError on mismatch.
    """
    by_path = {}
    for page in pages:
        by_path.setdefault(page["path"], []).append(page)
    files, entries = [], []
    for path, chunks in by_path.items():
        check_path(path)
        want = chunks[0]["blob"]
        mode = chunks[0]["mode"]
        if mode not in FILE_MODES:
            raise ScanrepoError(f"{path}: unsupported mode {mode}")
        for data in rejoin(chunks):
            if git_blob(data) == want:
                break
        else:
            raise ScanrepoError(f"blob mismatch for {path}: got {git_blob(rejoin(chunks)[0])} want {want}")
        files.append((mode, path, want, data))
        entries.append((mode, path, want))
        log(f"ok    blob {want[:12]}  {path}" + (f"  ({len(chunks)} pages)" if len(chunks) > 1 else ""))
    for mode, sha, reason, path in omitted:
        if mode not in OMIT_MODES:
            raise ScanrepoError(f"{path}: unsupported omitted mode {mode}")
        entries.append((mode, path, sha))
        log(f"omit  blob {sha[:12]}  {path}  ({reason}; id only, bytes not on paper)")
    tree, objects = tree_objects(build_tree(entries))
    if tree != meta["tree"]:
        raise ScanrepoError(f"tree mismatch: got {tree} want {meta['tree']}")
    log(f"ok    tree {tree}")
    report = {"tree": tree, "trees": objects, "commit_verified": False, "commit_raw": None}
    raw = commit_body(meta)
    if raw is None:
        log(f"WARN  commit {meta['commit']} NOT verified: master has no author/committer/message "
            "(pre-1.1 master)")
        return files, report
    cid = git_object_id("commit", raw)
    if cid != meta["commit"]:
        raise ScanrepoError(f"commit mismatch: got {cid} want {meta['commit']}")
    log(f"ok    commit {cid}  (parent {meta['parent']})")
    report["commit_verified"] = True
    report["commit_raw"] = raw
    return files, report
