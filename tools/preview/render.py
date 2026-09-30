#!/usr/bin/env python3
"""Render theme previews: contact sheet PNG + one animated GIF per theme.

usage: render.py [theme|all] [--frames N] [--fps F] [--mood M]
"""
import argparse
import glob
import os
import subprocess
import tempfile

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("theme", nargs="?", default="all")
    ap.add_argument("--frames", type=int, default=60)
    ap.add_argument("--fps", type=float, default=20)
    ap.add_argument("--mood", default="neutral")
    args = ap.parse_args()

    subprocess.run([os.path.join(HERE, "build.sh")], check=True)
    out = os.path.join(HERE, "out")
    os.makedirs(out, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([os.path.join(HERE, "build", "preview"), args.theme, tmp,
                        str(args.frames), str(args.fps), args.mood], check=True)
        themes = sorted({os.path.basename(p).rsplit("_", 1)[0] for p in glob.glob(f"{tmp}/*.ppm")})
        stills = []
        for theme in themes:
            frames = [Image.open(p).convert("RGB") for p in sorted(glob.glob(f"{tmp}/{theme}_*.ppm"))]
            if len(frames) > 1:
                frames[0].save(os.path.join(out, f"{theme}_{args.mood}.gif"), save_all=True,
                               append_images=frames[1:], duration=int(1000 / args.fps), loop=0)
            stills.append((theme, frames[len(frames) // 2]))
        w, h = stills[0][1].size
        sheet = Image.new("RGB", (w, (h + 28) * len(stills)), (24, 24, 24))
        draw = ImageDraw.Draw(sheet)
        for i, (theme, img) in enumerate(stills):
            y = i * (h + 28)
            draw.text((6, y + 8), f"{theme} ({args.mood})", fill=(220, 220, 220))
            sheet.paste(img, (0, y + 28))
        path = os.path.join(out, f"sheet_{args.theme}_{args.mood}.png")
        sheet.save(path)
        print(path)


if __name__ == "__main__":
    main()
