# Video delivery

Ordinary video downloads keep the original streams at the selected resolution.
They do not automatically transcode Instagram VP9 to H.264.

The delivered video offers **Compatible version** (FA: **نسخهٔ سازگار**).
This starts another video download of the same source and playlist item, at the
same quality, with an explicit MP4 output request. Compatible H.264/yuv420p and
AAC-LC streams are reused; other streams are converted with a size-aware budget.
The bot does not infer the user's operating system.

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
