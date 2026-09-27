"""Convert images (PNG, JPG, WebP, GIF, TIFF, BMP, HEIC, ...) to a single PDF. Minimal & fast.

img2pdf embeds JPEG/PNG losslessly (no re-encoding) and re-encodes other
formats internally via Pillow - one conversion pass, as fast as it gets.

Usage:
    python convert.py photo1.png photo2.jpg -o album.pdf
    python convert.py photo1.png photo2.jpg -o album.pdf -t 700  # at most 700 KB
    python convert.py photo1.png --to png -o photo1.png          # format convert
    python convert.py ./photos/ --remove-bg -o no-bg.zip         # remove backgrounds
    python convert.py ./photos/                 # a whole folder

HEIC/AVIF support requires Pillow plugins: pip install pillow-heif
"""

import argparse
import io
import sys
import time
from pathlib import Path

import img2pdf
from PIL import Image

IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".jfif", ".gif", ".bmp",
    ".tif", ".tiff", ".webp", ".heic", ".heif", ".avif",
}

CONVERT_FORMATS = {"png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff"}


def collect_images(args: list[str]) -> list[Path]:
    """Expand file/folder arguments into an ordered list of image files."""
    files: list[Path] = []
    for raw in args:
        p = Path(raw)
        if p.is_dir():
            files.extend(p for p in sorted(p.iterdir()) if p.suffix.lower() in IMAGE_EXTS)
        elif p.is_file():
            files.append(p)
        else:
            print(f"skip (not found): {p}", file=sys.stderr)
    return files


def _compress(path: Path, quality: int, scale: float = 1.0) -> io.BytesIO:
    """Re-encode an image as JPEG at `quality` (1-100) to shrink the PDF.

    Transparency is flattened onto white. When `scale` < 1.0 the image is
    downscaled first (LANCZOS) to shrink the PDF further. The re-encoded
    JPEG is then embedded by img2pdf without further loss.
    """
    with Image.open(path) as im:
        im.load()
        if im.mode in ("RGBA", "LA", "P"):
            rgba = im.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.getchannel("A"))
            rgb = bg
        else:
            rgb = im.convert("RGB")
        if scale != 1.0:
            rgb = rgb.resize(
                (max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale))),
                Image.Resampling.LANCZOS,
            )
        buf = io.BytesIO()
        rgb.save(buf, format="JPEG", quality=quality, optimize=True)
    buf.seek(0)
    return buf


def _convert_at_target(paths: list[Path], out, target_bytes: int) -> None:
    """Write a PDF of at most `target_bytes`, as close to it as possible.

    Three rounds, each only entered if the previous can't reach the target:
    1. lossless embed (the default quality-free path);
    2. binary-search JPEG quality 1-99 at full resolution for the highest
       quality that still fits;
    3. binary-search an image downscale factor (0.10-1.0) for the largest
       scale that fits, then re-search quality at that scale.
    Never fails: if even the smallest output is too big, that smallest
    output is written anyway (best effort).
    """
    cache: dict[tuple[int | None, float], bytes] = {}

    def build(quality: int | None, scale: float) -> bytes:
        key = (quality, scale)
        if key not in cache:
            buf = io.BytesIO()
            if quality is None:
                img2pdf.convert([str(p) for p in paths], outputstream=buf)
            else:
                img2pdf.convert([_compress(p, quality, scale) for p in paths], outputstream=buf)
            cache[key] = buf.getvalue()
        return cache[key]

    def best_quality(scale: float) -> int | None:
        """Highest JPEG quality at `scale` whose PDF fits; None if q=1 doesn't."""
        if len(build(1, scale)) > target_bytes:
            return None
        lo, hi, best = 1, 99, 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if len(build(mid, scale)) <= target_bytes:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return best

    if len(build(None, 1.0)) <= target_bytes:
        out.write(build(None, 1.0))
        return

    q = best_quality(1.0)
    if q is not None:
        out.write(build(q, 1.0))
        return

    lo, hi = 0.10, 1.0
    while hi - lo > 0.05:
        mid = (lo + hi) / 2
        if len(build(1, mid)) <= target_bytes:
            lo = mid
        else:
            hi = mid
    q = best_quality(lo)
    if q is not None:
        out.write(build(q, lo))
    else:
        out.write(build(1, 0.10))  # best effort: smallest achievable


def convert_image_file(src: Path, fmt: str) -> io.BytesIO:
    """Re-encode one image as `fmt` (png/jpg/webp/gif/bmp/tiff).

    Transparency is flattened onto white for formats without alpha
    (jpg, bmp). Returns a BytesIO of the re-encoded image.
    """
    fmt = fmt.lower()
    save_fmt = "JPEG" if fmt in ("jpg", "jpeg") else fmt.upper()
    with Image.open(src) as im:
        im.load()
        if save_fmt in ("JPEG", "BMP") and im.mode in ("RGBA", "LA", "P"):
            rgba = im.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.getchannel("A"))
            im = bg
        buf = io.BytesIO()
        kwargs = {}
        if save_fmt in ("JPEG", "PNG", "WEBP"):
            kwargs["optimize"] = True
        if save_fmt == "JPEG":
            kwargs["quality"] = 92
        im.save(buf, format=save_fmt, **kwargs)
    buf.seek(0)
    return buf


_rembg_session = None


def remove_background_image(src: Path) -> io.BytesIO:
    """Remove the background of one image, returning a transparent PNG.

    Uses rembg with the u2net model (small & fast on CPU; downloaded to
    ~/.rembg on first use). The import and session are lazy so the rest
    of the app works even when rembg is not installed. Exceptions
    propagate to the caller to report gracefully.
    """
    from rembg import new_session, remove

    global _rembg_session
    if _rembg_session is None:
        _rembg_session = new_session("u2net")

    with Image.open(src) as im:
        im.load()
        out = remove(im, session=_rembg_session)
        buf = io.BytesIO()
        out.save(buf, format="PNG")
    buf.seek(0)
    return buf


def convert_images(
    paths: list[Path],
    out,
    quality: int | None = None,
    target_size_kb: int | None = None,
) -> tuple[list[Path], list[Path]]:
    """Embed image files into one PDF, writing to file-like object `out`.

    quality=None embeds JPEG/PNG losslessly (fastest). quality=1-100
    re-encodes every image as JPEG at that quality to shrink the file.
    target_size_kb (positive int) produces a PDF of at most that many KB,
    searching quality and downscale automatically; it overrides `quality`.
    Returns (valid, skipped) - skipped files could not be read as images
    (corrupt, or HEIC/AVIF without plugins).
    """
    valid, bad = [], []
    for p in paths:
        try:
            with Image.open(p) as im:
                pass
        except Exception:
            bad.append(p)
        else:
            valid.append(p)
    if not valid:
        return valid, bad

    if target_size_kb and target_size_kb > 0:
        _convert_at_target(valid, out, target_size_kb * 1024)
    elif quality is None:
        img2pdf.convert([str(p) for p in valid], outputstream=out)
    else:
        img2pdf.convert([_compress(p, quality) for p in valid], outputstream=out)
    return valid, bad


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Convert any image files to a single PDF.",
        epilog="Pass image files or folders; order is by filename.",
    )
    ap.add_argument("inputs", nargs="+", help="image files and/or folders")
    ap.add_argument("-o", "--output", type=Path, default=Path("output.pdf"),
                    help="output PDF (default: output.pdf)")
    ap.add_argument("-q", "--quality", type=int, default=None, metavar="1-100",
                    help="re-encode images as JPEG at this quality to shrink the PDF (default: lossless)")
    ap.add_argument("-t", "--target", type=int, default=None, metavar="KB",
                    help="produce a PDF of at most this many KB (overrides -q)")
    ap.add_argument("--to", type=str, default=None, metavar="FORMAT",
                    help="convert images to this format (png/jpg/webp/gif/bmp/tiff) instead of a PDF")
    ap.add_argument("--remove-bg", action="store_true",
                    help="remove image backgrounds, saving transparent PNGs, instead of a PDF")
    args = ap.parse_args()

    if args.quality is not None and not 1 <= args.quality <= 100:
        print("quality must be between 1 and 100", file=sys.stderr)
        return 1

    if args.target is not None and args.target < 1:
        print("target must be at least 1 KB", file=sys.stderr)
        return 1

    if args.to is not None and args.to.lower() not in CONVERT_FORMATS:
        print(f"unknown format: {args.to} (choose from {', '.join(sorted(CONVERT_FORMATS))})", file=sys.stderr)
        return 1

    if args.to and args.remove_bg:
        print("--to and --remove-bg are mutually exclusive", file=sys.stderr)
        return 1

    images = collect_images(args.inputs)
    if not images:
        print("no images found", file=sys.stderr)
        return 1

    start = time.perf_counter()
    if args.to or args.remove_bg:
        return _run_transform(args, images, start)

    with args.output.open("wb") as f:
        valid, bad = convert_images(images, f, quality=args.quality, target_size_kb=args.target)
    elapsed = time.perf_counter() - start

    for p in bad:
        print(f"skip (unreadable): {p}", file=sys.stderr)
    if not valid:
        print("no readable images", file=sys.stderr)
        return 1

    size_kb = args.output.stat().st_size / 1024
    if args.target is not None:
        mode = f"target {args.target} KB"
    else:
        mode = "lossless" if args.quality is None else f"jpeg q{args.quality}"
    print(f"Wrote {args.output} ({mode}) - {len(valid)} images, {size_kb:,.0f} KiB, {elapsed:.2f}s")
    return 0


def _run_transform(args: argparse.Namespace, images: list[Path], start: float) -> int:
    """Convert each image to a new format or remove backgrounds.

    Single image writes the result file to `-o`; multiple images write a
    ZIP of all results to `-o`.
    """
    from zipfile import ZIP_DEFLATED, ZipFile

    results: list[tuple[str, str, io.BytesIO]] = []
    for p in images:
        try:
            if args.remove_bg:
                results.append((p.stem, "png", remove_background_image(p)))
            else:
                fmt = args.to.lower()
                ext = "png" if fmt == "png" else "jpg" if fmt in ("jpg", "jpeg") else fmt
                results.append((p.stem, ext, convert_image_file(p, fmt)))
        except Exception as e:
            print(f"skip (failed): {p}: {e}", file=sys.stderr)

    if not results:
        print("no images converted", file=sys.stderr)
        return 1

    if len(results) == 1:
        stem, ext, buf = results[0]
        out_path = args.output if args.output != Path("output.pdf") else Path(f"{stem}.{ext}")
        with out_path.open("wb") as f:
            f.write(buf.getvalue())
    else:
        out_path = args.output if args.output != Path("output.pdf") else Path("output.zip")
        seen: set[str] = set()
        with ZipFile(out_path, "w", ZIP_DEFLATED) as z:
            for stem, ext, buf in results:
                name = f"{stem}.{ext}"
                i = 1
                while name in seen:
                    name = f"{stem}_{i}.{ext}"
                    i += 1
                seen.add(name)
                z.writestr(name, buf.getvalue())

    elapsed = time.perf_counter() - start
    size_kb = out_path.stat().st_size / 1024
    mode = "remove-bg" if args.remove_bg else f"-> {args.to}"
    print(f"Wrote {out_path} ({mode}) - {len(results)} images, {size_kb:,.0f} KiB, {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())