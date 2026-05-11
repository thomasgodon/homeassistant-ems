"""Render EMS brand assets: icon.png, icon@2x.png, logo.png, logo@2x.png."""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

GOLD = "#F59E0B"
SLATE = "#1F2937"
GREEN = "#34D399"   # battery
BLUE = "#60A5FA"    # charging plug
OUT = Path(__file__).parent

SCALE = 4  # supersample factor for anti-aliasing


def load_font(px: int) -> ImageFont.ImageFont:
    for name in ("seguibl.ttf", "segoeuib.ttf", "arialbd.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default(size=px)


def draw_ray(draw: ImageDraw.ImageDraw, cx: int, cy: int, disc_r: int, angle_deg: float, ray_len: int, ray_w: int) -> None:
    rad = math.radians(angle_deg)
    gap = int(disc_r * 0.18)
    x0 = cx + (disc_r + gap) * math.cos(rad)
    y0 = cy + (disc_r + gap) * math.sin(rad)
    x1 = cx + (disc_r + gap + ray_len) * math.cos(rad)
    y1 = cy + (disc_r + gap + ray_len) * math.sin(rad)
    draw.line([(x0, y0), (x1, y1)], fill=SLATE, width=ray_w)


def draw_battery(draw: ImageDraw.ImageDraw, cx: float, cy: float, size: int, color: str) -> None:
    """Vertical battery silhouette: body (rounded rect) + terminal nub at top."""
    bw = int(size * 0.72)
    bh = int(size * 1.35)
    nub_w = int(bw * 0.36)
    nub_h = int(size * 0.18)
    corner = max(3, int(bw * 0.20))
    x0, y0 = int(cx - bw / 2), int(cy - bh / 2)
    x1, y1 = int(cx + bw / 2), int(cy + bh / 2)
    draw.rounded_rectangle([x0, y0, x1, y1], radius=corner, fill=color)
    nx0 = int(cx - nub_w / 2)
    nx1 = int(cx + nub_w / 2)
    draw.rectangle([nx0, y0 - nub_h, nx1, y0 + 2], fill=color)


def draw_plug(draw: ImageDraw.ImageDraw, cx: float, cy: float, size: int, color: str) -> None:
    """Charging plug: two prongs on top + rounded connector body below."""
    bw = int(size * 0.88)
    bh = int(size * 0.80)
    prong_w = int(bw * 0.26)
    prong_h = int(size * 0.42)
    prong_gap = int(bw * 0.28)
    corner = max(3, int(bw * 0.18))

    # body (below prongs)
    body_top = int(cy - bh / 2 + prong_h * 0.55)
    body_bot = int(cy + bh / 2)
    draw.rounded_rectangle(
        [int(cx - bw / 2), body_top, int(cx + bw / 2), body_bot],
        radius=corner, fill=color,
    )

    # left prong
    lx = int(cx - prong_gap / 2 - prong_w)
    draw.rounded_rectangle(
        [lx, int(cy - bh / 2), lx + prong_w, body_top + prong_h // 4],
        radius=max(2, prong_w // 4), fill=color,
    )
    # right prong
    rx = int(cx + prong_gap / 2)
    draw.rounded_rectangle(
        [rx, int(cy - bh / 2), rx + prong_w, body_top + prong_h // 4],
        radius=max(2, prong_w // 4), fill=color,
    )


def render_icon(size: int) -> Image.Image:
    big = size * SCALE
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    margin = int(big * 0.08)
    corner = int(big * 0.22)
    d.rounded_rectangle([margin, margin, big - margin, big - margin], radius=corner, fill=GOLD)

    cx, cy = big // 2, big // 2
    disc_r = int(big * 0.175)
    d.ellipse([cx - disc_r, cy - disc_r, cx + disc_r, cy + disc_r], fill=SLATE)

    ray_len = int(big * 0.082)
    ray_w = max(2, int(big * 0.046))

    for angle in (0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0):
        draw_ray(d, cx, cy, disc_r, angle, ray_len, ray_w)

    return img.resize((size, size), Image.LANCZOS)


def render_logo(width: int, height: int) -> Image.Image:
    big_w, big_h = width * SCALE, height * SCALE
    img = Image.new("RGBA", (big_w, big_h), (0, 0, 0, 0))

    badge_big = render_icon(big_h)
    img.paste(badge_big, (0, 0), badge_big)

    d = ImageDraw.Draw(img)
    font_px = int(big_h * 0.38)
    font = load_font(font_px)

    text = "EMS"
    bbox = d.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    text_x = big_h + int((big_w - big_h - tw) // 2)
    text_y = (big_h - th) // 2 - bbox[1]

    d.text((text_x, text_y), text, fill=SLATE, font=font)

    return img.resize((width, height), Image.LANCZOS)


def main() -> None:
    icon_256 = render_icon(256)
    icon_256.save(OUT / "icon.png")
    print("Wrote icon.png 256×256")

    icon_512 = render_icon(512)
    icon_512.save(OUT / "icon@2x.png")
    print("Wrote icon@2x.png 512×512")

    logo_512 = render_logo(512, 256)
    logo_512.save(OUT / "logo.png")
    print("Wrote logo.png 512×256")

    logo_1024 = render_logo(1024, 512)
    logo_1024.save(OUT / "logo@2x.png")
    print("Wrote logo@2x.png 1024×512")


if __name__ == "__main__":
    main()
