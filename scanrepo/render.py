"""Render a .scanrepo master to a printable PDF: cover + one page per page block.

Needs reportlab and segno (pip install -r requirements.txt).
"""
import datetime as dt
import json
import os

import segno
from reportlab.lib.colors import Color, black, white
from reportlab.lib.pagesizes import A4, letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from . import core

GRAY = Color(0.45, 0.45, 0.45)
LIGHT = Color(0.8, 0.8, 0.8)
MARGIN = 36
TAB = 4
FONT_CANDIDATES = [
    ("DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf"),
    ("LiberationMono-Regular.ttf", "LiberationMono-Bold.ttf"),
    ("NotoSansMono-Regular.ttf", "NotoSansMono-Bold.ttf"),
]
FONT_DIRS = ["/usr/share/fonts", "/usr/local/share/fonts", "/Library/Fonts",
             "/System/Library/Fonts", os.path.expanduser("~/.fonts"), "C:/Windows/Fonts"]


def _find_font(name):
    for root in FONT_DIRS:
        for dirpath, _, files in os.walk(root):
            if name in files:
                return os.path.join(dirpath, name)
    return None


def setup_fonts(font_path=None):
    """Register a Unicode monospace TTF. Falls back to Courier (Latin-1 glyphs only)."""
    pairs = [(font_path, None)] if font_path else []
    pairs += [(_find_font(r), _find_font(b)) for r, b in FONT_CANDIDATES]
    for regular, bold in pairs:
        if regular and os.path.exists(regular):
            pdfmetrics.registerFont(TTFont("Mono", regular))
            pdfmetrics.registerFont(TTFont("Mono-Bold", bold if bold and os.path.exists(bold) else regular))
            return "Mono", "Mono-Bold", True
    return "Courier", "Courier-Bold", False


def make_qr(payload: bytes):
    """Smallest QR at error correction M or better (L only if M will not fit).

    Byte mode with an ECI 26 (UTF-8) header: without it, zbar and some phone
    apps guess Shift-JIS for non-ASCII text and change the bytes.
    """
    text = payload.decode("utf-8")
    try:
        return segno.make_qr(text, error="m", boost_error=True, mode="byte", encoding="utf-8", eci=True)
    except segno.DataOverflowError:
        return segno.make_qr(text, error="l", boost_error=False, mode="byte", encoding="utf-8", eci=True)


def draw_qr(c, qr, x, y, size):
    """Vector QR with a 4-module quiet zone inside size x size, lower-left at (x, y)."""
    matrix = qr.matrix
    n = len(matrix) + 8
    m = size / n
    c.setFillColor(white)
    c.rect(x, y, size, size, stroke=0, fill=1)
    c.setFillColor(black)
    for r, row in enumerate(matrix):
        yy = y + size - (r + 5) * m
        col = 0
        while col < len(row):
            if row[col]:
                start = col
                while col < len(row) and row[col]:
                    col += 1
                c.rect(x + (start + 4) * m, yy, (col - start) * m, m, stroke=0, fill=1)
            else:
                col += 1
    return m


def display_line(text: str, ascii_only: bool):
    out, col = [], 0
    for ch in text:
        if ch == "\t":
            pad = TAB - col % TAB
            out.append(" " * pad)
            col += pad
            continue
        o = ord(ch)
        if o < 32 or o == 127:
            ch = "^" + chr(o ^ 0x40)
        elif ascii_only and o > 255:
            ch = "?"
        out.append(ch)
        col += len(ch)
    return "".join(out)


def wrap(text, width_chars):
    if width_chars < 1:
        width_chars = 1
    if not text:
        return [""]
    return [text[i:i + width_chars] for i in range(0, len(text), width_chars)]


class Renderer:
    def __init__(self, out_path, page_size="letter", font_path=None):
        self.size = A4 if str(page_size).lower() == "a4" else letter
        self.W, self.H = self.size
        self.c = canvas.Canvas(str(out_path), pagesize=self.size, pageCompression=1)
        self.font, self.bold, self.unicode = setup_fonts(font_path)
        self.qr_size = min(self.W - 2 * MARGIN, 3.4 * 72)
        self.qr_info = []

    def text(self, x, y, s, size=9, font=None, color=black):
        self.c.setFillColor(color)
        self.c.setFont(font or self.font, size)
        self.c.drawString(x, y, s)

    def rtext(self, x, y, s, size=9, font=None, color=black):
        self.c.setFillColor(color)
        self.c.setFont(font or self.font, size)
        self.c.drawRightString(x, y, s)

    def cw(self, size):
        return pdfmetrics.stringWidth("M", self.font, size)

    # ------------------------------------------------------------- cover
    def cover(self, meta, omitted, pages, cover_text):
        c, W, H = self.c, self.W, self.H
        payloads = core.cover_payloads(cover_text, meta["commit"], limit=1900)
        qrs = [make_qr(p) for p in payloads]
        for p, q in zip(payloads, qrs):
            self.qr_info.append(("cover", len(p), q.designator))
        x, y = MARGIN, H - MARGIN - 10
        self.text(x, y, "SCANREPO  ·  PRINTED GIT", 8, color=GRAY)
        self.rtext(W - MARGIN, y, "cover", 8, color=GRAY)
        y -= 40
        self.text(x, y, meta["repo"], 30, self.bold)
        y -= 26
        self.text(x, y, f"Edition {meta['edition']}", 16)
        y -= 14
        c.setStrokeColor(black)
        c.line(MARGIN, y, W - MARGIN, y)
        y -= 18
        rows = [("repo", meta["repo"]), ("edition", meta["edition"]), ("branch", meta["branch"]),
                ("commit", meta["commit"]), ("tree", meta["tree"]),
                ("parent", meta.get("parents") or meta["parent"]), ("pages", meta["pages"])]
        who = _who(meta)
        if who:
            rows += who
        if omitted:
            rows.append(("omitted", f"{len(omitted)} file(s), ids only"))
        for k, v in rows:
            self.text(x, y, f"{k:<10}", 9, self.bold)
            self.text(x + self.cw(9) * 11, y, str(v), 9)
            y -= 13
        msg = _message(meta)
        if msg is not None:
            y -= 6
            self.text(x, y, "message", 9, self.bold)
            y -= 13
            width = int((W - 2 * MARGIN) / self.cw(8.5))
            lines = []
            for line in msg.rstrip("\n").split("\n"):
                lines += wrap(display_line(line, not self.unicode), width)
            for line in lines[:10]:
                self.text(x, y, line, 8.5)
                y -= 11
            if len(lines) > 10:
                self.text(x, y, f"... ({len(lines) - 10} more lines in the cover code)", 8.5, color=GRAY)
                y -= 11
        # one cover code: bottom right, contents to its left.
        # several (long signature, many omitted files): they get their own pages.
        if len(qrs) == 1:
            qsize, qy = self.qr_size, MARGIN + 24
            draw_qr(c, qrs[0], W - MARGIN - qsize, qy, qsize)
            self.rtext(W - MARGIN, qy - 12, f"C| cover code  ·  QR {qrs[0].designator}", 7, color=GRAY)
            right, bottom = W - MARGIN - qsize - 14, qy
        else:
            right, bottom = W - MARGIN, MARGIN + 24
            self.text(x, y - 4, f"cover codes: {len(qrs)} parts (C| 1/{len(qrs)} .. {len(qrs)}/{len(qrs)}) on the next page(s)",
                      8.5, color=GRAY)
            y -= 16
        y -= 10
        self.contents(x, y, right, bottom, pages, omitted)
        self.text(MARGIN, MARGIN, "Scan back: photograph every page, then  python3 scanrepo-restore.py photos/ out/",
                  7, color=GRAY)
        c.showPage()
        if len(qrs) > 1:
            qsize = min((W - 2 * MARGIN - 24) / 2, (H - 2 * MARGIN - 70) / 2)
            for start in range(0, len(qrs), 4):
                self.text(MARGIN, H - MARGIN - 10,
                          f"{meta['repo']}  ·  edition {meta['edition']}  ·  cover codes", 8, color=GRAY)
                for k, q in enumerate(qrs[start:start + 4]):
                    gx = MARGIN + (k % 2) * (qsize + 24)
                    gy = H - MARGIN - 30 - (k // 2 + 1) * (qsize + 20)
                    draw_qr(c, q, gx, gy, qsize)
                    self.text(gx, gy - 10, f"C| part {start + k + 1}/{len(qrs)}  ·  QR {q.designator}", 7, color=GRAY)
                c.showPage()

    def contents(self, x, y, right, bottom, pages, omitted):
        size = 7.5
        lead = 9.5
        width = int((right - x) / self.cw(size))
        spans = {}
        for n, p in enumerate(pages, 1):
            spans.setdefault(p["path"], [n, n])[1] = n
        rows = [(f"{a}" if a == b else f"{a}-{b}", path) for path, (a, b) in spans.items()]
        rows += [("--", f"{path}  [{reason}]") for _, _, reason, path in omitted]
        if y - lead < bottom + lead:
            return y
        self.text(x, y, "contents", 9, self.bold)
        y -= lead + 2
        for i, (pg, path) in enumerate(rows):
            if y < bottom + lead and i < len(rows) - 1:
                self.text(x, y, f"... {len(rows) - i} more", size, color=GRAY)
                return y - lead
            line = f"{pg:>9}  {path}"
            self.text(x, y, display_line(line, not self.unicode)[:width], size)
            y -= lead
        return y

    # -------------------------------------------------------------- pages
    def page(self, n, total, meta, page, block, line_start):
        c, W, H = self.c, self.W, self.H
        payload = core.page_payload(n, total, meta["commit"], block)
        qr = make_qr(payload)
        self.qr_info.append((n, len(payload), qr.designator))
        top = H - MARGIN - 10
        self.text(MARGIN, top, f"{meta['repo']}  ·  edition {meta['edition']}  ·  {meta['commit'][:12]}", 8, color=GRAY)
        self.rtext(W - MARGIN, top, f"page {n} / {total}", 8, color=GRAY)
        y = top - 18
        chunk = f"  ({page['chunk']}/{page['chunk_of']})" if int(page["chunk_of"]) > 1 else ""
        if page["mode"] == "120000":
            chunk += "  (symlink; text is the target)"
        self.text(MARGIN, y, display_line(page["path"], not self.unicode) + chunk, 11, self.bold)
        y -= 12
        self.text(MARGIN, y, f"mode {page['mode']}  ·  blob {page['blob']}  ·  {page['size']} bytes", 7.5, color=GRAY)
        y -= 6
        c.setStrokeColor(black)
        c.line(MARGIN, y, W - MARGIN, y)
        text_top = y - 12
        qx = W - MARGIN - self.qr_size
        qy = text_top + 8 - self.qr_size
        draw_qr(c, qr, qx, qy, self.qr_size)
        self.rtext(W - MARGIN, qy - 9, f"P| page {n}/{total}  ·  QR {qr.designator}", 6.5, color=GRAY)
        qr_bottom = qy - 16
        narrow_right = qx - 12
        full_right = W - MARGIN
        bottom = MARGIN + 6

        try:
            text = page["bytes"].decode("utf-8")
        except UnicodeDecodeError:
            text = page["bytes"].decode("utf-8", "replace")
        lines = text.split("\n")
        if text.endswith("\n"):
            lines = lines[:-1]
        last_no = line_start + len(lines) - 1
        gutter = len(str(max(last_no, 1))) + 1
        for size in [x / 4 for x in range(32, 17, -1)]:  # 8.0pt .. 4.5pt
            lead = size * 1.18
            cw = self.cw(size)
            rows, yy = [], text_top
            for i, line in enumerate(lines):
                disp = display_line(line, not self.unicode)
                first = True
                while True:
                    right = narrow_right if yy > qr_bottom else full_right
                    width = int((right - MARGIN) / cw) - gutter - 1
                    piece, disp = disp[:max(width, 1)], disp[max(width, 1):]
                    rows.append((yy, str(line_start + i) if first else "", piece))
                    yy -= lead
                    first = False
                    if not disp:
                        break
            if yy >= bottom:
                break
        else:
            raise core.ScanrepoError(f"page {n}: text does not fit even at 4.5pt")
        for yy, no, piece in rows:
            if no:
                self.rtext(MARGIN + cw * gutter - cw * 0.5, yy, no, size, color=GRAY)
            else:
                self.rtext(MARGIN + cw * gutter - cw * 0.5, yy, "+", size, color=LIGHT)
            self.text(MARGIN + cw * (gutter + 0.5), yy, piece, size)
        if not text.endswith("\n") and int(page["chunk"]) == int(page["chunk_of"]) and text:
            self.text(MARGIN + cw * (gutter + 0.5), rows[-1][0] - size * 1.18, "(no newline at end of file)", size * 0.9, color=GRAY)
        c.showPage()

    def save(self):
        self.c.save()


def _who(meta):
    rows = []
    for key in ("author", "committer"):
        if key in meta:
            v = meta[key]
            rows.append((key, _pretty_ident(v)))
    return rows


def _pretty_ident(v):
    try:
        ident, ts, tz = v.rsplit(" ", 2)
        sign = 1 if tz[0] == "+" else -1
        off = dt.timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5])) * sign
        when = dt.datetime.fromtimestamp(int(ts), dt.timezone(off))
        return f"{ident}  {when:%Y-%m-%d %H:%M} {tz}"
    except (ValueError, IndexError):
        return v


def _message(meta):
    if "message" in meta:
        return json.loads(meta["message"])
    return None


def render(master: bytes, out_path, page_size="letter", font_path=None, log=print):
    meta, omitted, pages = core.parse_master(master)
    cover_text = master[:master.find(b"## PAGE 1\n")] if pages else master
    r = Renderer(out_path, page_size, font_path)
    if not r.unicode:
        log("warn  no Unicode monospace TTF found; using Courier (non-Latin-1 text prints as '?', QR codes are unaffected)")
    r.cover(meta, omitted, pages, cover_text)
    line_no = {}
    for n, page in enumerate(pages, 1):
        start = line_no.get(page["path"], 1)
        block = core.serialize_page(n, page) if "size" in page else _legacy_block(master, n)
        r.page(n, len(pages), meta, page, block, start)
        line_no[page["path"]] = start + page["bytes"].count(b"\n")
    r.save()
    versions = sorted({d for _, _, d in r.qr_info}, key=lambda s: (int(s.split("-")[0]), s))
    biggest = max(r.qr_info, key=lambda t: t[1])
    log(f"pdf   {out_path}: {r.c.getPageNumber() - 1} sheets, {len(r.qr_info)} QR codes "
        f"(largest payload {biggest[1]} bytes, QR {biggest[2]}; versions used {', '.join(versions)})")
    return r.qr_info


def _legacy_block(master: bytes, n: int) -> bytes:
    start = master.find(f"## PAGE {n}\n".encode())
    end = master.find(b"\n## END PAGE", start)
    return master[start:end + len(b"\n## END PAGE")]
