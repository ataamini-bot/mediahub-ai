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
availability, accuracy and unlimited usage are not promised. The adapter makes one
recognition request, does not rotate profiles/proxies, does not retry rate limits or
follow redirects, bounds the response size, and times out recognition after 45 seconds.
It sends a fingerprint computed locally from the 12-second sample, rather than the audio
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
   Music should be audible in the first 12 seconds. Humming recognition is not promised.
2. User confirms processing the first 12 seconds and sharing the required recognition
   data with the selected external service, and downloading a sufficiently
   matching full track automatically. Links use the existing site's extractor profile,
   including the YouTube PO-token provider. Only the first media item and first 12 seconds
   are requested. The fetch is limited to 120 seconds and 25 MiB; private/inaccessible links
   fail visibly before recognition is called. Uploaded files remain supported in the same form.
3. The bot shows the identified artist/title and up to three matching public YouTube
   sources, filtered by title, artist, duration and unwanted cover/remix variants.
4. One unambiguous strict title/artist/duration match starts an **MP3 320 kbps** download.
   Otherwise, the result explicitly asks the user to press **Download MP3 320** beside a
   source. Missing reference duration no longer hides all candidates, but those sources
   require review and never start automatically. **Other formats** offers M4A, WAV, AAC,
   FLAC, OGG and OPUS. Older requests without auto-download consent remain manual.
   ShazamIO results do not reliably include full-song duration, so they use explicit source
   selection via **Download MP3 320**. Preview or sample duration is never used as song duration.
5. The ordinary downloader fetches **that independent full-track source**. Normal download
   quotas and file-size limits apply; the stored link is included in admin activity exports.

MP3 outputs are encoded at 320 kbps and 44.1 kHz, including low-sample-rate inputs that
would otherwise make the encoder silently use a lower bitrate. This does **not** turn a
lower-quality source into a lossless/master recording. The file name includes artist and
song title. Download/encoding failures are shown explicitly; a polling timeout provides
**Check download** for the existing job rather than another paid recognition attempt.
Concurrent/repeated result checks reuse the source/format job and claim Telegram delivery
once in the database. Uncertain Telegram delivery is not automatically repeated.

These providers identify music; they do not supply a licensed master audio file. Apple Music preview
URLs and the submitted clip are **never** substituted for the full track. If recognition,
duration metadata or a matching accessible source is unavailable, the bot says so. Title
and duration matching cannot guarantee an official recording; review the displayed source.
Downloads depend on the public source being accessible from the server. This feature does
not bypass login, DRM or access restrictions. WAV/FLAC conversion does not increase source
quality. Acquire any rights needed for your use.

## Restarts, cancellation and recovery

Requests have durable UUIDs. Global and per-user admission limits are protected by a
PostgreSQL lock. A Worker claims the request before calling a paid provider. Repeated
confirmations or Celery redelivery cannot initiate the same paid operation twice. Paid
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
