import os
import tempfile
import time
import unittest
import json

from dexbot import signals_free as sf
from dexbot.sellsim import SOL

W = "WALLET1111111111111111111111111111111111111"
M = "MINT11111111111111111111111111111111111pump"


def tx(err=None, sol_pre=10_000_000_000, sol_post=8_999_995_000, tok_pre=0, tok_post=1_000_000, fee=5000, extra=None):
    pre = [{"owner": W, "mint": M, "uiTokenAmount": {"uiAmount": tok_pre}}]
    post = [{"owner": W, "mint": M, "uiTokenAmount": {"uiAmount": tok_post}}] + (extra or [])
    return {"blockTime": int(time.time()) - 5, "transaction": {"message": {"accountKeys": [{"pubkey": W}, {"pubkey": "X"}]}},
            "meta": {"err": err, "fee": fee, "preBalances": [sol_pre, 0], "postBalances": [sol_post, 0],
                     "preTokenBalances": pre, "postTokenBalances": post}}


class T(unittest.TestCase):
    def test_buy_confirmed_by_mint_and_payment(self):
        b = sf.parse_buy(tx(), W)
        self.assertEqual((b["mint"], round(b["paid_sol"], 4)), (M, 1.0))

    def test_not_a_buy(self):
        self.assertIsNone(sf.parse_buy(tx(err={"x": 1}), W))                                   # failed transaction
        self.assertIsNone(sf.parse_buy(tx(sol_post=10_000_000_000 - 5000), W))                # transfer in: nothing paid
        self.assertIsNone(sf.parse_buy(tx(tok_pre=1_000_000, tok_post=0, sol_post=11_000_000_000), W))   # a sell

    def test_wallet_list_needs_documented_source(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "w.json")
            json.dump([{"address": W, "label": "x"}], open(p, "w"))
            self.assertIn("LISTA LIPSA", sf.load_wallets(p)[1])
            json.dump([{"address": W, "label": "x", "source_url": "https://example.org/post", "source_note": "self-published"}], open(p, "w"))
            self.assertEqual(len(sf.load_wallets(p)[0]), 1)
        self.assertIn("LISTA LIPSA", sf.load_wallets(sf.WALLETS_FILE)[1])                       # repo ships an empty list

    def test_migration_needs_curve_and_amm_and_waits_6h(self):
        gt = lambda: [{"relationships": {"base_token": {"data": {"id": "solana_" + M}}}}]
        now_ms = time.time() * 1000
        pools = [{"dexId": "pumpfun", "pairAddress": "C", "pairCreatedAt": now_ms - 9e6},
                 {"dexId": "pumpswap", "pairAddress": "P", "pairCreatedAt": now_ms - 3600e3}]
        w = sf.MigrationWatch()
        ev = w.poll(gt, lambda m: (200, pools), verify=lambda m: m)
        self.assertEqual((len(ev), ev[0].amm_pool, ev[0].curve_dex, ev[0].status), (1, "P", "pumpfun", "MIGRATION_CANDIDATE"))
        self.assertFalse(ev[0].eligible_for_v3())                                              # 1 h after migration
        self.assertTrue(ev[0].eligible_for_v3(time.time() + 6 * 3600))
        self.assertEqual(w.poll(gt, lambda m: (200, pools), verify=lambda m: m), [])           # deduped by pool
        self.assertEqual(sf.MigrationWatch().poll(gt, lambda m: (200, pools[1:]), verify=lambda m: m), [])   # no curve -> nothing

    def test_verification(self):
        import base64
        def acct(complete):
            raw = b"\0" * 48 + bytes([complete]) + b"\0" * 20
            return {"value": {"owner": "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P", "data": [base64.b64encode(raw).decode(), "base64"]}}
        mk = lambda: sf.Migration(M, "P", "pumpswap", "C", "pumpfun", time.time(), time.time(), "t")
        rpc = lambda c: (lambda method, params: acct(c) if method == "getAccountInfo" else [{"blockTime": 1700000000}])
        v = sf.verify_pump_curve(mk(), rpc(1))
        self.assertEqual((v.status, v.migrated_at, v.curve_last_tx_at), ("MIGRATION", None, 1700000000.0))   # time not claimed
        self.assertEqual(v.migration_time_estimate, v.amm_created_at)
        self.assertEqual(sf.verify_pump_curve(mk(), rpc(0)).status, "MIGRATION_CANDIDATE")            # curve not complete
        m = sf.Migration(M, "P", "pumpswap", "D", "meteoradbc", time.time(), time.time(), "t")
        self.assertEqual(sf.verify_pump_curve(m, rpc(1)).status, "MIGRATION_CANDIDATE")                # no check for this curve type

    def test_pumpportal_disabled(self):
        self.assertFalse(sf.PUMPPORTAL_ENABLED)


if __name__ == "__main__":
    unittest.main()
