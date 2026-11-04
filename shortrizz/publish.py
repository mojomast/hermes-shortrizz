"""Publishing interface (stub). Live posting is intentionally NOT implemented.

Each Publisher turns a clip + schedule into a request. `DryRun` mode (the default and only
enabled mode) writes publish_plan.json describing exactly what would be posted where/when.

To implement a real platform, subclass Publisher and fill in `upload()`:
  * YouTubeShorts: YouTube Data API v3 `videos.insert` (OAuth2 scope youtube.upload), resumable
    upload; set snippet.title (<=100 chars, add #Shorts), snippet.description, status.privacyStatus
    ="private" + status.publishAt (RFC3339) for scheduling. Needs a Google Cloud OAuth client.
  * TikTok: Content Posting API (`/v2/post/publish/video/init/`, FILE_UPLOAD or PULL_FROM_URL),
    requires an approved TikTok developer app + user OAuth (video.publish scope). Unaudited apps
    can only post privately.
  * InstagramReels: Instagram Graph API: POST /{ig-user-id}/media (media_type=REELS, video_url
    must be a public URL) then /{ig-user-id}/media_publish. Needs a Business/Creator account
    linked to a Facebook Page and a long-lived token.
Credentials must come from env vars / a secrets store outside the repo - never commit them.
"""
import datetime as dt, json, os
from pathlib import Path

class Publisher:
    name = "base"
    env_vars: list = []
    def enabled(self):
        return os.environ.get(f"SHORTRIZZ_ENABLE_{self.name.upper()}") == "1"
    def build_request(self, clip, when):
        return {"platform": self.name, "file": clip["file"], "scheduled_for": when.isoformat(),
                "title": clip["title"][:100], "caption": f"{clip['caption']} {' '.join(clip['hashtags'])}".strip()}
    def upload(self, request):  # hook: implement real API call here
        raise NotImplementedError(f"{self.name}: live upload not implemented (see module docstring)")

class YouTubeShorts(Publisher):
    name = "youtube"; env_vars = ["YOUTUBE_CLIENT_SECRETS_FILE", "YOUTUBE_TOKEN_FILE"]
    def build_request(self, clip, when):
        r = super().build_request(clip, when)
        t = clip["title"][:90]
        r.update(title=t if "#shorts" in t.lower() else f"{t} #Shorts", privacyStatus="private", publishAt=r["scheduled_for"],
                 description=r.pop("caption"))
        return r

class TikTok(Publisher):
    name = "tiktok"; env_vars = ["TIKTOK_CLIENT_KEY", "TIKTOK_ACCESS_TOKEN"]

class InstagramReels(Publisher):
    name = "instagram"; env_vars = ["IG_USER_ID", "IG_ACCESS_TOKEN"]

PUBLISHERS = {p.name: p for p in (YouTubeShorts(), TikTok(), InstagramReels())}

def plan(out_dir, platforms, start=None, every_hours=24, live=False):
    out_dir = Path(out_dir)
    clips = json.loads((out_dir / "clips.json").read_text())["clips"]
    start = start or (dt.datetime.now().astimezone().replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1))
    reqs = []
    for i, c in enumerate(sorted(clips, key=lambda c: -c["virality_score"])):
        when = start + dt.timedelta(hours=every_hours * i)
        for name in platforms:
            p = PUBLISHERS[name]
            r = p.build_request(c, when)
            r["status"] = "dry-run"
            if live:
                if not p.enabled():
                    r["status"] = f"skipped (set SHORTRIZZ_ENABLE_{name.upper()}=1 and implement upload())"
                else:
                    p.upload(r); r["status"] = "uploaded"
            reqs.append(r)
    (out_dir / "publish_plan.json").write_text(json.dumps({"generated": dt.datetime.now().astimezone().isoformat(),
                                                           "posts": reqs}, indent=2))
    return reqs
