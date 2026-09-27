"""Universal image converter - local web app.

Convert images to a PDF, between formats, or remove backgrounds.
Run:   python app.py
Open:  http://localhost:3000
"""

import io
import mimetypes
import os
import sys
import tempfile
import zipfile
from pathlib import Path

from flask import Flask, render_template_string, request, send_file
from werkzeug.utils import secure_filename

import convert

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024 * 1024  # 256 MB upload limit

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Image Converter</title>
<style>
  :root {
    --bg: #F7F6F3; --surface: #FFFFFF; --ink: #111111; --ink-2: #2F3437;
    --muted: #787774; --line: #EAEAEA; --red-bg: #FDEBEC; --red-ink: #9F2F2D;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg); color: var(--ink-2); line-height: 1.6;
    font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
    min-height: 100vh; display: flex; flex-direction: column; align-items: center;
    padding: 96px 24px 48px;
  }
  main { width: 100%; max-width: 640px; }
  header { margin-bottom: 48px; }
  h1 {
    font-family: Georgia, "Times New Roman", serif;
    font-size: 42px; font-weight: 500; letter-spacing: -0.03em; line-height: 1.1;
    color: var(--ink);
  }
  .sub { color: var(--muted); margin-top: 10px; font-size: 15px; }
  .card {
    background: var(--surface); border: 1px solid var(--line);
    border-radius: 8px; padding: 32px;
  }
  #drop {
    border: 1px dashed #D6D6D1; border-radius: 8px; padding: 56px 24px;
    text-align: center; cursor: pointer;
    transition: border-color 200ms ease, background 200ms ease;
  }
  #drop.drag { border-color: var(--ink); background: #FBFAF8; }
  #drop svg { width: 26px; height: 26px; stroke: var(--muted); margin-bottom: 14px; }
  #drop p { color: var(--muted); font-size: 14px; }
  .btn {
    display: inline-block; background: var(--ink); color: #FFFFFF; border: 0;
    border-radius: 5px; padding: 12px 22px; margin-top: 18px;
    font-size: 14px; font-weight: 500; font-family: inherit; cursor: pointer;
    transition: background 200ms ease, transform 100ms ease;
  }
  .btn:hover { background: #333333; }
  .btn:active { transform: scale(0.98); }
  .btn:disabled { background: #C9C9C4; cursor: not-allowed; }
  .files {
    margin-top: 22px; border-bottom: 1px solid var(--line);
    font-family: Consolas, "Courier New", monospace; font-size: 12.5px;
  }
  .files li {
    list-style: none; display: flex; justify-content: space-between; gap: 16px;
    padding: 7px 0; border-top: 1px solid var(--line);
  }
  .files .n { color: var(--muted); white-space: nowrap; }
  .count { margin-top: 16px; font-size: 13px; color: var(--muted); }
  .quality {
    margin-top: 24px; padding-top: 20px; border-top: 1px solid var(--line);
    display: flex; align-items: center; gap: 14px;
  }
  .quality label { font-size: 13px; font-weight: 500; color: var(--ink-2); white-space: nowrap; }
  .quality input[type="range"] { flex: 1; accent-color: var(--ink); }
  .quality .qval { font-family: Consolas, "Courier New", monospace; font-size: 12px; color: var(--muted); white-space: nowrap; }
  .quality.size { margin-top: 16px; }
  .quality.size input[type="number"] {
    flex: 1; min-width: 0; padding: 8px 10px; border: 1px solid var(--line);
    border-radius: 5px; font-size: 14px; font-family: inherit;
    color: var(--ink-2); background: var(--surface);
  }
  .quality.size input[type="number"]:focus { outline: none; border-color: var(--ink); }
  .quality.dim { opacity: 0.45; }
  .quality[hidden] { display: none; }
  .quality select {
    flex: 1; min-width: 0; padding: 8px 10px; border: 1px solid var(--line);
    border-radius: 5px; font-size: 14px; font-family: inherit;
    color: var(--ink-2); background: var(--surface); cursor: pointer;
  }
  .quality select:focus { outline: none; border-color: var(--ink); }
  .error {
    background: var(--red-bg); color: var(--red-ink); border-radius: 6px;
    padding: 12px 16px; margin-top: 18px; font-size: 14px;
  }
  footer {
    margin-top: 44px; font-family: Consolas, "Courier New", monospace;
    font-size: 12px; color: var(--muted);
  }
</style>
</head>
<body>
<main>
  <header>
    <h1>Image Converter</h1>
    <p class="sub">Drop images and convert: PDF, other formats, or remove backgrounds.</p>
  </header>

  <section class="card">
    <form method="post" action="/convert" enctype="multipart/form-data">
      <div id="drop" role="button" tabindex="0" aria-label="Choose images">
        <svg viewBox="0 0 24 24" fill="none" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 16V4"/><path d="M6 10l6-6 6 6"/>
          <path d="M4 20h16"/>
        </svg>
        <p>Drag and drop images here, or</p>
        <button type="button" class="btn">Choose images</button>
        <input type="file" id="images" name="images" multiple hidden>
      </div>
      <ul class="files" id="files"></ul>
      <p class="count" id="count">No images chosen</p>
      <div class="quality">
        <label for="mode">Mode</label>
        <select id="mode" name="mode">
          <option value="pdf" selected>Convert to PDF</option>
          <option value="format">Convert format</option>
          <option value="bg">Remove background</option>
        </select>
        <span class="qval" id="modeHint">Lossless, or set a target size.</span>
      </div>
      <div class="quality" id="qualityRow">
        <label for="quality">Quality</label>
        <input type="range" id="quality" name="quality" min="5" max="100" value="100">
        <span class="qval" id="qval">100% - lossless</span>
      </div>
      <div class="quality size" id="targetRow">
        <label for="target">Target size (KB)</label>
        <input type="number" id="target" name="target_kb" min="1" placeholder="e.g. 700">
        <span class="qval">Optional - overrides Quality</span>
      </div>
      <div class="quality size" id="fmtRow" hidden>
        <label for="fmt">To format</label>
        <select id="fmt" name="fmt">
          <option value="png" selected>PNG</option>
          <option value="jpg">JPG</option>
          <option value="webp">WebP</option>
          <option value="gif">GIF</option>
          <option value="bmp">BMP</option>
          <option value="tiff">TIFF</option>
        </select>
        <span class="qval">Each image becomes this format.</span>
      </div>
      {% if error %}<div class="error">{{ error }}</div>{% endif %}
      <button type="submit" class="btn" id="go" disabled>Convert to PDF</button>
    </form>
  </section>

  <footer>localhost:3000 &middot; PDF &middot; format conversion &middot; remove background &middot; up to 256 MB</footer>
</main>

<script>
  const input = document.getElementById('images');
  const drop = document.getElementById('drop');
  const list = document.getElementById('files');
  const count = document.getElementById('count');
  const go = document.getElementById('go');
  const quality = document.getElementById('quality');
  const qval = document.getElementById('qval');

  quality.addEventListener('input', () => {
    const v = parseInt(quality.value, 10);
    qval.textContent = v >= 100 ? '100% - lossless' : `${v}% - re-encoded JPEG`;
  });

  const target = document.getElementById('target');
  const qualityRow = document.getElementById('qualityRow');

  target.addEventListener('input', () => {
    const active = target.value.trim() !== '';
    quality.disabled = active;
    qualityRow.classList.toggle('dim', active);
    if (active) {
      qval.textContent = 'overridden by target size';
    } else {
      const v = parseInt(quality.value, 10);
      qval.textContent = v >= 100 ? '100% - lossless' : `${v}% - re-encoded JPEG`;
    }
  });

  const mode = document.getElementById('mode');
  const modeHint = document.getElementById('modeHint');
  const fmtRow = document.getElementById('fmtRow');
  const targetRow = document.getElementById('targetRow');

  function syncMode() {
    const m = mode.value;
    const isPdf = m === 'pdf';
    qualityRow.hidden = !isPdf;
    targetRow.hidden = !isPdf;
    fmtRow.hidden = m !== 'format';
    modeHint.textContent = isPdf ? 'Lossless, or set a target size.'
      : m === 'format' ? 'Re-encode images into another format.'
      : 'Transparent PNGs, background removed.';
    go.textContent = isPdf ? 'Convert to PDF'
      : m === 'format' ? 'Convert images' : 'Remove background';
  }
  mode.addEventListener('change', syncMode);
  syncMode();

  function render() {
    const items = Array.from(input.files || []);
    list.innerHTML = items.map(f =>
      `<li><span>${f.name}</span><span class="n">${(f.size / 1024).toFixed(0)} KB</span></li>`
    ).join('');
    go.disabled = items.length === 0;
    count.textContent = items.length
      ? `${items.length} image${items.length > 1 ? 's' : ''} ready`
      : 'No images chosen';
  }

  drop.addEventListener('click', () => input.click());
  input.addEventListener('change', render);
  ['dragover', 'dragenter'].forEach(ev =>
    drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('drag'); }));
  ['dragleave', 'drop'].forEach(ev =>
    drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('drag'); }));
  drop.addEventListener('drop', e => {
    if (e.dataTransfer.files.length) {
      const dt = new DataTransfer();
      Array.from(e.dataTransfer.files).forEach(f => dt.items.add(f));
      input.files = dt.files;
      render();
    }
  });
</script>
</body>
</html>"""


@app.get("/")
def index(error=None):
    return render_template_string(INDEX_HTML, error=error)


@app.post("/convert")
def do_convert():
    uploads = [f for f in request.files.getlist("images") if f and f.filename]
    if not uploads:
        return index("Choose at least one image.")

    mode = request.form.get("mode", "pdf")

    q_raw = request.form.get("quality", "100")
    try:
        q = int(q_raw)
    except ValueError:
        q = 100
    quality = None if q >= 100 else max(1, min(99, q))

    t_raw = request.form.get("target_kb", "")
    try:
        target_kb = int(t_raw)
    except ValueError:
        target_kb = None
    if target_kb is not None and target_kb <= 0:
        target_kb = None

    skipped = []
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        saved = []
        for i, up in enumerate(uploads):
            name = secure_filename(up.filename) or f"image_{i}"
            dest = tmpdir / f"{i:04d}_{name}"
            up.save(dest)
            saved.append((up.filename, dest))

        buf = io.BytesIO()
        if mode == "format":
            fmt = request.form.get("fmt", "png").lower()
            if fmt not in convert.CONVERT_FORMATS:
                return index(f"Unknown format: {fmt}")
            return _send_transformed(saved, fmt=fmt)
        if mode == "bg":
            return _send_transformed(saved, remove_bg=True)

        valid, bad = convert.convert_images(
            [p for _, p in saved], buf, quality=quality, target_size_kb=target_kb
        )

        if not valid:
            names = ", ".join(n for n, _ in saved)
            return index(f"None of the files could be read as images: {names}")

        skipped = [n for n, p in saved if p in bad]
        pdf = buf.getvalue()

    if skipped:
        print(f"skipped: {', '.join(skipped)}", file=sys.stderr)

    resp = send_file(
        io.BytesIO(pdf),
        mimetype="application/pdf",
        as_attachment=True,
        download_name="images.pdf",
    )
    if skipped:
        resp.headers["X-Skipped-Files"] = ", ".join(skipped)
    return resp


def _send_transformed(saved: list[tuple[str, Path]], fmt: str | None = None, remove_bg: bool = False):
    """Convert saved images (format or background removal) and send the result.

    One image downloads directly; several download as a ZIP.
    """
    results: list[tuple[str, bytes]] = []
    for i, (orig_name, path) in enumerate(saved):
        try:
            if remove_bg:
                buf = convert.remove_background_image(path)
                ext = "png"
            else:
                buf = convert.convert_image_file(path, fmt)
                ext = "png" if fmt == "png" else "jpg" if fmt in ("jpg", "jpeg") else fmt
        except Exception as e:
            return index(f"Could not convert {orig_name}: {e}")
        stem = Path(secure_filename(orig_name)).stem or f"image_{i}"
        results.append((f"{stem}.{ext}", buf.getvalue()))

    if len(results) == 1:
        name, data = results[0]
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return send_file(io.BytesIO(data), as_attachment=True, download_name=name, mimetype=mime)

    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
        seen: set[str] = set()
        for name, data in results:
            final, i = name, 1
            while final in seen:
                stem, ext = name.rsplit(".", 1)
                final = f"{stem}_{i}.{ext}"
                i += 1
            seen.add(final)
            z.writestr(final, data)
    zbuf.seek(0)
    return send_file(zbuf, as_attachment=True, download_name="images.zip", mimetype="application/zip")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    print(f"Serving on http://localhost:{port}  (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=port, debug=False)