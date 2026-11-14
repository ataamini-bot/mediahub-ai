# Music recognition and portrait photos

Both tools appear immediately after **Convert media** in the Persian and English menus.
They are optional and **disabled by default**. Existing subscriptions, balances and download links are preserved. Single-portrait purchases are a separate product, initially disabled by a zero price.

## Operator setup

After installing this version and applying migration `d39e0f102132`, run:

```bash
cd /opt/mediahub-ai
sudo python3 scripts/mediahub.py tools
```

The same form is option **10. Music recognition and portrait photos** in `sudo mediahub`.
Choose the tool, enable it, enter its provider key privately, and set daily attempt limits.
No external paid request is made during setup. Settings take effect without restarting.

| Tool | Provider | Key | Default limits |
|---|---|---|---|
| Music recognition | [AudD](https://dashboard.audd.io/) | AudD API token | 2 attempts/user/day, 30 across the bot/day |
| Portrait editing | [OpenAI API](https://platform.openai.com/api-keys) | OpenAI API key with image editing access | 2 attempts/user/day, 30 across the bot/day |

Provider charges belong to the operator's account. Configure provider billing limits as
well as bot limits. These are **attempt** limits, not money limits or charges to users'
internal balances. Counters reset at midnight in the bot's configured quota timezone
(Asia/Tehran by default). Failed, cancelled and uncertain attempts
count; the provider may already have processed them. OpenAI's default model is
`gpt-image-2.6-flare`, medium quality, one 1024×1536 JPEG. Setup also allows
`gpt-image-2.6-sunburst` or `gpt-image-2` if available to that account.

Keys are encrypted with the existing application settings encryption key, omitted from
public configuration, and included only as encrypted values in database backups. Preserve
the existing recovery key when moving installations. Do not paste API keys into support
messages, GitHub, or logs.

## Music workflow

1. Open **Identify music**, then send a public Instagram, YouTube, TikTok, X or Threads
   video/post link, or upload a voice message, audio file or video clip up to 25 MiB.
   Music should be audible in the first 12 seconds. Humming recognition is not promised.
2. User confirms sharing a 12-second mono sample with AudD and downloading a sufficiently
   matching full track automatically. Links use the existing site's extractor profile,
   including the YouTube PO-token provider. Only the first media item and first 12 seconds
   are requested. The fetch is limited to 120 seconds and 25 MiB; private/inaccessible links
   fail visibly before AudD is called. Uploaded files remain supported in the same form.
3. The bot shows the identified artist/title and up to three matching public YouTube
   sources, filtered by title, artist, duration and unwanted cover/remix variants.
4. One unambiguous strict title/artist/duration match starts an **MP3 320 kbps** download.
   Otherwise, the result explicitly asks the user to press **Download MP3 320** beside a
   source. Missing reference duration no longer hides all candidates, but those sources
   require review and never start automatically. **Other formats** offers M4A, WAV, AAC,
   FLAC, OGG and OPUS. Older requests without auto-download consent remain manual.
5. The ordinary downloader fetches **that independent full-track source**. Normal download
   quotas and file-size limits apply; the stored link is included in admin activity exports.

MP3 outputs are encoded at 320 kbps and 44.1 kHz, including low-sample-rate inputs that
would otherwise make the encoder silently use a lower bitrate. This does **not** turn a
lower-quality source into a lossless/master recording. The file name includes artist and
song title. Download/encoding failures are shown explicitly; a polling timeout provides
**Check download** for the existing job rather than another paid recognition attempt.
Concurrent/repeated result checks reuse the source/format job and claim Telegram delivery
once in the database. Uncertain Telegram delivery is not automatically repeated.

AudD identifies music; it does not supply a licensed master audio file. Apple Music preview
URLs and the submitted clip are **never** substituted for the full track. If recognition,
duration metadata or a matching accessible source is unavailable, the bot says so. Title
and duration matching cannot guarantee an official recording; review the displayed source.
Downloads depend on the public source being accessible from the server. This feature does
not bypass login, DRM or access restrictions. WAV/FLAC conversion does not increase source
quality. Acquire any rights needed for your use.

## Portrait workflow

Send a clear, single-person front-facing JPG, PNG or WebP (up to 10 MiB, 20 megapixels).
Choose original clothing, suit, suit with a mandarin/diplomat-collar shirt, or shirt; then
choose white, light blue or grey. The confirmation describes external processing. The
edit prompt requests preserved identity and natural skin texture, with mild exposure and
contrast correction. It does not request facial reshaping. Check likeness before use;
acceptance for passports or other official documents is not guaranteed.

The bot strips EXIF metadata before the API call and sends the resulting image as a
Telegram document to avoid photo compression. Working inputs are removed when processing
finishes (including failures); the output is removed after delivery. Undelivered portrait
outputs expire after 30 minutes and unconfirmed uploads after one hour. These periods are
checked by the backend's 30-second cleanup loop. Source messages and Telegram's own storage
are outside this working-file cleanup. External provider retention is governed separately
by that provider; the UI does not claim immediate deletion by third parties.

## Restarts, cancellation and recovery

Requests have durable UUIDs. Global and per-user admission limits are protected by a
PostgreSQL lock. A Worker claims the request before calling a paid provider. Repeated
confirmations or Celery redelivery cannot initiate the same paid operation twice. Paid
calls have bounded execution time and no automatic retry. Work interrupted after a claim
becomes uncertain; after 15 minutes the janitor closes it and removes its working files.
Use **Check result** after a bot restart. Cancellation suppresses delivery; it cannot undo
a request the provider has already received. Cancellation during link fetching is checked
before recognition. A link fetch failure does not call AudD, but still counts as a bot tool
attempt. Source links remain in the private database request, while temporary sample bytes
are removed. Link worker timeouts also terminate their FFmpeg subprocess group.

Portrait delivery is claimed once. If Telegram's response is lost, the bot does not
regenerate or automatically resend it. Operators can inspect the request state; undelivered
files still expire. Backups never include this temporary media directory; restoration
cancels pending/processing/completed tool attempts instead of replaying provider calls.
Update, rollback and uninstall refuse to interrupt pending or processing tool requests.

## Acceptance before enabling for customers

Automated tests use synthetic fixtures and stubbed providers; they incur no API charges.
The operator should perform one real music and one real photo test after setting keys:
verify the full track differs from the sample, all required output formats work, facial
likeness is acceptable, the chosen clothing/background are correct, and temporary files
are removed. Provider availability, fees and real image quality require those live tests.

API references: [AudD standard recognition](https://docs.audd.io/),
[OpenAI image edits](https://developers.openai.com/api/docs/guides/image-generation).

For a failed music test, safe stage diagnostics (no keys or source URLs) are available with:

```bash
sudo docker compose logs --since=30m worker bot |
  grep -E 'Music link sample ready:|Music recognition complete:|Music sources ready:|Music source search failed:|Music download failed:|Media download complete:|Telegram media sent:' |
  tail -30
```

These tools use AudD for recognition. ShazamIO is an unofficial third-party client, not
a configured provider in this release. No provider changes or paid test calls happen
during this update.


## Plan quotas and editable messages

Admin → Plans → choose any plan (including Free) → Music recognition / Portrait photos.
Each tool has its own limit: `0` disables it, `-1` is unlimited, a positive integer up to
10000 is the allowance, and `default` inherits the operator's existing daily limit.
Choose Daily or Weekly separately for each tool. Periods use the configured quota timezone;
weekly resets at Monday 00:00. Current plan edits apply to subsequent admissions, without
resetting usage. Failed/cancelled/uncertain attempts count. The server-wide daily budget
continues to apply, including unlimited plans and individually purchased photos.

Admin → Bot settings → Main page buttons → Texts and button titles → language →
Music and portrait messages edits all messages in these workflows. Search all message texts
also finds existing authored localization messages and start/menu templates. Search FA or EN
phrases; the first 30 matches are paginated. Required placeholders are displayed; `default`
restores an entry. General HTML fragments preserve their original tags. Arbitrary upstream
errors, user-entered content, file names and external metadata are not editable bot copy.
Settings are version-checked/audited and cached for up to 30 seconds. Consent defaults say
external recognition/image-editing service; end users do not see provider brands.

## Single-portrait purchases, without subscriptions

Admin → Bot settings → One portrait price (toman / USDT). Set an exact price; zero disables
new sales in that currency. Both start at zero. Persian checkout uses toman/card or toman
internal credit; English checkout uses USDT/network or USDT internal credit. Subscription
coupons do not apply. Provider configuration must be available to offer new purchases.

User → Portrait photo → Buy one portrait → existing checkout → receipt/TxID review or
internal-credit confirmation. An approved payment grants exactly one portrait entitlement;
it never creates/extends/upgrades a subscription. Price and destination are frozen at order
creation. Duplicate approval or credit-payment retries cannot grant extra entitlements.
Entitlements have no expiry; the user explicitly chooses Use a purchased portrait before
uploading/confirming. Purchased attempts do not consume the plan allowance and work even
when the current plan disables portraits. They still respect global cost/concurrency limits.

A confirmed generation failure or cancellation before worker start restores the entitlement.
A completed result, cancellation after processing starts, or uncertain provider/delivery
outcome does not automatically restore or regenerate; support must review it. Restoration
of a backup returns unstarted reservations but cannot safely replay already-started work.
Temporary output retention is still 30 minutes; the unlimited entitlement expiry does not
mean generated media is stored indefinitely.

This migration allows non-subscription payment records. Application-only rollback to an
older release is disabled in the manifest. Roll back with the verified pre-update database
backup; do not discard purchases or force an older image against this schema.
