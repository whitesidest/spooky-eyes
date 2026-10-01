"""Render a thumbnail of every firmware theme into devices/static/devices/themes/.

Uses tools/preview (the real renderer, built with g++ on demand) so the pictures are exactly what
the boards draw. Each theme gets:

  <id>.png     both eyes, 500x240, alpha outside the round panels (floats on any background)
  <id>_s.png   the same at 250x120 for grids
  <id>.gif     a short animated loop at 250x120 (only when Pillow is installed; optional)

plus themes.json listing what was rendered. Re-run after changing firmware/src/render/themes.cpp:

    manage.py render_theme_thumbs [--frames 40] [--no-gif] [--mood neutral]
"""
from __future__ import annotations

import glob
import json
import os
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

APP_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = APP_DIR.parents[1]
PREVIEW_DIR = REPO_DIR / "tools" / "preview"
OUT_DIR = APP_DIR / "static" / "devices" / "themes"

EYE = 240  # panel size; the preview writes <eye><gap><eye>


def read_ppm(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    parts = data.split(maxsplit=4)  # P6 w h 255 <raw>
    w, h = int(parts[1]), int(parts[2])
    raw = data[len(data) - w * h * 3:]
    return w, h, raw


def circle_alpha(w: int, h: int) -> bytes:
    """Alpha plane: opaque inside the two round panels (centred in each 240 block), soft 1px edge."""
    gap = w - 2 * EYE
    r = EYE / 2
    alpha = bytearray(w * h)
    centers = (r, EYE + gap + r)
    for y in range(h):
        dy = y + 0.5 - r
        for cx in centers:
            x0 = int(cx - r)
            for x in range(x0, x0 + EYE):
                dx = x + 0.5 - cx
                d = (dx * dx + dy * dy) ** 0.5
                if d <= r - 1:
                    alpha[y * w + x] = 255
                elif d < r:
                    alpha[y * w + x] = int(255 * (r - d))
    return bytes(alpha)


def to_rgba(w: int, h: int, rgb: bytes, alpha: bytes) -> bytes:
    out = bytearray(w * h * 4)
    out[0::4] = rgb[0::3]
    out[1::4] = rgb[1::3]
    out[2::4] = rgb[2::3]
    out[3::4] = alpha
    return bytes(out)


def downscale2(w: int, h: int, rgba: bytes) -> tuple[int, int, bytes]:
    """2x box filter (premultiplied so edges don't darken)."""
    w2, h2 = w // 2, h // 2
    out = bytearray(w2 * h2 * 4)
    for y in range(h2):
        r0 = (2 * y) * w * 4
        r1 = r0 + w * 4
        for x in range(w2):
            o = (y * w2 + x) * 4
            i0, i1 = r0 + x * 8, r1 + x * 8
            px = (rgba[i0:i0 + 4], rgba[i0 + 4:i0 + 8], rgba[i1:i1 + 4], rgba[i1 + 4:i1 + 8])
            a = sum(p[3] for p in px)
            if a:
                for c in range(3):
                    out[o + c] = sum(p[c] * p[3] for p in px) // a
            out[o + 3] = a // 4
    return w2, h2, bytes(out)


def write_png(path: Path, w: int, h: int, rgba: bytes) -> None:
    stride = w * 4
    raw = b"".join(b"\x00" + rgba[y * stride:(y + 1) * stride] for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def write_gif(path: Path, frames: list[tuple[int, int, bytes]], fps: float) -> bool:
    try:
        from PIL import Image
    except ImportError:
        return False
    images = []
    for w, h, rgba in frames:
        im = Image.frombytes("RGBA", (w, h), rgba)
        images.append(im.convert("RGB").quantize(colors=128, method=Image.Quantize.FASTOCTREE))
    images[0].save(path, save_all=True, append_images=images[1:], duration=int(1000 / fps), loop=0,
                   optimize=True, disposal=0)
    return True


class Command(BaseCommand):
    help = "Render thumbnails of every eye theme into devices/static/devices/themes/ (needs g++)."

    def add_arguments(self, parser):
        parser.add_argument("--frames", type=int, default=40, help="frames to simulate; the still is taken mid-way")
        parser.add_argument("--fps", type=float, default=20.0)
        parser.add_argument("--mood", default="neutral")
        parser.add_argument("--no-gif", action="store_true", help="skip the animated previews")
        parser.add_argument("--out", default=str(OUT_DIR))

    def handle(self, *args, frames, fps, mood, no_gif, out, **options):
        out_dir = Path(out)
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run([str(PREVIEW_DIR / "build.sh")], check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError) as err:
            raise CommandError(f"could not build tools/preview (is g++ installed?): {err}") from err
        manifest = {"themes": [], "sizes": {"png": [2 * EYE + 20, EYE], "small": [EYE + 10, EYE // 2]}, "gif": False}
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([str(PREVIEW_DIR / "build" / "preview"), "all", tmp, str(frames), str(fps), mood],
                           check=True, capture_output=True)
            ids = sorted({Path(p).name.rsplit("_", 1)[0] for p in glob.glob(f"{tmp}/*.ppm")})
            alpha = None
            for theme in ids:
                paths = sorted(glob.glob(f"{tmp}/{theme}_*.ppm"))
                w, h, rgb = read_ppm(Path(paths[len(paths) // 2]))
                if alpha is None:
                    alpha = circle_alpha(w, h)
                full = to_rgba(w, h, rgb, alpha)
                write_png(out_dir / f"{theme}.png", w, h, full)
                write_png(out_dir / f"{theme}_s.png", *downscale2(w, h, full))
                entry = {"id": theme, "png": f"{theme}.png", "small": f"{theme}_s.png"}
                if not no_gif:
                    small = [downscale2(w, h, to_rgba(w, h, read_ppm(Path(p))[2], alpha)) for p in paths[::2]]
                    if write_gif(out_dir / f"{theme}.gif", small, fps / 2):
                        entry["gif"] = f"{theme}.gif"
                        manifest["gif"] = True
                manifest["themes"].append(entry)
                self.stdout.write(f"{theme:14s} {'gif ' if 'gif' in entry else ''}ok")
        for stale in out_dir.glob("*"):  # drop pictures of themes that no longer exist
            stem = stale.stem[:-2] if stale.stem.endswith("_s") else stale.stem
            if stale.suffix in (".png", ".gif") and stem not in ids:
                stale.unlink()
        (out_dir / "themes.json").write_text(json.dumps(manifest, indent=1) + "\n")
        self.stdout.write(self.style.SUCCESS(f"{len(ids)} themes -> {out_dir}"))
