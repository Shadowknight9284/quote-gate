"""Fill check (quote_gate.fillcheck): CSV parsing, reconciliation categories and FIFO economics."""
import os
import unittest
from decimal import Decimal

from quote_gate import fillcheck as fc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def csv(*lines):
    return "\n".join(lines) + "\n"


BROKER = csv("order_id,side,quantity,price,fee",
             "A1,buy,2,100.00,0.20",
             "A2,sell,1,101.50,0.10")


class Parsing(unittest.TestCase):
    def test_header_synonyms_are_detected(self):
        m = fc.detect_mapping(["Client Order ID", "Action", "Filled Qty", "Avg Price", "Commission", "Created At"])
        self.assertEqual(m, {"order_id": "Client Order ID", "side": "Action", "quantity": "Filled Qty",
                             "price": "Avg Price", "fee": "Commission", "time": "Created At"})

    def test_signed_quantity_without_side_column(self):
        log = fc.parse(csv("id,qty,price", "x,3,10", "y,-2,11"))
        self.assertEqual([r.signed_qty for r in log.rows], [Decimal(3), Decimal(-2)])

    def test_bad_rows_are_reported_not_fatal(self):
        log = fc.parse(csv("order_id,side,quantity,price", "a,buy,1,10", "b,hold,1,10", "c,buy,0,10",
                           "d,buy,1,-5", "e,buy,abc,10", ",buy,1,10"))
        self.assertEqual(len(log.rows), 1)
        self.assertEqual([n for n, _ in log.invalid], [3, 4, 5, 6, 7])

    def test_money_formatting_and_rebates(self):
        log = fc.parse(csv("order_id,side,quantity,price,fee", 'a,buy,"1,000",$1.25,-0.01'))
        r = log.rows[0]
        self.assertEqual((r.signed_qty, r.price, r.fee), (Decimal(1000), Decimal("1.25"), Decimal("-0.01")))

    def test_required_columns_and_explicit_mapping(self):
        with self.assertRaises(fc.FillCheckError):
            fc.parse(csv("ref,qty", "a,1"))
        log = fc.parse(csv("ref,amt,rate", "a,1,10"), {"order_id": "ref", "quantity": "amt", "price": "rate"})
        self.assertEqual(log.rows[0].order_id, "a")
        with self.assertRaises(fc.FillCheckError):
            fc.parse(csv("ref,amt,rate", "a,1,10"), {"order_id": "nope", "quantity": "amt", "price": "rate"})

    def test_size_limit(self):
        with self.assertRaises(fc.FillCheckError):
            fc.parse("order_id,quantity,price\n" + "a,1,1\n" * (fc.MAX_BYTES // 6 + 1))


class Reconciliation(unittest.TestCase):
    def run_check(self, bot, **kw):
        return fc.check(fc.parse(BROKER), fc.parse(bot), **kw)

    def test_identical_logs_are_clean(self):
        r = self.run_check(BROKER)
        self.assertTrue(r["summary"]["clean"])
        self.assertEqual(r["summary"]["matched"], 2)
        self.assertTrue(r["positions"]["agree"])

    def test_each_category_is_found(self):
        bot = csv("order_id,side,quantity,price,fee",
                  "A1,buy,2,100.00,0.20", "A1,buy,2,100.00,0.20",      # booked twice
                  "Z9,buy,1,99.00,0.10")                              # not at broker; A2 missing
        s = self.run_check(bot)["summary"]
        self.assertEqual((s["booked_twice"], s["missing_from_bot"], s["not_at_broker"], s["matched"]), (1, 1, 1, 1))
        self.assertFalse(s["clean"])

    def test_mismatches_name_the_field(self):
        bot = csv("order_id,side,quantity,price,fee", "A1,buy,2,100.00,0.30", "A2,sell,1,101.00,0.10")
        details = {i["order_id"]: i["detail"] for i in self.run_check(bot)["issues"]}
        self.assertIn("fee", details["A1"])
        self.assertIn("average price", details["A2"])
        bot = csv("order_id,side,quantity,price,fee", "A1,sell,2,100.00,0.20", "A2,sell,1,101.50,0.10")
        self.assertIn("quantity", {i["order_id"]: i["detail"] for i in self.run_check(bot)["issues"]}["A1"])

    def test_partial_fills_aggregate_per_order(self):
        broker = fc.parse(csv("order_id,side,quantity,price,time", "A,buy,1,10,t1", "A,buy,1,12,t2"))
        bot = fc.parse(csv("order_id,side,quantity,price", "A,buy,2,11"))
        self.assertTrue(fc.check(broker, bot)["summary"]["clean"])

    def test_fifo_economics_and_position_limit(self):
        broker = fc.parse(csv("order_id,side,quantity,price,fee",
                              "1,buy,2,100,0.5", "2,sell,3,103,0.5", "3,buy,1,101,0.5"))
        e = fc.check(broker, broker, max_position=Decimal(0), mark=Decimal(99))["economics"]
        # long 2 @100 closed at 103 (+6); short 1 @103 closed at 101 (+2)
        self.assertEqual((e["realized_pre_fee"], e["fees"], e["realized_after_fees"]), ("8", "1.5", "6.5"))
        self.assertEqual((e["final_position"], e["max_abs_position"], e["limit_breaches"]), ("0", "2", 2))
        self.assertNotIn("unrealized_at_mark", e)                             # flat: nothing to value

    def test_time_column_orders_the_replay(self):
        broker = fc.parse(csv("order_id,side,quantity,price,time",
                              "late,sell,1,105,2026-01-01T00:00:02", "early,buy,1,100,2026-01-01T00:00:01"))
        e = fc.check(broker, broker, max_position=Decimal(0))["economics"]
        self.assertEqual((e["order_source"], e["realized_pre_fee"], e["max_abs_position"]), ("time column", "5", "1"))


class SampleFiles(unittest.TestCase):
    def test_planted_problems_are_all_found(self):
        def load(name):
            with open(os.path.join(ROOT, "examples", "fills", name)) as f:
                return fc.parse(f.read())
        r = fc.check(load("broker_fills.csv"), load("bot_log.csv"), max_position=Decimal(5))
        found = {(i["kind"], i["order_id"]) for i in r["issues"]}
        self.assertEqual(found, {("mismatch", "B-1004"), ("booked_twice", "B-1006"),
                                 ("missing_from_bot", "B-1009"), ("not_at_broker", "B-1013")})
        self.assertEqual(r["summary"]["matched"], 10)
        self.assertEqual(r["positions"], {"broker": "1", "bot": "-1", "agree": False})
        self.assertEqual(r["economics"]["realized_pre_fee"], "5.75")

    def test_realistic_sample_planted_problems(self):
        def load(name):
            with open(os.path.join(ROOT, "examples", "fills", name)) as f:
                return fc.parse(f.read())
        bot = load("realistic_bot.csv")
        self.assertEqual(bot.mapping["order_id"], "client_id")                 # bot-style headers detected
        r = fc.check(load("realistic_broker.csv"), bot, max_position=Decimal(20))
        found = {(i["kind"], i["order_id"]) for i in r["issues"]}
        self.assertEqual(found, {("booked_twice", "ORD-5007"), ("booked_twice", "ORD-5017"),
                                 ("missing_from_bot", "ORD-5026"), ("missing_from_bot", "ORD-5036"),
                                 ("mismatch", "ORD-5034"), ("mismatch", "ORD-5047"), ("mismatch", "ORD-5058"),
                                 ("not_at_broker", "ORD-9001"),
                                 ("position_limit", "ORD-5025"), ("position_limit", "ORD-5068")})
        self.assertEqual([why for _, why in r["invalid"]["bot"]], ["not a number: 'N/A'", "unknown side 'HOLD'"])
        self.assertEqual(r["summary"]["matched"], 145)


if __name__ == "__main__":
    unittest.main()
