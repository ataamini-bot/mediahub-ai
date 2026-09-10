# Payment photo navigation fix

USDT payment instructions are sent as a QR-code photo. Telegram cannot apply
`edit_text` to a photo message, so the payment navigation callbacks now:

- edit the current message when it is a text message;
- send the replacement screen and delete the QR photo when it is a media
  message;
- remove the old inline keyboard if Telegram does not allow deleting the
  media message.

This fixes both `Choose another plan` and `Cancel` after selecting a USDT
network. The release only rebuilds and restarts the Bot and has no database
migration.
