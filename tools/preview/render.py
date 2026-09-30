#!/usr/bin/env python3
"""Render theme previews: contact sheet PNG, per-theme stills and one animated GIF per theme.

usage: render.py [theme|all] [--frames N] [--fps F] [--mood M] [--columns C] [--scale S]
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
    ap.add_argument("--columns", type=int, default=3, help="contact-sheet columns")
    ap.add_argument("--scale", type=float, default=0.5, help="contact-sheet cell scale")
    ap.add_argument("--stills", action="store_true", help="also save a full-size PNG per theme")
    ap.add_argument("--action", default="",
                    help="fire an action 1/5 of the way in: blink, wink_left, wink_right, startle, roll, look")
    args = ap.parse_args()
    tag = args.mood + (f"_{args.action}" if args.action else "")

    subprocess.run([os.path.join(HERE, "build.sh")], check=True)
    out = os.path.join(HERE, "out")
    os.makedirs(out, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([os.path.join(HERE, "build", "preview"), args.theme, tmp,
                        str(args.frames), str(args.fps), args.mood, args.action], check=True)
        themes = sorted({os.path.basename(p).rsplit("_", 1)[0] for p in glob.glob(f"{tmp}/*.ppm")})
        stills = []
        for theme in themes:
            frames = [Image.open(p).convert("RGB") for p in sorted(glob.glob(f"{tmp}/{theme}_*.ppm"))]
            if len(frames) > 1:
                frames[0].save(os.path.join(out, f"{theme}_{tag}.gif"), save_all=True,
                               append_images=frames[1:], duration=int(1000 / args.fps), loop=0)
            still = frames[len(frames) // 2]
            if args.stills:
                still.save(os.path.join(out, f"{theme}_{tag}.png"))
            stills.append((theme, still))
        w, h = stills[0][1].size
        cw, ch = int(w * args.scale), int(h * args.scale)
        cols = max(1, min(args.columns, len(stills)))
        rows = (len(stills) + cols - 1) // cols
        pad, label = 8, 18
        sheet = Image.new("RGB", (cols * (cw + pad) + pad, rows * (ch + label + pad) + pad), (24, 24, 24))
        draw = ImageDraw.Draw(sheet)
        for i, (theme, img) in enumerate(stills):
            x = pad + (i % cols) * (cw + pad)
            y = pad + (i // cols) * (ch + label + pad)
            draw.text((x + 2, y + 2), f"{theme} ({tag})", fill=(220, 220, 220))
            sheet.paste(img.resize((cw, ch), Image.LANCZOS), (x, y + label))
        path = os.path.join(out, f"sheet_{args.theme}_{tag}.png")
        sheet.save(path)
        print(path)


if __name__ == "__main__":
    main()
