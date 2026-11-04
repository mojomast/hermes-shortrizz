"""16:9 -> 9:16 reframing with face tracking + simple active-speaker detection.

Pass 1 (analysis): decode the clip at low res / low fps, detect faces (YuNet, Haar fallback),
link detections into tracks by position, and score each track's "mouth motion" (frame-diff energy
in the lower half of the face box) as an active-speaker cue.
Pass 2 (plan): per time step pick a target track (active speaker, else largest face) with hysteresis
and a minimum shot length -> camera *cuts* between speakers (like pro clippers) and smoothly follows
within a shot (dead-zone + EMA, no jitter). No faces at all -> blurred-background letterbox.
"""
import os, subprocess
from pathlib import Path
import numpy as np
import cv2
from .util import log, models_dir

YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
             "face_detection_yunet_2023mar.onnx")

def yunet_model():
    """Path to the YuNet face model, downloaded once into the models cache. None -> Haar fallback."""
    p = models_dir() / "face_detection_yunet_2023mar.onnx"
    if not p.exists() and os.environ.get("SHORTRIZZ_OFFLINE") != "1":
        try:
            import urllib.request
            log("downloading YuNet face model (230 KB)…")
            urllib.request.urlretrieve(YUNET_URL, p.with_suffix(".part")); p.with_suffix(".part").rename(p)
        except Exception as e:
            log(f"YuNet download failed ({e}); using Haar cascade")
    return p if p.exists() else None
AW = 960  # analysis width (mouth-motion needs enough face pixels)

class Detector:
    def __init__(self, w, h):
        self.yn = None
        m = yunet_model()
        if m is not None:
            self.yn = cv2.FaceDetectorYN.create(str(m), "", (w, h), 0.6, 0.3, 50)
        else:
            self.haar = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    def __call__(self, img):
        if self.yn is not None:
            _, f = self.yn.detect(img)
            return [] if f is None else [tuple(map(float, r[:4])) for r in f]
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return [tuple(map(float, r)) for r in self.haar.detectMultiScale(g, 1.1, 5, minSize=(24, 24))]

def read_frames(src, start, dur, w, h, fps=None):
    vf = f"scale={w}:{h}" + (f",fps={fps}" if fps else "")
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", f"{start}", "-t", f"{dur}", "-i", str(src),
                          "-vf", vf, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL)  # we may stop reading early (cut segments)
    n = w * h * 3
    while True:
        b = p.stdout.read(n)
        if len(b) < n:
            break
        yield np.frombuffer(b, np.uint8).reshape(h, w, 3)
    p.wait()

def audio_env(src, start, dur, fps):
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start}", "-t", f"{dur}", "-i", str(src), "-vn",
                          "-ac", "1", "-ar", "16000", "-f", "s16le", "-"], capture_output=True).stdout
    a = np.frombuffer(pcm, np.int16).astype(np.float32)
    hop = int(16000 / fps)
    n = len(a) // hop
    return np.sqrt(np.mean(a[:n * hop].reshape(n, hop) ** 2, 1)) if n else np.zeros(0)

def analyze(src, start, dur, sw, sh, fps=6):
    ah = int(round(sh * AW / sw / 2) * 2)
    det = Detector(AW, ah)
    tracks = []  # each: dict(cx, last_t, dets: {t: (x,y,w,h,motion)})
    prev = None
    for k, fr in enumerate(read_frames(src, start, dur, AW, ah, fps)):
        t = k / fps
        gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        for (x, y, w, h) in det(fr):
            if w < AW * 0.04:
                continue
            cx = x + w / 2
            m = 0.0
            if prev is not None:
                def diff(fy0, fy1):
                    y0, y1 = int(max(0, y + h * fy0)), int(min(ah, y + h * fy1))
                    x0, x1 = int(max(0, x + w * 0.25)), int(min(AW, x + w * 0.75))
                    if y1 <= y0 or x1 <= x0:
                        return 0.0
                    return float(np.mean(cv2.absdiff(gray[y0:y1, x0:x1], prev[y0:y1, x0:x1])))
                # mouth motion minus head/eye-region motion (cancels head turns & camera shake)
                m = max(0.0, diff(0.62, 0.95) - 0.8 * diff(0.2, 0.45))
            best = min(tracks, key=lambda tr: abs(tr["cx"] - cx), default=None)
            if best is None or abs(best["cx"] - cx) > max(w, AW * 0.08) or t - best["last"] > 3:
                best = {"cx": cx, "last": t, "dets": {}}; tracks.append(best)
            best["cx"] = 0.7 * best["cx"] + 0.3 * cx; best["last"] = t
            best["dets"][k] = (x, y, w, h, m)
        prev = gray
    nsteps = k + 1 if 'k' in locals() else 0
    scale = sw / AW
    tracks = [t for t in tracks if len(t["dets"]) >= max(2, nsteps * 0.05)]
    return tracks, nsteps, fps, scale, audio_env(src, start, dur, fps)

def plan(tracks, nsteps, fps, scale, sw, sh, crop_w, audio=None, min_shot=3.0, switch_ratio=1.6):
    """Return per-analysis-step crop x (source px) or None if no faces."""
    if not tracks or nsteps == 0:
        return None
    win = int(fps * 1.5)
    # active-speaker score per track per step = smoothed mouth motion, normalized by face size bias
    T = len(tracks)
    size = np.zeros((T, nsteps)); mot = np.zeros((T, nsteps)); cx = np.full((T, nsteps), np.nan)
    for i, tr in enumerate(tracks):
        for k, (x, y, w, h, m) in tr["dets"].items():
            size[i, k] = w * h; mot[i, k] = m; cx[i, k] = (x + w / 2) * scale
        # fill gaps in cx with last known (detector misses a frame)
        last = np.nan
        for k in range(nsteps):
            if np.isnan(cx[i, k]): cx[i, k] = last
            else: last = cx[i, k]
        first = np.where(~np.isnan(cx[i]))[0]
        if len(first): cx[i, :first[0]] = cx[i, first[0]]
    kern = np.ones(win) / win
    # active speaker cue: mouth motion weighted by audio loudness (talking mouths move when it's loud)
    if audio is not None and len(audio):
        au = np.zeros(nsteps); au[:min(nsteps, len(audio))] = audio[:nsteps]
        au = au / (np.percentile(au, 90) + 1e-6)
        mot = mot * np.clip(au, 0, 1.5)
    wkern = np.ones(int(fps * 3)) / int(fps * 3)
    mot_s = np.array([np.convolve(m, wkern, "same") for m in mot])
    pres = np.array([np.convolve((s > 0).astype(float), kern, "same") for s in size])
    size_s = np.array([np.convolve(s, kern, "same") for s in size])
    multi = (pres > 0.3).sum(0) >= 2
    score = np.where(multi, mot_s + 1e-3 * np.sqrt(size_s), np.sqrt(size_s)) * (pres > 0.3)
    cur, since = int(np.argmax(score[:, 0])), 0.0
    target = []
    for k in range(nsteps):
        best = int(np.argmax(score[:, k]))
        since += 1 / fps
        if best != cur and (score[cur, k] <= 0 or (since >= min_shot and score[best, k] > switch_ratio * score[cur, k])):
            cur, since = best, 0.0
        target.append(cur)
    if os.environ.get("SHORTRIZZ_DEBUG"):
        log("  speaker timeline: " + "".join(str(t) for t in target[::fps]))
    xs, x = [], None
    dead = crop_w * 0.08
    for k in range(nsteps):
        tx = cx[target[k], k]
        if np.isnan(tx):
            tx = x if x is not None else sw / 2
        if x is None or (k > 0 and target[k] != target[k - 1]):
            x = tx  # hard cut to new speaker
        elif abs(tx - x) > dead:
            x += (tx - x - np.sign(tx - x) * dead) * 0.25  # smooth follow outside dead-zone
        xs.append(float(np.clip(x - crop_w / 2, 0, sw - crop_w)))
    return xs

def render_frames(src, start, dur, sw, sh, fps, out_w=1080, out_h=1920, analysis=None, t_offset=0.0):
    """Yield output frames (out_h x out_w BGR). analysis: (xs, afps) or None for letterbox."""
    crop_w = int(round(sh * out_w / out_h / 2) * 2)
    if analysis is None or crop_w >= sw:
        for fr in read_frames(src, start, dur, sw, sh):
            yield letterbox(fr, out_w, out_h)
        return
    xs, afps = analysis
    for k, fr in enumerate(read_frames(src, start, dur, sw, sh)):
        t = k / fps + t_offset  # t_offset: where this sub-range starts inside the analysed range
        j = min(len(xs) - 1, int(t * afps))
        j2 = min(len(xs) - 1, j + 1)
        a = t * afps - j
        x = xs[j] if abs(xs[j2] - xs[j]) > crop_w * 0.3 else xs[j] * (1 - a) + xs[j2] * a  # don't blend cuts
        x = int(round(x))
        yield cv2.resize(fr[:, x:x + crop_w], (out_w, out_h), interpolation=cv2.INTER_LANCZOS4)

def letterbox(fr, ow, oh):
    h, w = fr.shape[:2]
    if abs(w / h - ow / oh) < 0.02:
        return cv2.resize(fr, (ow, oh), interpolation=cv2.INTER_AREA)
    bg_scale = max(ow / w, oh / h)
    bg = cv2.resize(fr, (int(w * bg_scale) + 1, int(h * bg_scale) + 1))
    y0, x0 = (bg.shape[0] - oh) // 2, (bg.shape[1] - ow) // 2
    bg = cv2.GaussianBlur(bg[y0:y0 + oh, x0:x0 + ow], (0, 0), 30)
    bg = (bg * 0.6).astype(np.uint8)
    fh = int(h * ow / w)
    fg = cv2.resize(fr, (ow, fh), interpolation=cv2.INTER_AREA)
    y = max(0, min(oh - fh, (oh - fh) // 2 - int(oh * 0.08)))
    bg[y:y + fh] = fg
    return bg

def make_plan(src, start, dur, sw, sh, mode="auto"):
    """mode: auto|face|center|letterbox. Returns (xs, afps) or None (letterbox)."""
    crop_w = int(round(sh * 1080 / 1920 / 2) * 2)
    if sw / sh <= 9 / 16 + 0.01:
        return None  # already vertical -> just scale (letterbox() is a no-op fit)
    if mode == "letterbox":
        return None
    if mode == "center":
        return [float((sw - crop_w) / 2)], 1.0
    tracks, n, afps, scale, au = analyze(src, start, dur, sw, sh)
    xs = plan(tracks, n, afps, scale, sw, sh, crop_w, au)
    if xs is None:
        log("  no faces found -> blurred letterbox")
        return None
    log(f"  face tracking: {len(tracks)} track(s), {n} samples")
    return xs, afps
