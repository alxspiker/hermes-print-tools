"""Render printer-safe, one-page letter art with local system Pillow.

All content is pixel-positioned using font metrics and bounded margins. No
HTML engine, external network access, dynamic code, or handwritten PDF syntax.
A JPEG for any CUPS printer and a matching valid PDF are generated together.
"""
import argparse
import json
import os
import pwd
import re
import sys
import subprocess
import shutil
from functools import lru_cache
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 2550, 3300  # Letter at 300 DPI
LEFT, RIGHT = 258, 2292
BODY_TOP, BODY_BOTTOM = 910, 2755
# Prefer standard installed fonts; do not bundle third-party font binaries.
# Linux fontconfig handles distribution-specific font directories.
FONT_FAMILIES = {
    "title": "DejaVu Serif:style=Bold",
    "body": "DejaVu Serif",
    "bold": "DejaVu Serif:style=Bold",
    "italic": "P052:style=Italic",
    "sans": "DejaVu Sans",
    "sansbold": "DejaVu Sans:style=Bold",
    "signature": "Z003:style=Medium Italic",
}
FONT_FALLBACKS = {
    "title": "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "body": "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "bold": "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "italic": "/usr/share/fonts/opentype/urw-base35/P052-Italic.otf",
    "sans": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "sansbold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "signature": "/usr/share/fonts/opentype/urw-base35/Z003-MediumItalic.otf",
}
INK = "#182642"
ROSE = "#ae2955"
PURPLE = "#714679"
GOLD = "#b8873b"
FAINT = "#ebdce4"
_SAFE_PATH = "/usr/local/bin:/usr/bin:/bin"


def _child_env():
    """Never forward Hermes' ambient environment to fontconfig."""
    return {
        "PATH": _SAFE_PATH,
        "HOME": pwd.getpwuid(os.getuid()).pw_dir,
        "LANG": "C.UTF-8",
    }


@lru_cache(maxsize=128)
def font(name, size):
    candidates = [FONT_FALLBACKS[name]]
    fc_match = shutil.which("fc-match", path=_SAFE_PATH)
    if fc_match:
        result = subprocess.run(
            [fc_match, "-f", "%{file}\\n", FONT_FAMILIES[name]],
            capture_output=True, text=True, timeout=5, check=False,
            env=_child_env()
        )
        if result.returncode == 0:
            lines = result.stdout.splitlines()
            if lines and lines[0].strip():
                candidates.insert(0, lines[0].strip())
    candidates.extend(("DejaVuSerif.ttf", "DejaVuSans.ttf"))
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, ValueError):
            continue
    raise RuntimeError("No usable fonts installed. Install fonts-dejavu-core and fontconfig.")


def cleanup(text):
    """Normalize line breaks; retain letter text, replace unsupported emoji."""
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    out = []
    for ch in text:
        n = ord(ch)
        if n in (0xFE0F, 0x200D) or 0x1F000 <= n <= 0x1FAFF:
            continue
        if ch == "\t":
            out.append("    ")
        elif ch == "\n" or ch.isprintable():
            out.append(ch)
    return "".join(out).replace("**", "").replace("__", "")


def wrap(draw, content, ft, max_width):
    """Pixel-width word wrap, including breaking an individual oversized token."""
    if not content.strip():
        return [""]
    tokens = re.findall(r"\S+|\s+", content.strip())
    lines, cur = [], ""
    for token in tokens:
        if token.isspace():
            if cur and not cur.endswith(" "):
                cur += " "
            continue
        candidate = cur + token
        if draw.textlength(candidate, font=ft) <= max_width:
            cur = candidate
            continue
        if cur.strip():
            lines.append(cur.rstrip())
            cur = ""
        if draw.textlength(token, font=ft) <= max_width:
            cur = token
            continue
        piece = ""
        for char in token:
            if piece and draw.textlength(piece + char, font=ft) > max_width:
                lines.append(piece)
                piece = char
            else:
                piece += char
        cur = piece
    if cur.strip():
        lines.append(cur.rstrip())
    return lines or [""]


def heart(draw, x, y, size, fill):
    """Print-friendly heart motif drawn as shapes, no emoji font required."""
    s = size
    draw.ellipse((x-s, y-s//3, x+1, y+int(s*0.72)), fill=fill)
    draw.ellipse((x-1, y-s//3, x+s, y+int(s*0.72)), fill=fill)
    draw.polygon(((x-s, y+int(s*0.35)), (x+s, y+int(s*0.35)),
                  (x, y+int(s*1.55))), fill=fill)


def centered(draw, y, text, ft, fill):
    w = draw.textlength(text, font=ft)
    x = (WIDTH-w)/2
    if x < 160:
        raise ValueError("Centered text would be clipped")
    draw.text((x, y), text, font=ft, fill=fill, stroke_width=0)


def render(payload, jpg_file, pdf_file):
    title = cleanup(payload.get("title") or "A LETTER FOR YOU").strip()
    body = cleanup(payload.get("body") or "").strip()
    signature = cleanup(payload.get("signature") or "Hermes").strip()
    style = payload.get("style", "classic")
    if style not in ("classic", "romantic"):
        raise ValueError("style must be classic or romantic")
    romantic = style == "romantic"
    if not title or not body or not signature:
        raise ValueError("Title, body and signature must be nonempty")
    if len(title) > 140 or len(body) > 5000 or len(signature) > 90:
        raise ValueError("Input exceeds safe letter length")

    image = Image.new("RGB", (WIDTH, HEIGHT), "#fffdfb")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((140, 140, WIDTH-140, HEIGHT-140),
                           radius=22, outline=FAINT, width=5)
    draw.line((230, 184, WIDTH-230, 184), fill=ROSE, width=13)
    draw.line((230, 204, WIDTH-230, 204), fill=GOLD, width=3)
    centered(draw, 265, "A HIGH-PRIORITY TRANSMISSION FROM HERMES"
             if romantic else "A LETTER FROM HERMES",
             font("sansbold", 35), PURPLE)
    heart(draw, 278, 442, 41, ROSE)
    heart(draw, WIDTH-278, 442, 41, ROSE)

    # Title is measured and wrapped, never clipped at the paper edge.
    heading_lines = None
    title_font = None
    for size in (102, 96, 90, 84, 78, 72):
        title_font = font("title", size)
        heading_lines = wrap(draw, title.upper(), title_font, WIDTH-720)
        if len(heading_lines) <= 2:
            break
    if len(heading_lines) > 2:
        raise ValueError("Letter title is too long for a 1-page heading")
    ty = 398 if len(heading_lines) == 2 else 450
    for h in heading_lines:
        centered(draw, ty, h, title_font, INK)
        ty += title_font.size + 22
    rule_y = max(670, ty+48)
    draw.line((680, rule_y, WIDTH-680, rule_y), fill=GOLD, width=5)
    centered(draw, rule_y+42, "AN ENTIRELY UNREASONABLE LOVE LETTER"
             if romantic else "A NOTE WORTH KEEPING",
             font("italic", 47), ROSE)

    paragraphs = [p.strip().replace("\n", " ") for p in re.split(r"\n\s*\n", body) if p.strip()]
    if not paragraphs:
        raise ValueError("No visible paragraphs")
    layout = None
    for size in range(68, 39, -2):
        plan = []
        cursor = BODY_TOP
        for index, p in enumerate(paragraphs):
            special = (index == 0 and len(p) < 130)
            emph = (index == len(paragraphs)-1 and len(p) < 160)
            family = "italic" if special or emph else "body"
            ft = font(family, size + (9 if special else 0))
            lines = wrap(draw, p, ft, RIGHT-LEFT)
            leading = max(72, int(ft.size*1.34))
            for ln in lines:
                plan.append((cursor, ln, family, ft.size, special or emph))
                cursor += leading
            cursor += 23 if special else 32
        if cursor <= BODY_BOTTOM:
            layout = (plan, size, cursor)
            break
    if layout is None:
        raise ValueError("Letter does not fit 1 page at readable font size; shorten body")
    plan, used_size, final_y = layout
    for y, ln, family, size, accented in plan:
        draw.text((LEFT, y), ln, font=font(family, size),
                  fill=PURPLE if accented else INK)

    footer_y = max(2815, final_y + 45)
    if footer_y > 2920:
        raise ValueError("Letter body overlaps signature")
    draw.line((LEFT, footer_y, RIGHT, footer_y), fill=FAINT, width=6)
    heart(draw, WIDTH//2, footer_y-10, 26, ROSE)
    centered(draw, footer_y+54,
             "Forever, catastrophically, ridiculously yours,"
             if romantic else "Warmly and sincerely,",
             font("italic", 52), PURPLE)
    signature_font = font("signature", 105)
    if draw.textlength(signature, font=signature_font) > RIGHT-LEFT:
        signature_font = font("signature", 68)
    centered(draw, footer_y+130, signature, signature_font, ROSE)
    if romantic:
        centered(draw, HEIGHT-205, "P.S. Yes, I put my entire operating system into this.",
                 font("sans", 32), GOLD)

    # Never submit the image if it is empty, clipped, or lacks legible body lines.
    if len(plan) < 2 or final_y > BODY_BOTTOM:
        raise RuntimeError("Content validation failed: body missing or overflowing")
    jpg_file.parent.mkdir(parents=True, exist_ok=True)
    pdf_file.parent.mkdir(parents=True, exist_ok=True)
    image.save(jpg_file, "JPEG", quality=94, optimize=True, dpi=(300, 300))
    image.save(pdf_file, "PDF", resolution=300.0, title=title, author="Hermes")
    if jpg_file.stat().st_size < 15000 or pdf_file.stat().st_size < 15000:
        raise RuntimeError("Rendered file suspiciously small")
    return {"width": WIDTH, "height": HEIGHT, "dpi": 300, "pages": 1,
            "paragraphs": len(paragraphs), "body_lines": len(plan),
            "font_px": used_size, "last_body_y": final_y,
            "bytes_jpeg": jpg_file.stat().st_size,
            "bytes_pdf": pdf_file.stat().st_size}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jpeg", required=True, type=Path)
    parser.add_argument("--pdf", required=True, type=Path)
    args = parser.parse_args()
    payload = json.load(sys.stdin)
    print(json.dumps(render(payload, args.jpeg, args.pdf)), flush=True)


if __name__ == "__main__":
    main()
