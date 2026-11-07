# Video delivery

After choosing a video quality, the user picks their operating system for this
download. The bot keeps the resolution and playlist item they selected. It does
not detect or store a device preference.

| Selection | Video delivered |
| --- | --- |
| Android, Windows, Linux | Original streams, without a compatibility transcode |
| iPhone / iPad, macOS, Other / Not sure | Compatible MP4 (H.264 and AAC-LC when audio is present) |

Changing quality or opening the device menu does not start a job or consume
quota. Choosing a device creates one normal video download. Going back replaces
the previous buttons. The device prompt applies to fresh links, playlist videos,
and the video quality picker under a previous result. Audio, photos, covers,
descriptions and uploaded file conversions do not need a device selection.

Videos delivered with their original streams offer **Compatible version** (FA: **نسخهٔ سازگار**).
This starts another video download of the same source and playlist item, at the
same quality, with an explicit MP4 output request. Compatible H.264/yuv420p and
AAC-LC streams are reused; other streams are converted with a size-aware budget.

This action checks the requesting user's ownership, membership and plan limits,
and counts as a new download like **Download again**. A repeated download keeps
its original or compatible mode. Rapid repeated clicks share one active request.
Media bytes are still removed after delivery; the saved source link enables
follow-up requests after a restart. Existing uploaded-file conversion is unchanged.

Instagram quality selection reuses the extractor response within each metadata
request and within the worker download; no persistent media cache is introduced.

Diagnostic log messages:

- Worker: `Media download complete` includes extraction, transfer and merging.
- Worker: `Media output ready: ... mode=original` means no conversion was requested.
- Worker: `Video compatibility ready` records conversion/remux time when requested.
- Bot: `Telegram media sent` records the sendDocument call duration, not the time
  a Telegram client takes to download the file onto the user's device.

No database migration is required. The previous application version can be
restored with the existing MediaHub rollback command.
