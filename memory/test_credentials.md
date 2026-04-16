# Test Credentials

## Admin Account
- URL: http://localhost:3000 (local preview) or https://6ed76f3d-26e5-46cc-af72-4a2bee2ab07b.preview.emergentagent.com
- Email: test@scalpyn.com
- Password: TestPass123!

## Test Data (Preview Environment)
- Pool POOLGATE ID: d166102a-79e5-4824-9e02-13a2f046b819
  - Coins: BTC_USDT, ETH_USDT, SOL_USDT, DOG_USDT, XRP_USDT, LINK_USDT, ADA_USDT
- L1 Watchlist (L1-POOLGATE) ID: 56987c59-3354-4191-987d-9896d85a09c4

## Testing Notes
- Login with the credentials above to access the app
- Navigate to Watchlist page to see the pipeline watchlist system
- Navigate to Pools page to manage pool coins
- Use GET /api/watchlists/{id}/debug to see pipeline observability report
- Use POST /api/watchlists/{id}/refresh to trigger manual pipeline scan
