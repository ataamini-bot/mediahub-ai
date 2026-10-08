# MediaHub AI

[فارسی](README.fa.md) · **English** · [Русский](README.ru.md) · [简体中文](README.zh-CN.md)

A self-hosted Telegram bot for media downloads, audio extraction, file conversion, and subscription management, with Persian and English interfaces.

MediaHub brings media tools and administration into Telegram: users send a link and choose an output, while administrators manage plans, payments, customers, and reports from the bot.

This README is available in Persian, English, Russian, and Simplified Chinese. The bot interface currently supports Persian and English, and the installer uses English prompts.

## Project status

**Version 1 release candidate: `v1.0.0-rc.1`.** The current implementation is on the [`feature/admin-foundation`](https://github.com/ataamini-bot/mediahub-ai/tree/feature/admin-foundation) branch. This README describes that candidate; the repository's default branch may contain an earlier version.

The installation command below uses the pinned candidate commit `71ba5a1a5daa2183f730d5d82fdc00813b261283`, which passed [CI](https://github.com/ataamini-bot/mediahub-ai/actions/runs/37816641875). A stable v1 release remains subject to live acceptance and deployment checks.

## Features

### Media tools

- Download from supported public URLs, including YouTube, public Instagram content, TikTok, Facebook, X, Pinterest, and Threads.
- Select available video qualities up to 4K, subject to the source and configured plan.
- Extract audio, cover images, and descriptions when available.
- Convert uploaded media into supported video and audio formats.
- Receive files named after the source media title.
- Use details, download-again, and related-output buttons beneath delivered files.
- View download progress, transfer speed, file size, and estimated remaining time.

Available media and formats depend on the source website. Instagram support is limited to public content and does not use Instagram login cookies or personal sessions.

### Subscriptions and payments

- Configurable free and paid plans, subscription durations, and usage limits.
- Manual card-payment and USDT payment workflows.
- Receipt and transaction-ID review inside the private bot admin panel.
- Approved-payment reports in the Payments topic.
- Separate internal-credit balances for Toman and USDT, with transaction records and reversal entries.
- Discount codes with validity periods, eligibility rules, and usage limits.

Prices, payment destinations, plan settings, and discount rules are managed by the operator through the bot.

### Administration

- Persian and English user interfaces.
- Required membership in channels selected by the user's bot language.
- Customer search, account blocking, subscription management, and quota controls.
- Administrator roles, permissions, and activity logs.
- Broadcasts with audience and language selection, preview, confirmation, a persistent queue, and delivery reports.
- Download and payment statistics.
- Reporting topics for Monitoring, Payments, Backups, System, and Support.
- Scheduled encrypted backups and restore verification.

## Install on a new Ubuntu server

The installer targets **Ubuntu Server 22.04, 24.04, or 26.04 on amd64/x86_64**, with root or sudo access. It installs the required host tools and Docker components, then runs the application services through Docker Compose.

Prepare these values before starting:

| Value | Where it comes from |
| --- | --- |
| Bot Token | Create your bot with [@BotFather](https://t.me/BotFather) |
| Telegram API ID and API Hash | Register your application at [my.telegram.org/apps](https://my.telegram.org/apps) |
| Superadmin Telegram ID | The numeric user ID of the account that will administer the bot |
| Timezone | Default: `Asia/Tehran` |

The **Bot Token selects the bot** this installation will run. The superadmin ID is your Telegram user ID, not your phone number, bot ID, or API ID. The API ID and API Hash are required by the Local Bot API service.

Run this on a fresh server where `/opt/mediahub-ai` does not already exist:

```bash
(
  set -euo pipefail
  release='71ba5a1a5daa2183f730d5d82fdc00813b261283'
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl
  installer="$(mktemp /tmp/mediahub-install.XXXXXX)"
  trap 'rm -f "$installer"' EXIT
  curl --proto '=https' -fsSL \
    "https://raw.githubusercontent.com/ataamini-bot/mediahub-ai/${release}/install.sh" \
    -o "$installer"
  sudo bash "$installer" "$release" install
)
```

This starts installation directly. The untagged candidate builds its application images on the server. Follow the prompts and look for `INSTALLATION=OK` before continuing with Telegram group setup.

The installer generates the database password and application secrets. Keep a secure off-server copy of `/opt/mediahub-ai/.mediahub/recovery-key.txt`, separate from your backups; this key is needed to decrypt a backup during recovery.

After installation, open the bot in Telegram using the superadmin account, press **Start**, and open the admin panel.

## Set up the reporting group

1. Create a private Telegram group, such as **MediaHub Reports**.
2. Enable **Topics** in the group settings.
3. Add the exact bot whose token you entered during installation.
4. Make the bot an administrator with **Manage Topics** permission.
5. Enter the group's numeric ID when the installer asks for `Supergroup ID`.
6. Check the displayed group name and confirm to create the reporting topics.

For example, a private message link of `https://t.me/c/1234567890/5` corresponds to group ID **`-1001234567890`**. The final `5` is a message ID. Use the ID from your own group.

The installer creates **Monitoring**, **Payments**, **Backups**, **System**, and **Support** topics and saves their IDs. Required-membership channels can be added afterward, with an audience of `fa`, `en`, or `all`; the bot must be an administrator in those channels too.

To resume group setup later:

```bash
sudo mediahub telegram
```

## Manage the server

Open the server-management menu:

```bash
sudo mediahub
```

Terminal menus and prompts are in English. The bot itself supports Persian and English.

| Option | Action |
| --- | --- |
| 1 | Install MediaHub |
| 2 | Update to a stable release |
| 3 | Create and export backup |
| 4 | Import backup and restore |
| 5 | Telegram group, topics, and required channels |
| 6 | Check system health |
| 7 | Roll back application |
| 8 | Configure update source |
| 9 | Uninstall containers while keeping data |
| 0 | Exit |

For a command-line health report:

```bash
cd /opt/mediahub-ai
sudo bash scripts/check_launch.sh
```

`SERVER_CHECK=OK` confirms the scripted server checks passed. Test the live bot separately: start it, download a small file, and verify the admin panel and reporting topics.

## Backups, recovery, and updates

Backups include PostgreSQL data, application environment settings, and files from the application's `secrets` directory. Downloaded media, Redis queues, and Docker images are not included. Archives are encrypted and checked with isolated restore verification.

Use **Create and export backup** to produce a portable backup, and **Import backup and restore** to recover it. Keep the `.mhb` archive, its matching `.json` manifest, and the recovery key. Stop the old installation before moving the same bot to a new server.

Stable updates require a published release tag and matching container images. Update-source settings support an HTTPS Git repository and a container registry. The update process takes a verified backup before database migration and preserves previous application images for compatible rollback. Application rollback does not automatically downgrade the database.

See the [installation and recovery guide](https://github.com/ataamini-bot/mediahub-ai/blob/feature/admin-foundation/docs/INSTALLATION.md) for the complete procedures and the differences between restoring an existing installation and recovering onto a fresh server.

## Media cleanup and retained data

Downloaded and generated media files are temporary working files and are removed after delivery. Background cleanup handles eligible files left by interrupted or failed jobs; some recovery and cache-cleanup paths use retention windows.

Source URLs and download metadata remain in the database for operator activity logs, quotas, and administration. Payment, subscription, and internal-credit records are also retained. Users do not receive a download-history menu, and full source URLs are not shown in delivered-file details. Database backups include the retained records.

Deleting a server-side file does not remove the copy already delivered in Telegram. See [media-file cleanup](https://github.com/ataamini-bot/mediahub-ai/blob/feature/admin-foundation/docs/MEDIA_FILE_CLEANUP.md) for the exact behavior.

## Architecture

| Service | Responsibility |
| --- | --- |
| Bot | Telegram interface, user flows, and admin panel using aiogram |
| Backend | FastAPI application, business rules, and database access |
| Worker | Celery jobs for downloads and media processing |
| Monitor | Service monitoring and operational reports |
| Backup | Scheduled encrypted backups and restore verification |
| PostgreSQL | Persistent application data |
| Redis | Task queue and temporary state |
| Local Bot API | Telegram API service for local media transfer |
| bgutil-provider | YouTube proof-of-origin token service on the internal Docker network |

Media processing uses yt-dlp and FFmpeg. Database changes are managed with Alembic. Docker Compose defines the deployment.

## Troubleshooting

| Message or symptom | What to check |
| --- | --- |
| `Telegram getChat: Bad Request: chat not found` | Confirm the group ID and that the installed bot belongs to that exact group. Then rerun `sudo mediahub telegram`. |
| A Topics or administrator-permission error | Enable Topics on the group and grant the bot Manage Topics permission. |
| `Another installation or recovery operation is running` | An earlier menu or operation holds the lock. Close an idle earlier menu with `0`, or let an active operation finish. |
| The bot does not appear in your chats | Open the username belonging to the configured Bot Token and press Start. |
| No response after Start | Run the health check and check that the bot's token is not being used by another active installation. |
| `Destination exists` | Use the existing installation's management tools; the fresh-install bootstrap does not overwrite the directory. |

## Documentation and validation

The detailed guides below are currently written in Persian:

- [Installation, Telegram setup, updates, and recovery](https://github.com/ataamini-bot/mediahub-ai/blob/feature/admin-foundation/docs/INSTALLATION.md)
- [Launch operations and acceptance checks](https://github.com/ataamini-bot/mediahub-ai/blob/feature/admin-foundation/docs/LAUNCH_OPERATIONS.md)
- [Media filenames and related outputs](https://github.com/ataamini-bot/mediahub-ai/blob/feature/admin-foundation/docs/MEDIA_OUTPUTS.md)
- [Media conversion](https://github.com/ataamini-bot/mediahub-ai/blob/feature/admin-foundation/docs/MEDIA_CONVERSION.md)
- [Broadcasts](https://github.com/ataamini-bot/mediahub-ai/blob/feature/admin-foundation/docs/BROADCASTS.md)
- [Internal credit](https://github.com/ataamini-bot/mediahub-ai/blob/feature/admin-foundation/docs/INTERNAL_CREDIT.md)
- [Discount codes](https://github.com/ataamini-bot/mediahub-ai/blob/feature/admin-foundation/docs/COUPONS.md)
- [Media cleanup and activity records](https://github.com/ataamini-bot/mediahub-ai/blob/feature/admin-foundation/docs/MEDIA_FILE_CLEANUP.md)

CI covers backend, bot, and installer tests, plus an isolated Docker installation and backup/restore exercise. Live Telegram behavior and source-website availability require separate acceptance checks.

Report reproducible problems through [GitHub Issues](https://github.com/ataamini-bot/mediahub-ai/issues). Include the application commit, the affected feature, and the error message. Remove tokens, API hashes, encryption keys, private URLs, and payment details from any shared logs.
