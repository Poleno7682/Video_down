from __future__ import annotations

# Codecs Telegram's own clients are known to mis-render inside an MP4
# container: ffmpeg decodes them fine (so validate_media_file's decode check
# passes), but mobile/desktop Telegram apps show only the first frame while
# audio keeps playing. See app/worker/downloader.py's ensure_telegram_compatible_video
# for the download-time transcode fallback that re-encodes these to H.264.
RISKY_TELEGRAM_VIDEO_CODECS = {"vp9", "av1"}

# Regexes (for yt-dlp's format filter `~=` operator) matching vcodec values,
# for the format selector's codec preference order: H.264 first, then HEVC,
# then any codec. Sites report the same codec differently — YouTube uses
# "avc1" for H.264, TikTok/others use "h264"; HEVC shows up as "h265",
# "hevc", "hvc1", or "hev1" depending on the extractor.
#
# H.264 and HEVC are both absent from RISKY_TELEGRAM_VIDEO_CODECS above (only
# vp9/av1 mis-render), so there's no *correctness* reason to avoid HEVC —
# but H.264 stays preferred over it, not merged into one tier: TikTok
# confirmed in production that its highest-resolution HEVC format can have
# vcodec metadata claiming audio (acodec=aac) when the stream actually has
# none, while a lower-resolution H.264 alternative for the same video was
# fine. HEVC is only reached as a fallback tier when no H.264 format exists
# at all (e.g. Facebook, which sometimes offers only HEVC/VP9), so a real
# quality-only-available-in-HEVC case (Facebook) doesn't route through the
# same tier as a codec TikTok has actually shown to mislabel.
H264_VCODEC_FILTER = "[vcodec~='^(avc1|h264)']"
HEVC_VCODEC_FILTER = "[vcodec~='^(hevc|h265|hvc1|hev1)']"
