"""Universal image converter + URL shortener - local web app.

Convert images to a PDF, between formats, or remove backgrounds;
shorten URLs. Run:   python app.py
Open:  http://localhost:3000
"""

import io
import mimetypes
import os
import qrcode
import secrets
import socket
import sqlite3
import string
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse, urlsplit, urlunsplit

from flask import Flask, abort, redirect, render_template_string, request, send_file
from werkzeug.utils import secure_filename

import convert

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024 * 1024  # 256 MB upload limit

PORT = int(os.environ.get("PORT", 3000))

_CODE_ALPHABET = string.ascii_letters + string.digits


def _db_path() -> Path:
    """Pick a writable location for the SQLite DB.

    Locally this is urls.db next to app.py; on read-only serverless
    filesystems (Vercel) it falls back to the temp dir so the app can
    still start and serve.
    """
    local = Path(__file__).resolve().with_name("urls.db")
    try:
        conn = sqlite3.connect(local)
        conn.execute("SELECT 1")
        conn.close()
        return local
    except sqlite3.OperationalError:
        return Path(tempfile.gettempdir()) / "urls.db"


DB_PATH = _db_path()


def _get_lan_ip() -> str:
    """Pick the LAN IP of the default route (no packets sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


_LAN_IP = _get_lan_ip()


def _advertised_base() -> str:
    """Base URL for short links/QRs, reachable from other devices on the LAN."""
    parts = urlsplit(request.url_root.rstrip("/"))
    if parts.hostname in ("localhost", "127.0.0.1", "::1"):
        port = parts.port or PORT
        return urlunsplit((parts.scheme, f"{_LAN_IP}:{port}", "", "", ""))
    return request.url_root.rstrip("/")


def _db():
    """Open a connection, ensuring the urls table exists."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS urls ("
        "code TEXT PRIMARY KEY, url TEXT NOT NULL, "
        "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.commit()
    return conn


def _shorten_insert(url: str) -> str:
    """Insert a short code for url; retries on collision. Returns the code."""
    for _ in range(10):
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
        conn = _db()
        try:
            conn.execute("INSERT INTO urls (code, url) VALUES (?, ?)", (code, url))
            conn.commit()
        except sqlite3.IntegrityError:
            continue
        finally:
            conn.close()
        return code
    raise RuntimeError("Could not allocate a short code, try again.")


def _recent_urls(limit: int = 8):
    conn = _db()
    try:
        return conn.execute(
            "SELECT code, url FROM urls ORDER BY rowid DESC LIMIT ?", (limit,)
        ).fetchall()
    finally:
        conn.close()


def _lookup_url(code: str) -> str | None:
    conn = _db()
    try:
        row = conn.execute("SELECT url FROM urls WHERE code = ?", (code,)).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


try:
    _db().close()  # initialize the database file on startup
except sqlite3.OperationalError:
    pass  # read-only filesystem (serverless): DB will be created lazily in /tmp

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Image Converter</title>
<script>try{var t=localStorage.getItem('theme');if(t)document.documentElement.dataset.theme=t;}catch(e){}</script>
<style>
  :root {
    --bg: #F7F6F3; --surface: #FFFFFF; --ink: #111111; --ink-2: #2F3437;
    --muted: #787774; --line: #EAEAEA; --red-bg: #FDEBEC; --red-ink: #9F2F2D;
  }
  [data-theme="dark"] {
    --bg: #161615; --surface: #1E1E1C; --ink: #F4F4F1; --ink-2: #D2D2CD;
    --muted: #95958D; --line: #33332F; --red-bg: #3C1F1E; --red-ink: #E9A09D;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg); color: var(--ink-2); line-height: 1.6;
    font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
    min-height: 100vh; display: flex; flex-direction: column; align-items: center;
    padding: 96px 24px 48px;
  }
  main { width: 100%; max-width: 640px; }
  header { margin-bottom: 48px; position: relative; }
  #themeToggle {
    position: absolute; top: -6px; right: 0;
    background: none; border: 1px solid var(--line); border-radius: 20px;
    padding: 7px 14px; font-size: 12.5px; font-family: inherit;
    color: var(--muted); cursor: pointer;
    transition: color 150ms ease, border-color 150ms ease;
  }
  #themeToggle:hover { color: var(--ink); border-color: var(--ink-2); }
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
  [data-theme="dark"] #drop { border-color: #3A3A36; }
  [data-theme="dark"] #drop.drag { background: #262624; }
  #drop svg { width: 26px; height: 26px; stroke: var(--muted); margin-bottom: 14px; }
  #drop p { color: var(--muted); font-size: 14px; }
  .btn {
    display: inline-block; background: var(--ink); color: var(--surface); border: 0;
    border-radius: 5px; padding: 12px 22px; margin-top: 18px;
    font-size: 14px; font-weight: 500; font-family: inherit; cursor: pointer;
    text-decoration: none;
    transition: background 200ms ease, transform 100ms ease;
  }
  .btn:hover { background: #333333; }
  .btn:active { transform: scale(0.98); }
  .btn:disabled { background: #C9C9C4; cursor: not-allowed; }
  [data-theme="dark"] .btn:hover { background: #D8D8D3; }
  [data-theme="dark"] .btn:disabled { background: #464642; color: var(--muted); }
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
  section.card + section.card { margin-top: 20px; }
  .stitle {
    font-family: Georgia, "Times New Roman", serif; font-size: 21px; font-weight: 500;
    letter-spacing: -0.02em; color: var(--ink); margin-bottom: 2px;
  }
  .ssub { color: var(--muted); font-size: 13px; margin-bottom: 18px; }
  .ssub .lanhint {
    color: var(--ink-2); font-family: Consolas, "Courier New", monospace;
    text-decoration: none; border-bottom: 1px dashed var(--line);
  }
  .ssub .lanhint:hover { color: var(--ink); border-color: var(--ink-2); }
  .shortform { display: flex; gap: 10px; }
  .shortform input[type="url"], .shortform input[type="text"] {
    flex: 1; min-width: 0; padding: 10px 12px; border: 1px solid var(--line);
    border-radius: 5px; font-size: 14px; font-family: inherit;
    color: var(--ink-2); background: var(--surface);
  }
  .shortform input[type="url"]:focus, .shortform input[type="text"]:focus { outline: none; border-color: var(--ink); }
  .shortform .btn { margin-top: 0; padding: 10px 18px; }
  .sresult {
    margin-top: 16px; padding: 12px 14px; background: var(--bg);
    border: 1px solid var(--line); border-radius: 6px;
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap; font-size: 14px;
  }
  .shortlink {
    font-family: Consolas, "Courier New", monospace; font-size: 13px;
    color: var(--ink); word-break: break-all; text-decoration: none;
  }
  .shortlink:hover { text-decoration: underline; }
  #copyShort { margin-top: 0; padding: 6px 14px; font-size: 12.5px; }
  .files .n { max-width: 55%; overflow: hidden; text-overflow: ellipsis; }
  .sresult .qr { width: 110px; height: 110px; border-radius: 6px; flex-shrink: 0; }
  .smeta { display: flex; flex-direction: column; gap: 6px; min-width: 0; flex: 1; }
  .smeta .shortlink { font-size: 12.5px; }
  .sacts { display: flex; gap: 8px; }
  .sacts .btn { margin-top: 4px; padding: 6px 12px; font-size: 12.5px; }
  .qwrap { display: inline-flex; align-items: center; gap: 10px; min-width: 0; }
  .qr-thumb { width: 20px; height: 20px; border-radius: 3px; display: block; }
  .sresult[hidden] { display: none; }
  .pwrow { display: flex; align-items: center; gap: 14px; }
  .pwrow label { font-size: 13px; font-weight: 500; color: var(--ink-2); white-space: nowrap; }
  .pwrow input[type="number"] {
    width: 84px; padding: 8px 10px; border: 1px solid var(--line);
    border-radius: 5px; font-size: 14px; font-family: inherit;
    color: var(--ink-2); background: var(--surface);
  }
  .pwrow input[type="number"]:focus { outline: none; border-color: var(--ink); }
  .pwopts { display: flex; flex-wrap: wrap; gap: 6px 16px; margin-top: 14px; font-size: 13px; color: var(--ink-2); }
  .pwopts label { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; }
  .pwopts input { accent-color: var(--ink); }
  .pwout {
    display: flex; align-items: center; gap: 12px; margin-top: 16px;
    padding: 12px 14px; background: var(--bg); border: 1px solid var(--line);
    border-radius: 6px;
  }
  .pwout code {
    flex: 1; font-family: Consolas, "Courier New", monospace; font-size: 15px;
    color: var(--ink); word-break: break-all;
  }
  .pwout .btn { margin-top: 0; padding: 6px 14px; font-size: 12.5px; }
  #pwGen { margin-top: 16px; }
</style>
</head>
<body>
<main>
  <header>
    <h1>Image Converter</h1>
    <p class="sub">Drop images and convert: PDF, other formats, or remove backgrounds.</p>
    <button type="button" id="themeToggle" aria-label="Toggle dark mode">Dark</button>
  </header>

  <section class="card">
    <form method="post" action="/convert" enctype="multipart/form-data">
      <div id="drop" role="button" tabindex="0" aria-label="Choose images">
        <svg viewBox="0 0 24 24" fill="none" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 16V4"/><path d="M6 10l6-6 6 6"/>
          <path d="M4 20h16"/>
        </svg>
        <p id="dropLabel">Drag and drop images here, or</p>
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
          <option value="pdf2img">PDF to images</option>
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

  <section class="card">
    <h2 class="stitle">URL Shortener</h2>
    <p class="ssub">Turn a long link into a short one. Your PC is on this network at <a class="lanhint" href="http://{{ lan_ip }}:3000">{{ lan_ip }}:3000</a> — QRs scan from any phone on the same Wi-Fi.</p>
    <form method="post" action="/shorten" class="shortform">
      <input type="url" name="long_url" placeholder="Paste a long URL here…" required>
      <button type="submit" class="btn">Shorten</button>
    </form>
    {% if short %}
    <div class="sresult">
      <img class="qr" src="/qr/{{ code }}" alt="QR code" width="110" height="110">
      <div class="smeta">
        <span>Short URL:</span>
        <a class="shortlink" href="{{ short }}" target="_blank" rel="noopener">{{ short }}</a>
        <div class="sacts">
          <button type="button" class="btn" id="copyShort">Copy</button>
          <a class="btn" href="/qr/{{ code }}?dl=1">Download QR</a>
        </div>
      </div>
    </div>
    {% endif %}
    {% if recent %}
    <ul class="files">
      {% for code, url in recent %}
      <li><span class="qwrap"><a class="shortlink" href="/qr/{{ code }}" target="_blank" rel="noopener" title="Open QR"><img class="qr-thumb" src="/qr/{{ code }}" alt="QR"></a><a class="shortlink" href="/s/{{ code }}" target="_blank" rel="noopener">/s/{{ code }}</a></span><span class="n" title="{{ url }}">{{ url }}</span></li>
      {% endfor %}
    </ul>
    {% endif %}
  </section>

  <section class="card">
    <h2 class="stitle">Password Generator</h2>
    <p class="ssub">Random, secure passwords — copy and go.</p>
    <div class="pwrow">
      <label for="pwLen">Length</label>
      <input type="number" id="pwLen" min="8" max="64" value="16">
      <span class="qval" id="pwBits">entropy —</span>
    </div>
    <div class="pwopts">
      <label><input type="checkbox" id="pwUpper" checked> A–Z</label>
      <label><input type="checkbox" id="pwLower" checked> a–z</label>
      <label><input type="checkbox" id="pwDigits" checked> 0–9</label>
      <label><input type="checkbox" id="pwSymbols" checked> !@#…</label>
    </div>
    <div class="pwout">
      <code id="pwOut">…</code>
      <button type="button" class="btn" id="pwCopy">Copy</button>
    </div>
    <button type="button" class="btn" id="pwGen">Generate</button>
  </section>

  <section class="card">
    <h2 class="stitle">QR Code Generator</h2>
    <p class="ssub">Any text or link becomes a scannable QR.</p>
    <form class="shortform">
      <input type="text" id="qrData" placeholder="Text or URL…" maxlength="1000">
      <button type="button" class="btn" id="qrGen">Generate</button>
    </form>
    <div class="sresult" id="qrResult" hidden>
      <img class="qr" id="qrImg" alt="QR code">
      <div class="smeta">
        <span>QR for your text</span>
        <div class="sacts">
          <a class="btn" id="qrDl" href="#">Download QR</a>
        </div>
      </div>
    </div>
  </section>

  <footer>localhost:3000 &middot; PDF &middot; format conversion &middot; remove background &middot; PDF to images &middot; URL shortener &middot; password generator &middot; QR generator &middot; up to 256 MB</footer>
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
  const dropLabel = document.getElementById('dropLabel');

  function syncMode() {
    const m = mode.value;
    const isPdf = m === 'pdf';
    const isPdf2Img = m === 'pdf2img';
    qualityRow.hidden = !isPdf;
    targetRow.hidden = !isPdf;
    fmtRow.hidden = m !== 'format';
    dropLabel.textContent = isPdf2Img ? 'Drag and drop PDFs here, or' : 'Drag and drop images here, or';
    modeHint.textContent = isPdf ? 'Lossless, or set a target size.'
      : isPdf2Img ? 'Render every PDF page as an image.'
      : m === 'format' ? 'Re-encode images into another format.'
      : 'Transparent PNGs, background removed.';
    go.textContent = isPdf ? 'Convert to PDF'
      : isPdf2Img ? 'Extract pages'
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

  const themeToggle = document.getElementById('themeToggle');
  function paintTheme() {
    themeToggle.textContent = document.documentElement.dataset.theme === 'dark' ? 'Light' : 'Dark';
  }
  themeToggle.addEventListener('click', () => {
    const dark = document.documentElement.dataset.theme === 'dark';
    document.documentElement.dataset.theme = dark ? 'light' : 'dark';
    try { localStorage.setItem('theme', document.documentElement.dataset.theme); } catch (e) {}
    paintTheme();
  });
  paintTheme();

  const copyShort = document.getElementById('copyShort');
  if (copyShort) {
    copyShort.addEventListener('click', async () => {
      const link = document.querySelector('.smeta .shortlink');
      try {
        await navigator.clipboard.writeText(link.textContent);
        copyShort.textContent = 'Copied';
        setTimeout(() => { copyShort.textContent = 'Copy'; }, 1600);
      } catch (e) {}
    });
  }

  const pwLen = document.getElementById('pwLen');
  const pwUpper = document.getElementById('pwUpper');
  const pwLower = document.getElementById('pwLower');
  const pwDigits = document.getElementById('pwDigits');
  const pwSymbols = document.getElementById('pwSymbols');
  const pwOut = document.getElementById('pwOut');
  const pwBits = document.getElementById('pwBits');
  const pwGen = document.getElementById('pwGen');
  const pwCopy = document.getElementById('pwCopy');

  function randInt(max) {
    const buf = new Uint32Array(1);
    crypto.getRandomValues(buf);
    return buf[0] % max;
  }

  function generatePassword() {
    const pools = [];
    if (pwUpper.checked) pools.push('ABCDEFGHIJKLMNOPQRSTUVWXYZ');
    if (pwLower.checked) pools.push('abcdefghijklmnopqrstuvwxyz');
    if (pwDigits.checked) pools.push('0123456789');
    if (pwSymbols.checked) pools.push('!@#$%^&*()-_=+[]{};:,.<>?');
    const len = Math.min(64, Math.max(8, parseInt(pwLen.value, 10) || 16));
    if (!pools.length) {
      pwOut.textContent = 'pick at least one character set';
      pwBits.textContent = '';
      return;
    }
    const all = pools.join('');
    const chars = pools.map(p => p[randInt(p.length)]);
    while (chars.length < len) chars.push(all[randInt(all.length)]);
    for (let i = chars.length - 1; i > 0; i--) {
      const j = randInt(i + 1);
      [chars[i], chars[j]] = [chars[j], chars[i]];
    }
    pwOut.textContent = chars.slice(0, len).join('');
    const bits = len * Math.log2(all.length);
    pwBits.textContent = `entropy ~${bits.toFixed(0)} bits — ${bits >= 80 ? 'strong' : bits >= 50 ? 'good' : 'weak'}`;
  }

  pwGen.addEventListener('click', generatePassword);
  pwLen.addEventListener('change', generatePassword);
  [pwUpper, pwLower, pwDigits, pwSymbols].forEach(cb => cb.addEventListener('change', generatePassword));
  pwCopy.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(pwOut.textContent);
      pwCopy.textContent = 'Copied';
      setTimeout(() => { pwCopy.textContent = 'Copy'; }, 1600);
    } catch (e) {}
  });
  generatePassword();

  const qrData = document.getElementById('qrData');
  const qrGen = document.getElementById('qrGen');
  const qrResult = document.getElementById('qrResult');
  const qrImg = document.getElementById('qrImg');
  const qrDl = document.getElementById('qrDl');

  qrGen.addEventListener('click', () => {
    const data = qrData.value.trim();
    if (!data) return;
    const src = '/qrtext?data=' + encodeURIComponent(data);
    qrImg.src = src;
    qrDl.href = src + '&dl=1';
    qrResult.hidden = false;
  });
  qrData.addEventListener('keydown', e => { if (e.key === 'Enter') qrGen.click(); });
</script>
</body>
</html>"""


@app.get("/")
def index(error=None, short=None, code=None):
    return render_template_string(
        INDEX_HTML, error=error, short=short, code=code, recent=_recent_urls(), lan_ip=_LAN_IP
    )


@app.post("/shorten")
def do_shorten():
    raw = (request.form.get("long_url") or "").strip()
    if not raw:
        return index("Enter a URL to shorten.")
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return index("Enter a valid URL starting with http:// or https://")
    if len(raw) > 2048:
        return index("URL is too long (max 2048 characters).")
    try:
        code = _shorten_insert(raw)
    except RuntimeError as e:
        return index(str(e))
    return index(short=f"{_advertised_base()}/s/{code}", code=code)


@app.get("/s/<code>")
def go(code):
    url = _lookup_url(code)
    if url is None:
        abort(404)
    return redirect(url, code=302)


def qr_png(data: str) -> bytes:
    """Render data as a QR code PNG."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@app.get("/qr/<code>")
def qr(code):
    if _lookup_url(code) is None:
        abort(404)
    short = f"{_advertised_base()}/s/{code}"
    data = qr_png(short)
    return send_file(
        io.BytesIO(data),
        mimetype="image/png",
        as_attachment=bool(request.args.get("dl")),
        download_name=f"qr-{code}.png",
    )


@app.get("/qrtext")
def qrtext():
    data = request.args.get("data", "")
    if not data or len(data) > 1000:
        abort(400)
    return send_file(
        io.BytesIO(qr_png(data)),
        mimetype="image/png",
        as_attachment=bool(request.args.get("dl")),
        download_name="qr.png",
    )


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
        if mode == "pdf2img":
            return _extract_pdf_pages(saved)

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


def _extract_pdf_pages(saved: list[tuple[str, Path]]):
    """Render every page of the uploaded PDFs to PNGs (single file or ZIP)."""
    results: list[tuple[str, bytes]] = []
    for i, (orig_name, path) in enumerate(saved):
        if path.suffix.lower() != ".pdf":
            return index(f"{orig_name} is not a PDF.")
        try:
            pages = convert.pdf_to_images(path)
        except Exception as e:
            return index(f"Could not read {orig_name}: {e}")
        stem = Path(secure_filename(orig_name)).stem or f"pdf_{i}"
        for j, data in enumerate(pages, 1):
            results.append((f"{stem}-page-{j:02d}.png", data))
    if not results:
        return index("No pages could be extracted.")
    if len(results) == 1:
        name, data = results[0]
        return send_file(
            io.BytesIO(data), as_attachment=True, download_name=name, mimetype="image/png"
        )
    return _zip_and_send(results, "pdf-pages.zip")


def _zip_and_send(results: list[tuple[str, bytes]], download_name: str):
    """Bundle results into a ZIP and send it as a download."""
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
    return send_file(zbuf, as_attachment=True, download_name=download_name, mimetype="application/zip")


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

    return _zip_and_send(results, "images.zip")


if __name__ == "__main__":
    print(f"Serving on http://localhost:{PORT}  (Ctrl+C to stop)")
    app.run(host="0.0.0.0", port=PORT, debug=False)