# Completion phase release

This release completes the operational payment, support, reporting, monitoring,
English UI, pagination, and confirmation flows.

## User-facing behavior

- English users see USDT plans and can choose any active USDT network. Persian
  users see the rotating IRT card flow.
- USDT submissions require a TxID and network. A screenshot is optional. The
  same normalized TxID cannot be submitted twice on the same network; review is
  still manual and no blockchain confirmation is claimed by the bot.
- Same-plan renewals add time and daily quota. Upgrades start immediately and
  preserve the exact remaining time. Downgrades and mixed changes start after
  all already-paid entitlements. Future scheduled purchases are shifted when a
  renewal extends the current entitlement.
- A user may have at most three non-closed tickets. Tickets support new,
  in-progress, waiting-for-user, answered, closed, assignment, reopening, and
  complete message/attachment history. New tickets and user replies are sent
  to the configured Support Topic.
- Download statistics use six selectable periods: **1 year, 6 months, 3
  months, 1 month, 7 days, and daily**. Completed files are grouped by the
  actual source hostname, with pagination. Finance statistics show IRT and USDT
  independently, including method, duration, plan, status, and change type.
- Finance CSV exports are UTF-8 with BOM, formula-neutralized, and capped at
  10,000 rows. A narrower range is required above that limit.
- Operations settings configure the notification group, five Topics, and
  monitoring thresholds from the admin panel. The monitor records Bot polling,
  Telegram API, Celery, disk, RAM, CPU, and download-error observations.
- Long text and inline menus paginate without dropping rows, links, Unicode, or
  attachments. Sensitive final actions are actor-, message-, and form-bound
  confirmations that expire after five minutes and cannot be replayed.

## Release smoke checks

After deployment, verify the following with one Persian and one English test
account:

1. Open Bot statistics and select all six periods. On Downloads, confirm that
   site names and counts change with the selected period and that page arrows
   expose every site.
2. Open Finance statistics. Confirm separate IRT and USDT totals, averages,
   renewal/initial split, method rows, and a CSV download for a custom date
   range.
3. Submit one USDT payment with a TxID and without a screenshot, then submit
   the same TxID again on the same network. The second submission must be
   rejected while the first remains pending review.
4. Create three tickets, close one, create a fourth, then reopen the closed
   ticket. Reopening must be refused while three tickets are open. Check the
   Support Topic and the complete history/attachment view.
5. Change a plan from the admin panel and press the final confirmation twice.
   The second click must be rejected. Repeat after five minutes to confirm
   expiry.
6. Open Operations, edit a threshold, use each Topic test button, and confirm
   the monitor snapshot updates without exposing the Bot token.

The deployment script creates a PostgreSQL custom-format backup before the
migration. Keep that archive until the release has passed the smoke checks.
The script intentionally does not run an automatic downgrade after a
successful migration; a failed post-migration health check requires forward
recovery using the recorded backup and logs.
