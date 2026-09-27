# Image_converter

A universal image converter that runs locally in your browser. Convert images to a PDF, between formats, or remove backgrounds.

## Features

- **Convert to PDF** — merge images into a single PDF (lossless by default, with optional Quality slider or Target size in KB)
- **Convert format** — PNG, JPG, WebP, GIF, BMP, TIFF (single file downloads directly, multiple files come as a ZIP)
- **Remove background** — outputs transparent PNGs (uses `rembg` with the u2net model)

## Prerequisites

- Python 3.10 or newer
- pip

## Start the local host

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start the server
python app.py
```

Open your browser and go to:

```
http://localhost:3000
```

> **Note:** The first time you use **Remove background**, the u2net model (~176 MB) is downloaded automatically to `~/.rembg`. Subsequent runs use the cached model.

## Command line

```bash
# Convert images to a single PDF
python convert.py photo1.png photo2.jpg -o album.pdf

# PDF with a target size (at most 700 KB)
python convert.py photo1.png photo2.jpg -o album.pdf -t 700

# Convert an image to another format
python convert.py photo1.png --to png -o photo1.png

# Remove backgrounds from a folder of images (ZIP output)
python convert.py ./photos/ --remove-bg -o no-bg.zip
```