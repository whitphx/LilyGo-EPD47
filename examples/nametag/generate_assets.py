#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["qrcode>=7", "pillow>=10", "freetype-py>=2.4"]
# ///
"""Generate qrcode_data.h, name_font.h, and logo header files for the nametag example.

Run with `uv` so the deps are fetched on the fly:

    uv run generate_assets.py

To change the QR target or logo size, see --help. Drop streamlit.png /
stlite.png into ./assets to swap the placeholder logos for the real ones.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

import freetype
from PIL import Image, ImageDraw, ImageFont
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
    box_size: int = 8,
    border: int = 2,
) -> None:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("L")
    write_image_header(img, name, output_path)
    print(f"  qr     ({img.size[0]:>4}x{img.size[1]:<4}) {url!r} -> {output_path.name}")


def generate_placeholder_logo(label: str, output_path: Path, name: str, size: int = 80) -> None:
    img = Image.new("L", (size, size), 255)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, size - 1, size - 1], outline=0, width=2)
    font = _load_bold_font(size // 2)
    bbox = draw.textbbox((0, 0), label, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text(
        ((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]),
        label,
        fill=0,
        font=font,
    )
    write_image_header(img, name, output_path)
    print(f"  logo   ({size:>4}x{size:<4}) placeholder '{label}' -> {output_path.name}")


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


def convert_png(input_path: Path, output_path: Path, name: str, max_size: int) -> None:
    img = Image.open(input_path)
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("L", img.size, 255)
        bg.paste(img.convert("L"), mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("L")
    img.thumbnail((max_size, max_size), Image.LANCZOS)
    write_image_header(img, name, output_path)
    print(f"  logo   ({img.size[0]:>4}x{img.size[1]:<4}) {input_path.name} -> {output_path.name}")


LOGOS = [
    ("streamlit_logo", "streamlit.png", "S"),
    ("stlite_logo", "stlite.png", "L"),
]

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
    parser.add_argument("--url", default="https://whitphx.info/", help="URL encoded into the QR code.")
    parser.add_argument("--qr-box-size", type=int, default=8, help="Pixels per QR module.")
    parser.add_argument("--qr-border", type=int, default=2, help="Quiet-zone width in QR modules.")
    parser.add_argument("--logo-size", type=int, default=80, help="Max edge length of each logo in pixels.")
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
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    out_dir = (args.out_dir or script_dir).resolve()
    assets_dir = (args.assets_dir or (script_dir / "assets")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Writing headers to {out_dir}")
    generate_qrcode(
        args.url,
        out_dir / "qrcode_data.h",
        box_size=args.qr_box_size,
        border=args.qr_border,
    )

    for header_name, png_name, placeholder_label in LOGOS:
        png_path = assets_dir / png_name
        h_path = out_dir / f"{header_name}.h"
        if png_path.is_file():
            convert_png(png_path, h_path, header_name, max_size=args.logo_size)
        else:
            generate_placeholder_logo(placeholder_label, h_path, header_name, size=args.logo_size)

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
