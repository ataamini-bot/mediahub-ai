# Public Instagram downloads

An upstream HTTP 429 means Instagram is rate limiting access. It can affect
different public posts before the quality menu appears. It does not identify
which request or user caused the restriction, and the duration is not guaranteed.

The application stops at the first 429 observed by its Instagram yt-dlp session.
It does not run the normal metadata fallback or Celery retry loop for this error.
The same guard covers public video/photo extraction, worker downloads and the
Instagram link sample used by music recognition. YouTube and other sites retain
their existing profiles and retry behavior. No Instagram account cookies or
external proxy are introduced.

The cooldown is shared in Redis under `mediahub:instagram:cooldown`. A valid
`Retry-After` response header is respected; otherwise the application waits
15 minutes before allowing another attempt. Requests during the cooldown return
immediately and do not extend the deadline or contact Instagram. A further 429
starts another cooldown. This backoff reduces repeated requests; it cannot
remove an upstream block or promise that the next attempt will succeed.

New downloads and music-link submissions are rejected during a known cooldown
before creating a job or consuming an attempt. A request already admitted before
the upstream limit appears follows the existing failure/accounting rules.
Music samples are cleaned up on failure. File and voice recognition remain
available. The rate-limit message has editable Persian and English defaults in
the admin message catalogue.

Redis shares the deadline between containers and sample processes. During a
Redis outage, a process retains its last observed deadline locally; this fallback
cannot coordinate separate processes. Restoring Redis restores shared backoff.

## Read-only diagnosis

From `/opt/mediahub-ai`, print the remaining cooldown without requesting a post:

```bash
sudo docker compose exec -T backend python - <<'PY'
from app.services.instagram_access import remaining_seconds
print('INSTAGRAM_COOLDOWN_SECONDS=' + str(remaining_seconds()))
PY
```

Zero means the application permits an attempt, not that Instagram has lifted
the restriction. After the cooldown, test one public link on the operator's
server. Do not repeatedly probe posts during the cooldown, clear the Redis key
to force retries, or restart the stack to try to remove the restriction.

Automated tests use synthetic URLs and responses. They verify one-request
failure, cross-path backoff, expiry, response headers, worker cleanup and
localized messages. They do not establish live access from a production IP.
