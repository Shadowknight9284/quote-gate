"""Replay tests on a hand-authored capture fixture (no network). Contract R1."""
import json
import os
import tempfile
import unittest

from quote_gate import coinbase_replay as cr

MS = 1_000_000


def snap(bids, asks):
    return {"type": "snapshot", "product_id": "BTC-USD", "bids": bids, "asks": asks}


def upd(changes):
    return {"type": "l2update", "product_id": "BTC-USD", "changes": changes, "time": "2026-10-10T00:00:00Z"}


HB = {"type": "heartbeat", "product_id": "BTC-USD", "sequence": 1, "last_trade_id": 1, "time": "t"}


def write_capture(d, msgs):
    """msgs: list of (session, mono_ms, message dict)."""
    with open(os.path.join(d, "raw.jsonl"), "w") as f:
        for i, (s, ms, m) in enumerate(msgs):
            f.write(json.dumps({"session": s, "i": i, "wall_ns": 0, "mono_ns": ms * MS, "raw": json.dumps(m)}) + "\n")
    with open(os.path.join(d, "manifest.json"), "w") as f:
        json.dump({"raw_sha256": "fixture", "product_metadata": {"quote_increment": "0.01",
                                                                 "base_increment": "0.00000001"}}, f)


# Strong bid imbalance -> target +5 lots (0.005 BTC). Sizes in BTC.
BASE = [
    (1, 0, {"type": "subscriptions"}),
    (1, 10, snap([["100.00", "0.09"], ["99.99", "1"]], [["100.01", "0.01"], ["100.02", "1"]])),
    (1, 20, upd([["buy", "100.00", "0.08"]])),
    (1, 30, upd([["sell", "100.01", "0"]])),            # best ask moves to 100.02 (size 1 BTC)
    (1, 40, HB),
    (1, 200, upd([["buy", "99.99", "2"]])),             # 160 ms after previous book msg
    (2, 300, snap([["101.00", "0.01"]], [["101.01", "0.09"]])),   # new session: rebuild, sell-side imbalance
    (2, 310, upd([["buy", "101.00", "0.02"]])),
]


class Replay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_normalization_sessions_and_tops(self):
        write_capture(self.d, BASE)
        events, stats = cr.normalize(self.d)
        kinds = [e["kind"] for e in events]
        self.assertEqual(kinds, ["session_start", "top", "top", "top", "top", "session_end",
                                 "session_start", "top", "top", "session_end"])
        tops = [e for e in events if e["kind"] == "top"]
        self.assertEqual((tops[0]["bid_ticks"], tops[0]["bid_units"], tops[0]["ask_ticks"], tops[0]["ask_units"]),
                         (10000, 9000000, 10001, 1000000))
        self.assertEqual((tops[2]["ask_ticks"], tops[2]["ask_units"]), (10002, 100000000))
        self.assertEqual(stats["heartbeats"], 1)
        self.assertEqual(stats["sessions"], 2)
        self.assertEqual(stats["book"]["snapshots"], 2)

    def test_deterministic_output(self):
        write_capture(self.d, BASE)
        a = cr.run(self.d, os.path.join(self.d, "o1"))
        b = cr.run(self.d, os.path.join(self.d, "o2"))
        self.assertEqual(a["ingestion"]["normalized_sha256"], b["ingestion"]["normalized_sha256"])
        self.assertEqual(a["report"], b["report"])

    def test_hypothetical_execution_units_and_fees(self):
        write_capture(self.d, BASE)
        events, _ = cr.normalize(self.d)
        r = cr.replay(events, fee_bp=10, delay_events=0)
        # Hand trace: buy 5 lots @10001 (imb +0.8), sell 5 @10000 (imb -0.85 after ask moves),
        # sell 5 @10000 again (still -0.85), session 2 at target. Fees ceil(n*bp/1e4): 51+50+50.
        self.assertEqual((r["orders"], r["filled"], r["rejected"]), (3, 3, {}))
        self.assertEqual(r["fees_usd"], "0.00151")
        self.assertEqual(r["final_position_lots"], -5)
        self.assertEqual(r["realized_pre_fee_usd"], "-0.00005")
        # equity: realized -5 - fees 151 + unrealized (10000-10101)*5 = -661 (1e-5 USD);
        # hypothetical liquidation fee ceil(10101*5*10/1e4) = 51
        self.assertEqual(r["pre_fee_terminal_liquidation_value_pnl_usd"], "-0.0051")
        self.assertEqual(r["post_fee_terminal_liquidation_value_pnl_usd"], "-0.00712")
        self.assertEqual((r["label"], r["policy"]["lot_btc"], r["max_abs_position"]), (cr.LABEL, "0.001", 5))

    def test_insufficient_displayed_size_rejects(self):
        msgs = [(1, 0, snap([["100.00", "0.09"]], [["100.01", "0.00400000"]]))]   # 0.004 BTC < 5 lots
        write_capture(self.d, msgs)
        events, _ = cr.normalize(self.d)
        r = cr.replay(events, fee_bp=0, delay_events=0)
        self.assertEqual((r["filled"], r["rejected"]), (0, {"insufficient_quantity": 1}))

    def test_heartbeat_does_not_refresh_and_stale_blocks_decisions(self):
        # After the 160 ms gap (heartbeat in between) the decision at 200 ms is on a fresh message;
        # but a decision is only made on book messages, never on heartbeats.
        write_capture(self.d, BASE)
        events, _ = cr.normalize(self.d)
        r = cr.replay(events, fee_bp=0, delay_events=0)
        total_decisions = sum(r["decisions"].values())
        self.assertEqual(total_decisions, 6)                 # one per top event; none for heartbeat

    def test_session_end_holds_no_fake_close(self):
        write_capture(self.d, BASE)
        events, _ = cr.normalize(self.d)
        r = cr.replay(events, fee_bp=0, delay_events=0)
        # session 2 flips the signal to sell; position changes only via filled orders
        self.assertEqual(r["orders_unresolved_at_end"], 0)
        self.assertEqual(r["decisions"], {"order": 3, "at_target": 3})
        self.assertEqual(r["final_position_lots"], -5)

    def test_no_future_information(self):
        write_capture(self.d, BASE)
        full, _ = cr.normalize(self.d)
        write_capture(self.d, BASE[:5])
        prefix, _ = cr.normalize(self.d)
        tops_full = [e for e in full if e["kind"] == "top"][:3]
        tops_prefix = [e for e in prefix if e["kind"] == "top"]
        self.assertEqual(tops_full, tops_prefix)
        rf = cr.replay([e for e in full if e.get("i", 0) < 5 or e["kind"] != "top"][:4], 0, 0)
        rp = cr.replay(prefix[:4], 0, 0)
        self.assertEqual((rf["orders"], rf["filled"]), (rp["orders"], rp["filled"]))

    def test_invalid_batch_creates_interval_and_stops_tops(self):
        msgs = BASE[:3] + [(1, 25, upd([["buy", "100.001", "1"]])), (1, 30, upd([["buy", "99.98", "1"]]))]
        write_capture(self.d, msgs)
        events, stats = cr.normalize(self.d)
        self.assertEqual(len(stats["invalid_intervals"]), 1)
        self.assertIn("not a multiple", stats["invalid_intervals"][0]["reason"])
        self.assertEqual(sum(e["kind"] == "top" for e in events), 2)          # nothing after invalidation
        self.assertEqual(stats["book"]["updates_ignored_invalid"], 1)


if __name__ == "__main__":
    unittest.main()
