# Full-song download sources

These sources are searched **after** ShazamIO/AudD returns an artist and title. They
do not replace recognition. Persian and foreign titles are searched across the same
active source pool; there is no filtering by the user's UI language or by site language.
No new API key is required for the public-site adapters.

## Behaviour

- Public searches run in a pool of six workers beside the existing YouTube lookup.
  A 30-second catalogue budget, at most 18 matching post checks, and cooperative
  cancellation prevent a long sequential chain. Every active source's search is
  submitted before post resolution; work still queued when the budget expires is cancelled.
  Stop after two verified catalogue matches; retain a YouTube alternative when available.
- Match artist/title words in search results and in the actual MP3 filename. Strip only
  known source branding and quality annotations. Reject different artists, extra cover/remix
  variants, ambiguous download links, player-only URLs and locked/preview download boxes.
- A bounded prefix probe verifies native MP3 320 and estimates length without retaining
  media. Reference length must agree when available; otherwise a source must last at least
  one minute. A filename or a `320` label alone is insufficient.
- Five-minute shared Redis metadata caching avoids repeated searches. Negative results
  expire after 30 seconds; local fallback is bounded to 128 entries. Cache keys contain
  a hash of public artist/title/duration, never Telegram identifiers. No media is cached.
- HTTP 429 honours bounded Retry-After cooldowns (60–3600 seconds, 300 by default);
  HTTP 401/403 backs off that source for five minutes. Other sites remain available.
  Failed source transfers do not automatically repeat. An operator can inspect inventory
  without contacting any website using:

  ```bash
  docker compose exec -T -e DEBUG=false worker python -m app.services.music_sources
  ```

- Only reviewed HTTPS site/CDN hosts are followed, including every redirect. No cookies,
  credentials, authentication, signed preview links or subscription workarounds are used.
- The normal job owns the download, enforces the plan's size/quota limits, records the
  source for admin activity exports, checks stop/cancel and claims Telegram delivery once.
  The full MP3 is validated before completion. Native MP3 320 remains unchanged; other
  output formats use the existing converter. Temporary and delivered files retain the
  normal cleanup lifecycle. Recognized clips never become substitutes for full songs.

## Reviewed inventory

`Public adapter` means the public search form and an MP3 download route were observed.
It does not promise every title is present or every server can access the site. A missing
match, changed HTML, lower-bitrate file or blocked source is skipped. No claim is made
that a label proves a studio master or an official upload.

| Website | State in this version |
|---|---|
| musics-fa.com | Existing public adapter |
| rozmusic.com | Public adapter |
| upmusics.com | Public adapter |
| musicbaran1.ir | Public adapter |
| pop-music.ir | Public adapter; HTTP 403 also observed during review |
| musicdel.ir | Public adapter |
| sevilmusics.com | Public adapter |
| bir-music.com | Public adapter |
| nicmusic.net | Public adapter |
| worldofmusic.ir | Inactive: guest download flow requests subscription |
| www.musicdagh.ir | Inactive: direct download route not verified; reviewed post returned HTTP 403 |
| dibasmusic.com | Public adapter |
| play.radiojavan.com | Inactive: public full-file download integration not verified |
| remixgram.com | Inactive: guest links are demos; full downloads request subscription |
| pinkmusics.com | Public adapter |
| vipremix.ir | Inactive: full downloads request subscription |
| radiojavanhd.com | Public adapter; independent from Radio Javan |
| musictag.ir | Public adapter |
| newsong.ir | Public adapter |
| soonami.ir | Public adapter; only an exact recognized version can match |
| musictops.ir | Public adapter |
| musicviral.ir | Public adapter |
| melodya.ir | Public adapter |
| musics-mehr.com | Public adapter |
| moonmusic.in | Inactive: supplied address currently describes a Discord bot, not a download catalogue |

The inactive entries are retained explicitly, not silently claimed as working sources.
Enabling one needs a verified public full-track integration or a documented authorized
API; changing a status alone is insufficient. The source registry fixes site/CDN hosts
per adapter and is not an arbitrary URL-fetching setting.

## Acceptance

Send a Persian song and a foreign song in the music form, then verify that the received
file is the matching full track. The source-choice card identifies the selected website;
worker logs show `source=<catalogue key>` and `mode=public_mp3_320`. Also check another
output format and that pause/cancel and plan limits work. Automated fixtures use synthetic
music and do not send Telegram messages or establish continued live source availability.
