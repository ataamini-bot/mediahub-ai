# Music recognition

Music recognition appears immediately after **Convert media** in Persian and English.
It is optional and disabled by default. Portrait generation and its proposed single-photo
checkout have been withdrawn and are not included in this version.

## Operator setup

After installing this version and applying migration `d39e0f102132`, run:

```bash
cd /opt/mediahub-ai
sudo python3 scripts/mediahub.py tools
```

The same form is option **10. Music recognition** in `sudo mediahub`.
Enable music recognition, choose `shazamio` (the default) or `audd`, and set daily attempt
limits. ShazamIO does not need an API key; only AudD asks for one privately. No recognition
request is made during setup. Settings take effect without restarting.

| Tool | Provider | Key | Default limits |
|---|---|---|---|
| Music recognition (default) | [ShazamIO 0.9.0](https://github.com/shazamio/ShazamIO) | None | 2 attempts/user/day, 30 across the bot/day |
| Music recognition (manual alternative) | [AudD](https://dashboard.audd.io/) | AudD API token | Same shared counters |

Existing installations with no explicit provider selection switch to ShazamIO on update.
Their enabled state and quotas remain unchanged, and a saved AudD key remains encrypted
but is not used unless the operator explicitly selects `audd`. An error or no match from
ShazamIO **never** falls back to AudD. No new database migration is needed for provider
selection; it is stored in the existing music configuration.

ShazamIO uses an unofficial interface. It has no purchased API token, but service
availability, accuracy and unlimited usage are not promised. The adapter makes at most
three recognition requests for distinct windows, does not rotate profiles/proxies,
does not retry service errors or follow redirects, and bounds the response size.
Each call is limited to 45 seconds, within one 90-second preparation/recognition budget.
It sends a fingerprint computed locally from each 12-second sample, rather than the audio
file itself. Input and sample files are removed by the existing cleanup flow.

AudD charges belong to the operator's account; configure its billing limits as well as
bot limits if enabling it. These are **attempt** limits, not money limits or charges to users'
internal balances. Counters reset at midnight in the bot's configured quota timezone
(Asia/Tehran by default). Failed, cancelled and uncertain attempts
count; the provider may already have processed them.

Keys are encrypted with the existing application settings encryption key, omitted from
public configuration, and included only as encrypted values in database backups. Preserve
the existing recovery key when moving installations. Do not paste API keys into support
messages, GitHub, or logs.

## Music workflow

1. Open **Identify music**, then send a public Instagram, YouTube, TikTok, X or Threads
   video/post link, or upload a voice message, audio file or video clip up to 25 MiB.
   Clear recorded music works best. Humming recognition is not promised.
2. Sending the input immediately starts recognition, without a separate confirmation
   message or button. ShazamIO checks up to three distinct 12-second windows from the first
   36 seconds, stopping at the first match. It checks another window only after a valid
   no-match response, never after a timeout, invalid response, authentication error or rate
   limit. Short trailing windows under eight seconds are skipped. The windows share a
   90-second preparation/recognition budget; cancellation is checked between calls. AudD
   still receives only the first 12 seconds in one paid request, with no automatic paid
   fallback. All windows belong to one user recognition allowance; a no-match request can
   therefore make up to three Shazam requests. Download allowances remain separate.
   Links use the existing site's extractor profile, including the YouTube PO-token provider.
   Only the first media item and up to 36 seconds (12 for AudD) are requested.
   The fetch is limited to 120 seconds and 25 MiB; private/inaccessible links
   fail visibly before recognition is called. Uploaded files remain supported in the same form.
3. The bot shows the identified artist/title and up to three matching public YouTube
   sources, filtered by title, artist, duration and unwanted cover/remix variants.
4. A strict title/artist match starts an **MP3 320 kbps** download, including ShazamIO
   results without reference duration. Artist/Topic and official-audio metadata are preferred.
   When reference duration is available it must agree; otherwise the independent source
   must be at least a minute long. Equally ranked copies with conflicting durations or
   loose identity matches still need source selection. Comparable copies use the first
   match. The automatic result shows artist/title without an extra selection prompt.
   **Other formats** on the delivered file offers M4A, WAV, AAC, FLAC, OGG and OPUS.
   Older requests without auto-download enabled remain manual. Preview or sample duration
   is never used as song duration.
5. The ordinary downloader fetches **that independent full-track source**. Normal download
   quotas and file-size limits apply; the stored link is included in admin activity exports.
   Audio-only formats remain preferred. For a requested audio output format such as MP3,
   if YouTube exposes only a combined video/audio format, the downloader uses that
   format from the same full-track source and converts
   its audio to the requested output. It does not fall back to the submitted clip, select
   a different song, or accept a video-only stream. The combined source can be larger,
   so normal transfer-size limits still apply. This fallback does not resolve HTTP 403
   or responses that contain no accessible audible format.

MP3 outputs are encoded at 320 kbps and 44.1 kHz, including low-sample-rate inputs that
would otherwise make the encoder silently use a lower bitrate. This does **not** turn a
lower-quality source into a lossless/master recording. The file name includes artist and
song title. Download/encoding failures are shown explicitly; a polling timeout provides
**Check download** for the existing job rather than another paid recognition attempt.
Concurrent/repeated result checks reuse the source/format job and claim Telegram delivery
once in the database. Uncertain Telegram delivery is not automatically repeated.

In every private-chat section and form stage, `/menu` (also `/منو`) restores the
persistent first-page keyboard. It is handled before payment, support and admin
forms, so it cannot be stored as form input. In media forms, other commands and
home buttons, including renamed FA/EN labels and custom buttons, reach their usual
handlers. Returning to the menu
clears the input form and removes unsubmitted local uploads. The Worker retains
ownership of queued files; leaving the form does not cancel an admitted request.

These providers identify music; they do not supply a licensed master audio file. Apple Music preview
URLs and the submitted clip are **never** substituted for the full track. If recognition
or a matching accessible source is unavailable, the bot says so. Title
and duration matching cannot guarantee an official recording; review the displayed source.
Downloads depend on the public source being accessible from the server. This feature does
not bypass login, DRM or access restrictions. WAV/FLAC conversion does not increase source
quality. Acquire any rights needed for your use.

## Restarts, cancellation and recovery

Requests have durable UUIDs. Global and per-user admission limits are protected by a
PostgreSQL lock. A Worker claims the request before calling a paid provider. Repeated
submissions or Celery redelivery cannot initiate the same paid operation twice. Paid
calls have bounded execution time and no automatic retry. Work interrupted after a claim
becomes uncertain; after 15 minutes the janitor closes it and removes its working files.
Use **Check result** after a bot restart. Cancellation suppresses delivery; it cannot undo
a request the provider has already received. Cancellation during link fetching is checked
before recognition. A link fetch failure does not call either provider, but still counts as a bot tool
attempt. Source links remain in the private database request, while temporary sample bytes
are removed. Link worker timeouts also terminate their FFmpeg subprocess group.

Backups exclude temporary media. Restoration cancels old tool requests instead of replaying them.

Update, rollback and uninstall refuse to interrupt pending or processing tool requests.

## Acceptance before enabling for customers

Automated tests use synthetic fixtures and stubbed providers; they incur no API charges.
The operator should perform a real music test after choosing the provider: verify that the
full track differs from the submitted sample, required formats work and temporary files
are removed. Recognition accuracy and source availability still require live acceptance.

References: [ShazamIO](https://github.com/shazamio/ShazamIO),
[AudD standard recognition](https://docs.audd.io/).

For a failed music test, safe stage diagnostics (no keys or source URLs) are available with:

```bash
sudo docker compose logs --since=30m worker bot |
  grep -E 'Music link sample ready:|Music recognition complete:|Music sources ready:|Music source search failed:|Music download failed:|Media download complete:|Telegram media sent:' |
  tail -30
```

Automated tests exercise the pinned ShazamIO fingerprint generator with synthetic audio
and intercepted HTTP responses. They do not prove access from the production server.
After updating, test one uploaded clip/voice and one supported public link there. No real
recognition request or paid test call is made by the installer or CI.

### Music presentation and source-download diagnostics

Music uses a compact processing/result card. When the uploaded media or the bounded
Instagram sample contains video, the worker makes a best-effort H.264/AAC preview
(at most 12 seconds, 480 pixels, 5 MiB) from that same input. A failed preview never
blocks recognition. The bot edits its caption with the result and sends MP3/M4A
full songs with Telegram's audio player, title and performer; other formats remain
documents. The preview is labelled as the user's clip and is never substituted for
the independently selected full song. Source choice remains required when matching
is ambiguous; missing recognition duration alone no longer requires an extra click.

Preview delivery is owner-checked and claimed in the database before sending; an
unknown Telegram outcome is never automatically repeated. The bot removes its local
preview after the attempt. Original recognition inputs are removed when the worker
finishes. Unclaimed previews expire after ten minutes, and cancelled requests remove
them. All new copy is in the existing FA/EN message editor.

No-match cards offer an explanation, another sample, and the existing support form.
New tickets and user replies also alert authorized support administrators privately;
see [Support notifications](SUPPORT_NOTIFICATIONS.md).
Requesting support does not add a song to Shazam's catalogue. The reference bot's
lyrics, similar songs, likes, weekly charts and catalogue submissions are not features
of this presentation change.

After a definitive failed download, Retry starts a fresh download through normal plan
admission, using the saved identification. It consumes the normal download allowance.
The button carries the failed job ID: repeated clicks reuse its replacement or report
that the admission is still in progress. A sending/uncertain/completed job cannot be
retried this way, so a lost Telegram acknowledgement cannot trigger duplicate delivery.

A source HTTP 403 is a download failure, even if an upstream message mentions FFmpeg.
Provider health and a search result alone do not prove that media bytes are accessible.
Inspect the last three music downloads' stored errors without making any external request:

```bash
docker compose exec -T -e DEBUG=false worker python - --latest --stored-only < scripts/diagnose_music_download.py
```

This reports stage and classified failure reason (for example source HTTP rejection,
missing format, conversion, disk space, encoder or plan file-size limit). Local encoder
failures also include bounded stderr with paths, URLs and credential lines redacted;
extractor/provider response bodies are not printed. A new network probe is separate from the stored
failure and cannot explain a previous failure by itself.
For an operator-selected audio job, this read-only check uses the worker's current
YouTube profile, fresh metadata and up to 1 KiB from each of at most two audio formats:

```bash
docker compose exec -T -e DEBUG=false worker python - "$FAILED_AUDIO_JOB_ID" "$CONTROL_AUDIO_JOB_ID" < scripts/diagnose_music_download.py
```

Set these variables to a failed job and a previously successful audio job, or omit
the control argument to inspect just one job.
The check does not modify jobs, write media, send Telegram messages or call recognition
providers. It stops on rate limits/authentication challenges/timeouts and prints only
versions, flags, format IDs and response status, never signed media URLs or token values.
Successful probes do not guarantee a complete transfer and the diagnostic itself
does not resolve source rejections. No client/proxy/cookie changes are part of this update.

If initial requests succeed but normal audio downloads still fail, a separate bounded
download check calls `YoutubeDL.download` with the stored job's format selection,
the current worker client/provider options, and the existing extractor cache (reads
only). It downloads one source file containing audio into a private temporary directory and
removes it after success, failure or timeout. It does not encode MP3, update the job,
consume quota, queue work or send Telegram messages:

```bash
docker compose exec -T -e DEBUG=false worker python - "$FAILED_AUDIO_JOB_ID" --download-check < scripts/diagnose_music_download.py
```

This check is limited to one attempt, 90 seconds, 32 MiB of media and 12 media requests.
Retries are disabled; it does not change clients, cookies, proxies or token providers.
It reports the actual ranges requested, the number of bytes received before failure,
HTTP status and cache/challenge flags, without signed URLs or tokens. It identifies
`source_kind=audio_only` or `audio_video` for a combined fallback. A `COMPLETE`
result confirms source transfer in that diagnostic process only. It does not prove
that a Celery process, audio encoding or Telegram delivery succeeded. The timestamp
printed by the diagnostic can be compared with the stored failure's timestamp.

To compare the production HTTP downloader's actual initial request (including its
Range, headers and request extensions) with the small-range probe, use:

```bash
docker compose exec -T -e DEBUG=false worker python - --latest --transfer-check < scripts/diagnose_music_download.py
```

This selects the newest failed job among the last three music downloads, or the most
recent if none failed. It makes at most four media requests from one fresh extraction,
reads at most 1 KiB from each response, and closes it before any file is written.
`probe=downloader` tests the real downloader's initial request; `probe=range_1k` tests
the previous small byte range against the same signed URL. Both use the same client
and session. Different results identify a request-dependent rejection, while a new
successful probe still cannot establish why an earlier transfer failed.

For `NO_FORMATS`, compare the raw format inventory before an audio-only selector
is applied:

```bash
docker compose exec -T -e DEBUG=false worker python - "$FAILED_AUDIO_JOB_ID" --formats-check < scripts/diagnose_music_download.py
```

This makes at most two metadata extractions within 90 seconds, first reading the
existing worker cache and then with cache disabled. It reports only aggregate counts
of audio-only, combined, video-only, image and DRM formats plus diagnostic flags.
It never writes extractor cache, downloads media, changes the job or sends a message.
The comparison stops on authentication, rate limits or timeouts. Because the two
responses are sequential, a difference is evidence to investigate, not proof that
the cache caused the original failure. Missing audio-only formats with combined
formats still available is different from an extraction returning only images.


## Plan quotas and editable messages

Admin → Plans → choose any plan (including Free) → Music recognition.
The music limit supports: `0` disables it, `-1` is unlimited, a positive integer up to
10000 is the allowance, and `default` inherits the operator's existing daily limit.
Choose Daily or Weekly. Periods use the configured quota timezone;
weekly resets at Monday 00:00. Current plan edits apply to subsequent admissions, without
resetting usage. Failed/cancelled/uncertain attempts count. The server-wide daily budget
continues to apply, including unlimited plans.

Admin → Bot settings → Main page buttons → Texts and button titles → language →
Music recognition messages edits all messages in this workflow. Search all message texts
also finds existing authored localization messages and start/menu templates. Search FA or EN
phrases; the first 30 matches are paginated. Required placeholders are displayed; `default`
restores an entry. General HTML fragments preserve their original tags. Arbitrary upstream
errors, user-entered content, file names and external metadata are not editable bot copy.
Settings are version-checked/audited and cached for up to 30 seconds. Consent defaults say
external music recognition service; end users do not see provider brands.

## Retired portrait feature

The menu button, image provider, configuration form, photo limits and all purchase paths
are removed. The migration removes its saved provider key/configuration and saved menu
entries. Historical request metadata is retained; the cleanup worker removes leftover
media and cancels legacy work without contacting a provider. Existing subscription and
payment schemas are unchanged. Old inline buttons cannot start an image request.
