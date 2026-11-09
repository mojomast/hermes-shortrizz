import hashlib, json, os, subprocess, shutil, sys, time
from pathlib import Path

LOCAL_BIN = Path.home() / ".local" / "bin"

def which(name):
    p = LOCAL_BIN / name
    return str(p) if p.exists() else shutil.which(name)

def run(cmd, **kw):
    kw.setdefault("check", True)
    return subprocess.run([str(c) for c in cmd], **kw)

def log(msg):
    print(f"[shortrizz {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)

def sha256_file(path):
    """Hex SHA-256 of a file, read in 1 MiB chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_verified(url, dest, sha256, label):
    """Download ``url`` to ``dest`` and keep it only when the SHA-256 matches.

    A mismatch deletes the partial file and raises RuntimeError. ``dest`` is
    replaced only after the hash matches, so a bad download never sits at the
    final path.
    """
    import urllib.request
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        urllib.request.urlretrieve(url, part)
        digest = sha256_file(part)
        if digest.lower() != str(sha256).lower():
            raise RuntimeError(
                f"{label} SHA-256 mismatch: expected {sha256}, got {digest}. "
                "The download was rejected and not saved."
            )
        part.replace(dest)
    except Exception:
        part.unlink(missing_ok=True)
        raise


def ensure_verified(dest, url, sha256, label):
    """True when ``dest`` exists and matches ``sha256``.

    A file that fails the check is deleted and downloaded again. Returns False
    when the file is missing and ``SHORTRIZZ_OFFLINE=1`` (nothing is fetched).
    """
    dest = Path(dest)
    expect = str(sha256).lower()
    if dest.exists():
        if sha256_file(dest) == expect:
            return True
        log(f"{label} failed SHA-256 check; deleting it")
        dest.unlink()
    if os.environ.get("SHORTRIZZ_OFFLINE") == "1":
        log(f"{label} is missing and SHORTRIZZ_OFFLINE=1")
        return False
    download_verified(url, dest, expect, label)
    return dest.exists()


def ffprobe_duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
              capture_output=True, text=True).stdout
    return float(json.loads(out)["format"]["duration"])

def fmt_dur(s):
    return f"{int(s//60)}:{int(s%60):02d}"

def models_dir():
    d = Path(os.environ.get("SHORTRIZZ_MODELS", Path.home() / ".cache" / "shortrizz" / "models")).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d
