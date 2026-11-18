import base64
import struct
import unittest
from unittest import mock

from dexbot.live import PUMP_PROGRAM, LiveFeed
from dexbot.variants import V1, V2, make


def curve_account(real_quote, complete=0, owner=PUMP_PROGRAM):
    raw = b"\0" * 8 + struct.pack("<5Q", 1, 1, 1, real_quote, 1) + bytes([complete]) + b"\0" * 117
    return {"value": {"owner": owner, "data": [base64.b64encode(raw).decode(), "base64"]}}


ROW = {"pairAddress": "CURVE", "quoteToken": {"address": "Q"}, "priceUsd": "0.00002", "priceNative": "0.0000002"}  # quote = $100


class T(unittest.TestCase):
    def feed(self, account, decimals=9):
        f = LiveFeed(make(V1))
        f._rpc = mock.Mock(side_effect=lambda m, p: account if m == "getAccountInfo" else {"value": {"decimals": decimals}})
        return f

    def test_curve_liquidity_two_sided_usd(self):
        self.assertAlmostEqual(self.feed(curve_account(30 * 10 ** 9))._curve_liquidity(ROW), 2 * 30 * 100)

    def test_curve_unknown_cases(self):
        self.assertIsNone(self.feed(curve_account(10 ** 9, owner="X"))._curve_liquidity(ROW))       # not a pump curve
        self.assertIsNone(self.feed(curve_account(10 ** 9, complete=1))._curve_liquidity(ROW))      # migrated
        self.assertIsNone(self.feed(curve_account(10 ** 9), decimals=None)._curve_liquidity(ROW))   # unknown decimals
        f = LiveFeed(make(V1))
        f._rpc = mock.Mock(side_effect=OSError("down"))
        self.assertIsNone(f._curve_liquidity(ROW))                                                  # RPC failure

    def test_security_gate_uses_loosest_variant(self):
        f = LiveFeed(make(V1), gate_cfgs=[make(V1), make(V2)])
        self.assertEqual((f.gate_vol, f.gate_liq), (10_000, 20_000))
        self.assertEqual((make(V1).min_vol5m, make(V1).min_liq), (25_000, 40_000))   # thresholds themselves unchanged


if __name__ == "__main__":
    unittest.main()
