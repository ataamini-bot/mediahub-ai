# Audio extraction and media conversion

The bot supports two related flows:

- A video link can expose **Extract audio** beside the quality choices. The
  user selects MP3, M4A, WAV, AAC, FLAC, OGG, or OPUS; the worker downloads
  the best available audio stream and produces the selected final container.
- **Convert media** in the home menu accepts a Telegram audio/video upload.
  FFprobe determines the available streams, then the user chooses an output
  format. Audio inputs expose audio targets; video inputs expose video targets
  and audio targets only when an audio stream exists.

Video targets are MP4, MKV, AVI, MOV, and WEBM. The allow-list is enforced in
the bot, API schema, service, database check constraint, and worker command
builder; arbitrary FFmpeg muxers/codecs are never accepted.

Uploads are stored temporarily under `/app/downloads/incoming` with a random
filename and an `upload://` job reference. The worker validates the reference,
keeps the input during retry, and removes it after success, cancellation, or
final failure. The existing 1900 MB plan/file limit, concurrency control,
Celery queue, progress reporting, and successful-delivery quota accounting
apply to conversion jobs as well.

The migration is `f9c0d1e2f3a4`. Use `scripts/deploy_media_conversion.sh` for
the production rollout; it creates a PostgreSQL custom-format backup, checks
the running services, applies the migration, and recreates Backend, Worker,
Monitor, and Bot with smoke checks.
