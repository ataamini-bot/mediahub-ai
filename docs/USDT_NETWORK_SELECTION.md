# USDT network selection

## Customer flow

- Persian payments keep the existing automatic round-robin card selection.
- English payments list every active USDT destination in `sort_order`, then ID order.
- Selecting a network shows only that destination's address and QR code.
- USDT destination selection does not increment or use the rotation counters.
- The selected destination is revalidated against the active Backend list and is
  validated again when the receipt is submitted.

## Deployment

This release has no database migration. Deploy Backend and Bot from the server:

```bash
git fetch origin feature/admin-foundation
git show <commit>:scripts/deploy_usdt_network_selection.sh \
  > /tmp/deploy-usdt-network-selection.sh
bash /tmp/deploy-usdt-network-selection.sh <commit>
```

The script requires the server to be on commit
`15e322f6e42ebb053661199a6dba4a98d696e5c5` with a clean worktree.
