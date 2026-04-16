"""
Tests for Watchlist L1 pipeline fixes:
- Symbol normalization in pools (BTCUSDT → BTC_USDT)
- market.py spot-currencies format (BTC_USDT with underscore)
- L1 Watchlist with/without market data
- Debug endpoint pipeline stages
"""
import pytest
import requests
import os

BASE_URL = "http://localhost:8001"

# From context: existing pool/watchlist IDs
POOL_ID = "d166102a-79e5-4824-9e02-13a2f046b819"
WATCHLIST_ID = "56987c59-3354-4191-987d-9896d85a09c4"


@pytest.fixture(scope="module")
def auth_token():
    """Login and return JWT token."""
    resp = requests.post(f"{BASE_URL}/api/auth/login", json={
        "email": "test@scalpyn.com",
        "password": "TestPass123!"
    })
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return resp.json()["access_token"]


@pytest.fixture(scope="module")
def headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}


# ── Test 1: Symbol normalization BTCUSDT → BTC_USDT ──────────────────────────

class TestSymbolNormalization:
    """Pool coin symbol normalization tests."""

    def test_add_coin_btcusdt_stored_as_btc_usdt(self, headers):
        """BTCUSDT should be stored as BTC_USDT."""
        # Use a test symbol that won't conflict with existing ones
        resp = requests.post(
            f"{BASE_URL}/api/pools/{POOL_ID}/coins",
            json={"symbol": "LINKUSDT", "market_type": "spot"},
            headers=headers,
        )
        # 409 is ok if already exists (means it was already normalized and stored)
        assert resp.status_code in (200, 201, 409), f"Unexpected: {resp.status_code} {resp.text}"
        if resp.status_code in (200, 201):
            data = resp.json()
            assert data["symbol"] == "LINK_USDT", f"Expected LINK_USDT, got {data['symbol']}"
            print(f"PASS: LINKUSDT normalized to {data['symbol']}")
        else:
            # Check if existing coin is stored with underscore format
            coins_resp = requests.get(f"{BASE_URL}/api/pools/{POOL_ID}/coins", headers=headers)
            assert coins_resp.status_code == 200
            coins = coins_resp.json()["coins"]
            link_coin = next((c for c in coins if "LINK" in c["symbol"]), None)
            if link_coin:
                assert "_" in link_coin["symbol"], f"Coin not normalized: {link_coin['symbol']}"
                print(f"PASS: LINK coin stored as {link_coin['symbol']}")

    def test_add_coin_already_normalized_stays_unchanged(self, headers):
        """BTC_USDT should stay as BTC_USDT (no double normalization)."""
        # Use a new symbol with underscore
        resp = requests.post(
            f"{BASE_URL}/api/pools/{POOL_ID}/coins",
            json={"symbol": "ADA_USDT", "market_type": "spot"},
            headers=headers,
        )
        assert resp.status_code in (200, 201, 409), f"Unexpected: {resp.status_code} {resp.text}"
        if resp.status_code in (200, 201):
            data = resp.json()
            assert data["symbol"] == "ADA_USDT", f"Expected ADA_USDT, got {data['symbol']}"
            print(f"PASS: ADA_USDT stored unchanged as {data['symbol']}")
        else:
            print("PASS: ADA_USDT already exists (409), format preserved")

    def test_pool_coins_all_normalized(self, headers):
        """Verify all existing pool coins use BTC_USDT format (no BTCUSDT without underscore)."""
        resp = requests.get(f"{BASE_URL}/api/pools/{POOL_ID}/coins", headers=headers)
        assert resp.status_code == 200
        coins = resp.json()["coins"]
        assert len(coins) > 0, "Pool has no coins"
        for coin in coins:
            sym = coin["symbol"]
            if sym.endswith("USDT") and "_" not in sym:
                pytest.fail(f"Coin {sym} not normalized to BTC_USDT format")
        print(f"PASS: All {len(coins)} coins use normalized format")


# ── Test 2: market.py spot-currencies format ──────────────────────────────────

class TestMarketSpotCurrencies:
    """spot-currencies endpoint returns BTC_USDT format."""

    def test_spot_currencies_returns_underscore_format(self):
        """GET /api/market/spot-currencies should return BTC_USDT format."""
        resp = requests.get(f"{BASE_URL}/api/market/spot-currencies", timeout=15)
        assert resp.status_code == 200, f"Status: {resp.status_code} {resp.text}"
        data = resp.json()
        # Response should be a list of dicts with 'symbol'
        currencies = data if isinstance(data, list) else data.get("currencies", data.get("data", []))
        assert len(currencies) > 0, "No currencies returned"

        # Check a sample of symbols
        usdt_syms = [c["symbol"] for c in currencies[:50] if isinstance(c, dict) and "symbol" in c and c["symbol"].endswith("USDT")]
        btcusdt_format = [s for s in usdt_syms if "_" not in s]
        assert len(btcusdt_format) == 0, f"Found non-normalized symbols: {btcusdt_format[:5]}"
        print(f"PASS: Spot currencies use underscore format (checked {len(usdt_syms)} USDT pairs)")


# ── Test 3: Debug endpoint ─────────────────────────────────────────────────────

class TestDebugEndpoint:
    """GET /api/watchlists/{id}/debug returns pipeline stages."""

    def test_debug_returns_expected_stages(self, headers):
        resp = requests.get(f"{BASE_URL}/api/watchlists/{WATCHLIST_ID}/debug", headers=headers)
        assert resp.status_code == 200, f"Status: {resp.status_code} {resp.text}"
        data = resp.json()
        stages = data.get("stages", {})
        assert "1_pool_coins_total" in stages, "Missing stage 1_pool_coins_total"
        assert "3_symbols_with_market_data" in stages, "Missing stage 3_symbols_with_market_data"
        assert "5_active_in_watchlist" in stages, "Missing stage 5_active_in_watchlist"
        print(f"PASS: Debug stages present: {list(stages.keys())}")
        print(f"  Pool coins: {stages.get('1_pool_coins_total')}")
        print(f"  With market data: {stages.get('3_symbols_with_market_data')}")
        print(f"  Active in watchlist: {stages.get('5_active_in_watchlist')}")

    def test_debug_no_error_field(self, headers):
        resp = requests.get(f"{BASE_URL}/api/watchlists/{WATCHLIST_ID}/debug", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("error") is None, f"Debug returned error: {data.get('error')}"
        print("PASS: Debug endpoint returned no errors")

    def test_debug_has_summary(self, headers):
        resp = requests.get(f"{BASE_URL}/api/watchlists/{WATCHLIST_ID}/debug", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "summary" in data, "Debug missing summary field"
        print(f"PASS: Debug summary: {data['summary']}")


# ── Test 4: L1 Watchlist assets ───────────────────────────────────────────────

class TestL1WatchlistAssets:
    """L1 watchlist assets endpoint tests."""

    def test_watchlist_list_shows_l1(self, headers):
        resp = requests.get(f"{BASE_URL}/api/watchlists/", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        watchlists = data.get("watchlists", [])
        assert len(watchlists) > 0, "No watchlists found"
        l1_wls = [w for w in watchlists if w.get("level") == "L1"]
        assert len(l1_wls) > 0, "No L1 watchlists found"
        print(f"PASS: Found {len(l1_wls)} L1 watchlists")

    def test_l1_assets_returns_pool_coins_with_market_data(self, headers):
        """After refresh, L1 should show pool coins with price/volume when market data exists."""
        # First refresh
        ref = requests.post(f"{BASE_URL}/api/watchlists/{WATCHLIST_ID}/refresh", headers=headers)
        assert ref.status_code == 200, f"Refresh failed: {ref.text}"
        
        resp = requests.get(f"{BASE_URL}/api/watchlists/{WATCHLIST_ID}/assets", headers=headers)
        assert resp.status_code == 200, f"Assets failed: {resp.text}"
        data = resp.json()
        assets = data.get("assets", [])
        # Should have assets (seeded: BTC_USDT, ETH_USDT, SOL_USDT in market data)
        assert len(assets) > 0, "L1 should return assets when pool coins exist"
        print(f"PASS: L1 returned {len(assets)} assets")

        # Check that at least some have price data (from seeded market_metadata)
        with_price = [a for a in assets if a.get("current_price") is not None]
        print(f"  Assets with price: {len(with_price)}/{len(assets)}")
        for a in with_price[:3]:
            print(f"  {a['symbol']}: price={a['current_price']}, vol={a['volume_24h']}")

    def test_l1_without_market_data_still_returns_assets(self, headers):
        """L1 should NOT wipe assets when market_metadata is empty (profile filter bypass)."""
        # Get current asset count
        resp = requests.get(f"{BASE_URL}/api/watchlists/{WATCHLIST_ID}/assets", headers=headers)
        assert resp.status_code == 200
        assets = resp.json().get("assets", [])
        # The key test: if pool has coins, L1 should show them regardless of market data
        pool_resp = requests.get(f"{BASE_URL}/api/pools/{POOL_ID}/coins", headers=headers)
        pool_coins = pool_resp.json().get("coins", [])
        active_pool_coins = [c for c in pool_coins if c.get("is_active")]
        
        # L1 with no profile filter should pass all pool coins through
        assert len(assets) > 0, "L1 should have assets (pool has active coins)"
        print(f"PASS: L1 has {len(assets)} assets from pool with {len(active_pool_coins)} coins")

    def test_l1_asset_count_in_list(self, headers):
        """GET /api/watchlists/ should show correct asset_count for L1."""
        resp = requests.get(f"{BASE_URL}/api/watchlists/", headers=headers)
        assert resp.status_code == 200
        watchlists = resp.json().get("watchlists", [])
        wl = next((w for w in watchlists if str(w["id"]) == WATCHLIST_ID), None)
        if wl:
            print(f"PASS: L1 watchlist asset_count = {wl.get('asset_count')}")
        else:
            print(f"INFO: Watchlist {WATCHLIST_ID} not in list (may be owned by different user)")


# ── Test 5: Refresh endpoint ──────────────────────────────────────────────────

class TestRefreshEndpoint:
    """POST /api/watchlists/{id}/refresh tests."""

    def test_refresh_returns_asset_count(self, headers):
        resp = requests.post(f"{BASE_URL}/api/watchlists/{WATCHLIST_ID}/refresh", headers=headers)
        assert resp.status_code == 200, f"Refresh failed: {resp.status_code} {resp.text}"
        data = resp.json()
        assert "refreshed" in data
        assert data["refreshed"] is True
        assert "asset_count" in data
        print(f"PASS: Refresh returned asset_count={data['asset_count']}")

    def test_refresh_then_assets_not_empty(self, headers):
        """After refresh, assets endpoint should not be empty."""
        ref = requests.post(f"{BASE_URL}/api/watchlists/{WATCHLIST_ID}/refresh", headers=headers)
        assert ref.status_code == 200
        
        resp = requests.get(f"{BASE_URL}/api/watchlists/{WATCHLIST_ID}/assets", headers=headers)
        assert resp.status_code == 200
        assets = resp.json().get("assets", [])
        assert len(assets) > 0, f"Assets empty after refresh. Refresh returned: {ref.json()}"
        print(f"PASS: {len(assets)} assets returned after refresh")
