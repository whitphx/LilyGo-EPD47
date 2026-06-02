#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["qrcode>=7", "pillow>=10", "freetype-py>=2.4", "resvg-py>=0.1"]
# ///
"""Generate QR, name-font, logo, and projects headers for the nametag example.

Run with `uv` so the deps are fetched on the fly:

    uv run generate_assets.py

GitHub project metadata (stars, descriptions) is fetched from the public API
at script time and baked into projects_data.h — no WiFi needed on the device.
"""

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import freetype
from PIL import Image, ImageDraw, ImageFont, ImageOps
import qrcode


def _safe_identifier(name: str) -> str:
    return name.replace("-", "_").replace(" ", "_")


def write_image_header(img: Image.Image, name: str, output_path: Path) -> None:
    """Serialize a grayscale PIL image to the EPD driver's 4-bit-per-pixel header format."""
    img = img.convert("L")
    width, height = img.size

    if width % 2:
        padded = Image.new("L", (width + 1, height), 255)
        padded.paste(img, (0, 0))
        img = padded
        width = img.width

    ident = _safe_identifier(name)
    pixels = img.load()

    with output_path.open("w") as f:
        f.write("#pragma once\n")
        f.write("#include <stdint.h>\n\n")
        f.write(f"const uint32_t {ident}_width = {width};\n")
        f.write(f"const uint32_t {ident}_height = {height};\n")
        f.write(f"const uint8_t {ident}_data[({width}*{height})/2] = {{\n")

        for y in range(height):
            f.write("    ")
            byte = 0
            for x in range(width):
                l = pixels[x, y]
                if x % 2 == 0:
                    byte = l >> 4
                else:
                    byte |= l & 0xF0
                    f.write(f"0x{byte:02X}, ")
            f.write("\n")
        f.write("};\n")


def generate_qrcode(
    url: str,
    output_path: Path,
    name: str = "qrcode",
    box_size: int | None = None,
    border: int = 2,
    target_px: int = 232,
    ecc: str = "M",
) -> None:
    error_correction = _ECC_LEVELS[ecc]
    if box_size is None:
        # Pick the largest integer box_size at which (modules + 2*border)*box_size
        # stays within target_px, so QRs encoding different-length URLs end up
        # close to the same width.
        probe = qrcode.QRCode(
            version=None, error_correction=error_correction, box_size=1, border=border,
        )
        probe.add_data(url)
        probe.make(fit=True)
        total_modules = probe.modules_count + 2 * border
        box_size = max(2, target_px // total_modules)

    qr = qrcode.QRCode(
        version=None, error_correction=error_correction, box_size=box_size, border=border,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("L")
    write_image_header(img, name, output_path)
    print(f"  qr     ({img.size[0]:>4}x{img.size[1]:<4}) {url!r} -> {output_path.name}")


def generate_placeholder_logo(label: str, output_path: Path, name: str, height: int = 60) -> None:
    width = height * 2
    img = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, width - 1, height - 1], outline=0, width=2)
    font = _load_bold_font(height // 2)
    bbox = draw.textbbox((0, 0), label, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text(
        ((width - tw) / 2 - bbox[0], (height - th) / 2 - bbox[1]),
        label,
        fill=0,
        font=font,
    )
    write_image_header(img, name, output_path)
    print(f"  logo   ({width:>4}x{height:<4}) placeholder '{label}' -> {output_path.name}")


def _load_bold_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _rasterize_svg(input_path: Path, target_height: int) -> Image.Image:
    # Rasterize well above the target height so the downscale stays sharp.
    oversampled_h = target_height * 4

    try:
        import resvg_py
    except ImportError:
        resvg_py = None

    if resvg_py is not None:
        # resvg honors the SVG viewbox and preserves transparency, so the alpha
        # bbox lines up with the real content. Pass svg_string (svg_path is
        # currently broken upstream for nested files).
        svg = input_path.read_text(encoding="utf-8")
        png_bytes = bytes(resvg_py.svg_to_bytes(svg_string=svg, height=oversampled_h))
        img = Image.open(io.BytesIO(png_bytes))
    elif shutil.which("rsvg-convert"):
        result = subprocess.run(
            [shutil.which("rsvg-convert"), "-h", str(oversampled_h), str(input_path)],
            capture_output=True, check=True,
        )
        img = Image.open(io.BytesIO(result.stdout))
    elif shutil.which("qlmanage"):
        # qlmanage writes "{input.name}.png" inside the output dir, always as
        # a SIZE×SIZE thumbnail with a white background. It also tends to clip
        # SVGs with off-viewport transforms — prefer resvg/rsvg-convert.
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(
                [shutil.which("qlmanage"), "-t", "-s", str(oversampled_h),
                 "-o", td, str(input_path)],
                capture_output=True, check=True,
            )
            png_path = Path(td) / f"{input_path.name}.png"
            with Image.open(png_path) as raw:
                img = raw.copy()
    else:
        raise SystemExit(
            f"Cannot rasterize {input_path.name}: install resvg-py "
            "(automatic via `uv run`) or rsvg-convert."
        )

    return _crop_to_content(img)


def _crop_to_content(img: Image.Image) -> Image.Image:
    full = (0, 0, *img.size)
    bbox = None
    if img.mode in ("RGBA", "LA"):
        alpha_bbox = img.split()[-1].getbbox()
        # qlmanage paints alpha=255 everywhere, so a full-canvas alpha bbox
        # means alpha is useless and we have to detect content by darkness.
        if alpha_bbox and alpha_bbox != full:
            bbox = alpha_bbox
    if bbox is None:
        bbox = ImageOps.invert(_flatten_to_grayscale(img)).getbbox()
    return img.crop(bbox) if bbox and bbox != full else img


def _load_logo_source(input_path: Path, target_height: int) -> Image.Image:
    if input_path.suffix.lower() == ".svg":
        return _rasterize_svg(input_path, target_height)
    return Image.open(input_path)


def _flatten_to_grayscale(img: Image.Image) -> Image.Image:
    # Promote palette-with-transparency and grayscale-with-alpha to RGBA so the
    # alpha channel survives the conversion to L.
    if "transparency" in img.info or img.mode in ("P", "PA", "LA"):
        img = img.convert("RGBA")
    if img.mode == "RGBA":
        bg = Image.new("L", img.size, 255)
        bg.paste(img.convert("L"), mask=img.split()[-1])
        return bg
    return img.convert("L")


def _resize_to_height(img: Image.Image, target_height: int) -> Image.Image:
    w, h = img.size
    if h == target_height:
        new_w = w
    else:
        new_w = max(2, round(w * target_height / h))
    # EPD framebuffer packs two pixels per byte; even widths avoid a stray
    # padding nibble per row.
    if new_w % 2:
        new_w += 1
    return img.resize((new_w, target_height), Image.LANCZOS)


def convert_logo(input_path: Path, output_path: Path, name: str, target_height: int) -> None:
    img = _load_logo_source(input_path, target_height)
    img = _flatten_to_grayscale(img)
    img = _resize_to_height(img, target_height)
    write_image_header(img, name, output_path)
    print(f"  logo   ({img.size[0]:>4}x{img.size[1]:<4}) {input_path.name} -> {output_path.name}")


LOGOS = [
    # (header_name, stem in assets/, placeholder_label, scale_vs_base_height)
    # Scales compensate for differing internal padding so the marks read at
    # roughly the same visual height on the e-paper.
    ("streamlit_logo", "streamlit", "S", 2.0),
    ("stlite_logo",    "stlite",    "L", 1.2),
]
LOGO_EXTENSIONS = (".png", ".svg")


def find_logo_asset(assets_dir: Path, stem: str) -> Path | None:
    for ext in LOGO_EXTENSIONS:
        path = assets_dir / f"{stem}{ext}"
        if path.is_file():
            return path
    return None

BOLD_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]

# Matches the DPI fontconvert.py passes to FreeType.
FONT_RENDER_DPI = 150


def _measure_text_width_px(face: freetype.Face, text: str) -> int:
    total = 0
    for ch in text:
        face.load_char(ch, freetype.FT_LOAD_DEFAULT)
        total += face.glyph.advance.x >> 6
    return total


def find_max_font_size_pt(font_path: Path, text: str, max_width_px: int,
                          min_size: int = 12, max_size: int = 48) -> int:
    """Largest point size (in fontconvert.py's units) at which `text` fits `max_width_px`."""
    face = freetype.Face(str(font_path))
    for size in range(max_size, min_size - 1, -1):
        face.set_char_size(size << 6, size << 6, FONT_RENDER_DPI, FONT_RENDER_DPI)
        if _measure_text_width_px(face, text) <= max_width_px:
            return size
    return min_size


def find_bold_font(override: Path | None) -> Path | None:
    candidates = [override] + [Path(p) for p in BOLD_FONT_CANDIDATES]
    for path in candidates:
        if path and path.is_file():
            return path
    return None


# --- GitHub project fetch ---------------------------------------------------

QR_LINKS = [
    # (ident_suffix, url, target_px, ecc)
    # ECC "L" (7% error correction) lets the longer repo URLs fit in a smaller
    # QR version, so per-project QRs end up at the same modules-count + box_size
    # and therefore the same final width.
    ("site",     "https://whitphx.info/",                       232, "M"),
    ("github",   "https://github.com/whitphx",                  232, "M"),
    ("linkedin", "https://www.linkedin.com/in/whitphx/",        232, "M"),
    ("stlite",   "https://github.com/whitphx/stlite",           170, "L"),
    ("webrtc",   "https://github.com/whitphx/streamlit-webrtc", 170, "L"),
]

_ECC_LEVELS = {
    "L": qrcode.constants.ERROR_CORRECT_L,
    "M": qrcode.constants.ERROR_CORRECT_M,
    "Q": qrcode.constants.ERROR_CORRECT_Q,
    "H": qrcode.constants.ERROR_CORRECT_H,
}

# Display name -> GitHub repo (owner/name). Descriptions are overridden here
# so we don't depend on repo description text drifting.
PROJECTS = [
    ("Stlite",           "whitphx/stlite",           "In-browser Streamlit"),
    ("Streamlit-WebRTC", "whitphx/streamlit-webrtc", "Real-time A/V on Streamlit"),
]


def _github_get(path: str) -> object:
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "nametag-generate-assets",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _fetch_user_repos(user: str) -> list[dict]:
    repos: list[dict] = []
    page = 1
    while True:
        chunk = _github_get(f"/users/{user}/repos?per_page=100&page={page}")
        if not chunk:
            break
        repos.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
    return repos


def _format_stars(n: int) -> str:
    if n >= 10000:
        return f"{n // 1000}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def gather_projects() -> list[dict]:
    rows: list[dict] = []
    for display, repo_path, desc in PROJECTS:
        data = _github_get(f"/repos/{repo_path}")
        rows.append({
            "name": display,
            "desc": desc,
            "stars": _format_stars(data.get("stargazers_count", 0)),
        })
    return rows


def write_projects_header(output_path: Path) -> None:
    rows = gather_projects()

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    with output_path.open("w") as f:
        f.write("#pragma once\n")
        f.write("#include <stddef.h>\n\n")
        f.write("struct ProjectRow {\n")
        f.write("    const char* name;\n")
        f.write("    const char* desc;\n")
        f.write("    const char* stars;\n")
        f.write("};\n\n")
        f.write("const ProjectRow projects[] = {\n")
        for r in rows:
            f.write(f'    {{ "{esc(r["name"])}", "{esc(r["desc"])}", "{esc(r["stars"])}" }},\n')
        f.write("};\n")
        f.write(f"const size_t projects_count = {len(rows)};\n")

    print(f"  proj  ({len(rows)} projects) -> {output_path.name}")
    for r in rows:
        print(f"          {r['name']:24} {r['stars']:>6}  {r['desc']}")


def generate_font_header(font_path: Path, ident: str, size_pt: int, output_path: Path) -> None:
    script_dir = Path(__file__).resolve().parent
    # scripts/fontconvert.py lives at the repo root, two parents up from examples/nametag/.
    fontconvert = script_dir.parent.parent / "scripts" / "fontconvert.py"
    if not fontconvert.is_file():
        raise SystemExit(f"fontconvert.py not found at {fontconvert}")
    cmd = [
        sys.executable, str(fontconvert),
        "--compress",
        ident, str(size_pt),
        str(font_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    output_path.write_text(result.stdout)
    print(f"  font   ({size_pt:>3}pt) '{ident}' from {font_path.name} -> {output_path.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--qr-border", type=int, default=2, help="Quiet-zone width in QR modules.")
    parser.add_argument("--logo-height", type=int, default=60,
                        help="Target height (px) for each logo. Width follows from source aspect ratio.")
    parser.add_argument("--assets-dir", type=Path, default=None, help="Where to look for real logo PNGs.")
    parser.add_argument("--out-dir", type=Path, default=None, help="Where to write the generated headers.")
    parser.add_argument("--name-text", default="Yuichiro Tachibana",
                        help="Text used to auto-size the bold name font so it fits the column.")
    parser.add_argument("--name-font", type=Path, default=None,
                        help="Bold TTF/OTF for the name. Defaults to Arial Bold / DejaVu Sans Bold.")
    parser.add_argument("--name-font-size", type=int, default=None,
                        help="Override the auto-detected point size for the name font.")
    parser.add_argument("--name-max-width", type=int, default=568,
                        help="Pixel budget the name has to fit in (drives auto-sizing).")
    parser.add_argument("--skip-github", action="store_true",
                        help="Don't fetch GitHub data (keep the existing projects_data.h).")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    out_dir = (args.out_dir or script_dir).resolve()
    assets_dir = (args.assets_dir or (script_dir / "assets")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Writing headers to {out_dir}")
    for suffix, url, target_px, ecc in QR_LINKS:
        generate_qrcode(
            url,
            out_dir / f"qrcode_{suffix}.h",
            name=f"qrcode_{suffix}",
            target_px=target_px,
            border=args.qr_border,
            ecc=ecc,
        )

    for header_name, stem, placeholder_label, scale in LOGOS:
        src_path = find_logo_asset(assets_dir, stem)
        h_path = out_dir / f"{header_name}.h"
        target_h = max(2, int(round(args.logo_height * scale)))
        if src_path is not None:
            convert_logo(src_path, h_path, header_name, target_height=target_h)
        else:
            generate_placeholder_logo(placeholder_label, h_path, header_name, height=target_h)

    if not args.skip_github:
        try:
            write_projects_header(out_dir / "projects_data.h")
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            print(f"  proj  skipped: GitHub fetch failed ({e}). Keep existing projects_data.h "
                  "or rerun with network access.")

    bold_font = find_bold_font(args.name_font)
    if bold_font is None:
        print("  font   skipped: no bold TTF found. Pass --name-font /path/to/Bold.ttf.")
        return

    size_pt = args.name_font_size or find_max_font_size_pt(
        bold_font, args.name_text, args.name_max_width
    )
    generate_font_header(bold_font, "NameFontBold", size_pt, out_dir / "name_font.h")


if __name__ == "__main__":
    main()
