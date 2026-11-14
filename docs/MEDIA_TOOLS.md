# Music recognition and portrait photos

Both tools appear immediately after **Convert media** in the Persian and English menus.
They are optional and **disabled by default**. Existing subscriptions, wallet balances,
download quotas and stored download links are unchanged.

## Operator setup

After installing this version and applying migration `c28d9e0f1021`, run:

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

1. User sends a voice message containing recorded music, an audio file, or a video clip,
   up to 25 MiB. Music should be audible at the start. Humming recognition is not promised.
2. User confirms sharing a 12-second mono sample with AudD.
3. The bot shows the identified artist/title and up to three matching public YouTube
   sources, filtered by title, artist, duration and unwanted cover/remix variants.
4. User reviews the source and selects MP3, M4A, WAV, AAC, FLAC, OGG or OPUS.
5. The ordinary downloader fetches **that independent full-track source**. Normal download
   quotas and file-size limits apply; the stored link is included in admin activity exports.

AudD identifies music; it does not supply a licensed master audio file. Apple Music preview
URLs and the submitted clip are **never** substituted for the full track. If recognition,
duration metadata or a matching accessible source is unavailable, the bot says so. Title
and duration matching cannot guarantee an official recording: user review remains necessary.
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
a request the provider has already received.

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
