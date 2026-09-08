# Admin navigation and payment statistics

This update builds on production commit `4d765f960473c864c3aa965a41f842279ba78fae`.
The user confirmed that Threads downloads work with that version.

- Roles and permissions now appear inside **Admin management**. Administrators
  with only `roles.manage` can reach role management without gaining access to
  administrator accounts; account changes still require `admins.manage`.
- Payment statistics first ask for a period: today, last seven days, current
  Gregorian month, current Gregorian year, or all time. Only the selected period
  is displayed, with separate approved Toman and USDT amounts. The existing
  backend calculation uses payment creation time and the configured quota
  timezone; changing the report does not change payment records.
- Admin form handlers run before home-menu label matching. A submitted title
  such as “خرید اشتراک” or “Buy subscription” stays in the editor. Copy-selection
  buttons carry an edit icon, and normal home-menu purchases still work.

## Validation and rollout

The bot suite includes router-level checks for editing Persian and English
titles, ordinary home navigation, administrator permission boundaries, report
selection, and denied or invalid statistics requests.

Run `PYTHONPATH=bot python -m pytest bot/tests -q`, Python compilation, and
`git diff --check` before committing. The rollout script must pass `bash -n`.

`scripts/deploy_admin_navigation.sh` accepts the full published commit SHA. It
requires a clean `feature/admin-foundation` checkout at the expected production
base, verifies the fetched target, saves a PostgreSQL backup and the running bot
image, then builds and checks the replacement bot. It refuses to restart while
downloads are pending or processing and checks again after stopping the bot.
Only the bot is recreated; the database schema stays at `c7e4d2a9f610`.
If a later step fails, it restores the old bot image tag and attempts to restart
that runtime. It does not reset the source checkout or overwrite local changes;
the failure log records the source HEAD for recovery.

## Pending product input

The separate **📊 Bot statistics** screen awaits the structure the user offered
to provide. It is not implemented by this update. Financial custom date ranges
and further report breakdowns in the product requirements remain future work.
