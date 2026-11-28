# Request reuse and bounded processing

This release reduces repeated source requests. It does not remove Instagram,
YouTube or other providers' limits and is not a claim of million-user capacity.
Acceptance still requires the operator's own VPS and representative traffic.

## What is retained

- Temporary downloads, conversions and recognition samples keep their existing
  cleanup lifecycle. Sent media is deleted immediately after Telegram accepts
  it, even if delivery accounting is temporarily unavailable.
- PostgreSQL retains administrative activity, source links and small Telegram
  file references. Redis holds bounded, expiring metadata and recognition
  results. **Neither cache stores audio/video bytes. No archive channel is
  required.** Existing local Bot API input-cache cleanup remains enabled.
- Uploaded files and voice messages never enter shared caches. Instagram public
  posts and reviewed public music files can share output references. Other
  supported social links remain scoped to their requesting account unless the
  extractor explicitly reports public availability. Unlisted YouTube media is
  not shared between accounts.
- Identity includes the bot, source, carousel item, media type, selected format,
  quality, conversion/container and Telegram delivery kind. Output-pipeline
  changes must increment the key version in `output_key`.

## Processing and delivery

Concurrent identical requests retain separate owned jobs and quota reservations,
but join one source download. Followers do not read the leader's local file.
Only a successful Telegram response can publish its `file_id`. Each recipient
gets their own caption and controls. Actual cached size is checked against the
recipient's plan, and membership/plan/account gates remain in their existing flow.

Cancellation or failure of a leader permits one waiting follower to take over.
Pausing a follower does not pause the leader. Committed pending admissions can be
dispatched again after an API/broker interruption; a worker claims a job under a
database lock before source work. A durable delivery claim prevents concurrent
pollers from sending twice. Unknown Telegram-send results are not replayed.
Definitively rejected cached file IDs are invalidated and the user can request a
fresh download. Backup restoration clears reusable output references and cancels
undelivered cached jobs, preventing old queue/delivery replay.

Positive link-recognition results (identity and full-song candidates) are reused
for the metadata lifetime. Duplicate link recognitions wait for one producer.
When available, the already-sent preview is reused by Telegram file ID. Inputs
uploaded by users are excluded. Per-user and global recognition quotas still
apply to each admitted request. No new paid provider call or automatic paid
fallback is introduced. If no preview reference exists yet, the recognized-song
card can be shown without fetching another clip.

## Controls in the bot admin settings

| Setting | Default | Effect |
| --- | ---: | --- |
| Reuse public Telegram files | enabled | Enable reusable output references; uncertain visibility remains per user |
| Public file lifetime | 60 minutes | Expire output references and public-availability evidence |
| Download queue capacity | 200 | Bound downloads and recognition work awaiting/running; paused downloads and undelivered outputs count |
| Concurrent link checks | 4 | Shared Redis limit for metadata extraction |
| Link metadata lifetime | 120 seconds | Cache metadata and positive link-recognition results; zero disables these caches |
| Instagram request interval | 1000 ms | Space requests to Instagram metadata endpoints across processes; CDN media reads retain normal speed |
| Recognition request interval | 1000 ms | Space calls to the configured recognizer across processes |
| Download processes | 2 | Applied after a safe Worker restart; one separate process remains for recognition/short tasks |

Settings are validated and audited. Synchronous workers refresh source controls
within five seconds. To apply a process-count change, finish or cancel active
work, then run `docker compose restart worker`. The launcher supervises separate
`downloads` and `celery` consumers inside the existing Worker service, with one
reserved message per process. Installer, image rollback and volumes stay on the
existing service names.

Known Instagram cooldowns still prevent new source access. Warm metadata,
recognition results and confirmed Telegram outputs can be used without contacting
Instagram. Redis coordination failures do not open an uncontrolled Instagram
request burst. Queue-full and expired-reference messages are editable in FA/EN.

Recognition HTTP 429 responses establish a shared provider cooldown, respecting
`Retry-After` (60 seconds when absent). A shorter response never shortens an
existing cooldown. Fresh uncached attempts are rejected before reserving quota
while that cooldown is known; cached successful link results can still be used.
The affected call is not automatically repeated and no paid fallback runs.
`provider_busy` is distinct from `music_not_found`: the latter means recognition
completed without a match. Coordination reduces repeat traffic but cannot
guarantee Shazam availability or recognition accuracy.

Redis metadata and recognition indexes are bounded to 512 entries each; public
visibility evidence is bounded to 2,000 entries; the periodic cleanup trims
durable reusable outputs to 2,000 entries.
Metadata payloads are limited to 256 KiB and recognition results to 64 KiB.
Leases expire after process failure; metadata leases renew while extraction runs.

## Pinterest song recognition

The Music section accepts an individual Pinterest video pin or `pin.it` link.
Short-link hops are bounded and restricted to reviewed Pinterest domains; boards,
spoofed domains, credentials and private-address redirects are rejected. The
existing bounded sample path supplies ShazamIO (or the explicitly configured
AudD provider), then the ordinary full-song search/download pipeline runs. The
clip's extracted audio is never substituted for the complete song. A still image
or video without recognizable music cannot produce a song match. Samples and
preview files are cleaned up as before.

## Verification

Synthetic PostgreSQL/Redis tests exercise simultaneous identical and unique
requests, queue bounds, ownership, quota accounting, cancelled leaders, broker
interruption, duplicate delivery claims, expiry/restoration, source pacing,
metadata coalescing and cached recognition without sample/provider calls.
Bot tests check reference-only audio/document sends, immediate original-file
cleanup, uncertain sends and definitive invalid-reference errors. Pinterest tests
cover URL validation, bounded redirects and the 36-second sample configuration.
These tests do not contact production social networks or message real users.

Operator acceptance: recognize a public Pinterest video with music; repeat a
public Instagram link from two entitled test accounts; verify both receive the
correct independent controls and only one source transfer occurs for identical
outputs. Check `MEDIA_FILES_PENDING_CLEANUP=0` after delivery. Use a different
quality/container to verify it produces a separate rendition. Test cancel/pause
and queue limits before increasing concurrency. Live source availability and
actual VPS throughput must be measured separately from the synthetic checks.
