# Nametag

A self-introduction name tag for the LilyGo EPD47 (4.7" e-paper). Wear it on a lanyard at conferences and meetups — name, online handle, QR code linking to your site, and a small row of project icons.

## Layout

```
+---------------------------------------------------+
|                                                   |
|   Yuichiro Tachibana          +-----------+       |
|                               |           |       |
|   @whitphx                    |  QR code  |       |
|                               |           |       |
|   https://whitphx.info/       +-----------+       |
|                                                   |
|   [Streamlit] [stlite]                            |
|                                                   |
+---------------------------------------------------+
```

After drawing once, the sketch puts the ESP32-S3 into deep sleep. E-paper retains the image without power. Press the **BOOT (IO0)** button to redraw (useful after you swap assets).

## Building

In `platformio.ini`, make sure `src_dir = examples/nametag` is uncommented (the other `src_dir =` lines must stay commented). Then:

```sh
pio run -e T5-ePaper-S3 -t upload
```

## Customizing the content

### QR code

The QR code is **pre-generated at build time** into `qrcode_data.h`. The default target is `https://whitphx.info/`. To regenerate:

```sh
uv run generate_assets.py --url https://example.com/
```

`generate_assets.py` has a PEP 723 inline-deps header, so `uv run` fetches `qrcode` and `Pillow` automatically — no virtualenv setup required.

Tunables:
- `--qr-box-size N` — pixels per QR module (default 8).
- `--qr-border N`  — quiet-zone width in modules (default 2).

### Project icons (Streamlit, stlite)

Drop the real icon PNGs into `./assets/`:

```
assets/
  streamlit.png
  stlite.png
```

Then rerun:

```sh
uv run generate_assets.py
```

This produces `streamlit_logo.h` and `stlite_logo.h`. Until you supply the PNGs, the script writes 80×80 boxed-letter placeholders so the layout is visible.

Use `--logo-size N` to change the max edge length (default 80px).

### Name, handle, URL text

Edit the string constants at the top of `nametag.ino`:

```c
static const char NAME_TEXT[]   = "Yuichiro Tachibana";
static const char HANDLE_TEXT[] = "@whitphx";
static const char URL_TEXT[]    = "https://whitphx.info/";
```

Then rebuild and upload.

### Bold name font

The name is rendered with `NameFontBold`, generated from a system bold TTF (Arial Bold on macOS, DejaVu Sans Bold on Linux) via `scripts/fontconvert.py`. The generator picks the largest point size at which `NAME_TEXT` still fits the available column width.

If you change the name to something noticeably longer/shorter, rerun:

```sh
uv run generate_assets.py --name-text "Your New Name"
```

Overrides:
- `--name-font /path/to/Bold.ttf` — use a specific bold typeface.
- `--name-font-size 32` — skip auto-sizing and force a point size.
- `--name-max-width 568` — change the pixel budget the name has to fit in.

## Files

| File                  | Purpose                                                                |
| --------------------- | ---------------------------------------------------------------------- |
| `nametag.ino`         | Main sketch — draws the layout, sleeps until BOOT-button wake.         |
| `generate_assets.py`  | One-shot generator for QR + name-font + logo headers.                  |
| `qrcode_data.h`       | Generated QR-code bitmap (4-bit grayscale).                            |
| `name_font.h`         | Generated bold font for the name, auto-sized to fit the column.        |
| `streamlit_logo.h`    | Generated Streamlit icon bitmap (real PNG → header, or placeholder).   |
| `stlite_logo.h`       | Generated stlite icon bitmap (real PNG → header, or placeholder).      |
| `assets/`             | Drop your icon PNGs here for the generator to pick up.                 |
