"""Local operations console (quote_gate.console): HTTP API over a real loopback run."""
import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from quote_gate import console


class ConsoleApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd, cls.console = console.serve(0)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.console.close()

    def req(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        r = urllib.request.Request(self.base + path, data=data, method="GET" if body is None else "POST",
                                   headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=10) as f:
                return f.status, f.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def api(self, path, body=None):
        code, raw = self.req(path, body)
        return code, json.loads(raw)

    def wait(self, cond, timeout=8.0):
        deadline = time.monotonic() + timeout
        while True:
            s = self.api("/api/live?detail=1")[1]
            if cond(s):
                return s
            if time.monotonic() > deadline:
                raise AssertionError(f"condition not met: {s.get('state')}")
            time.sleep(0.05)

    def test_static_pages_and_unknown_paths(self):
        code, body = self.req("/")
        self.assertEqual(code, 200)
        self.assertIn(b"QuoteGate", body)
        self.assertEqual(self.req("/app.js")[0], 200)
        self.assertEqual(self.req("/../README.md")[0], 404)       # only the three known files are served
        self.assertEqual(self.req("/api/nope")[0], 404)

    def test_input_validation(self):
        self.assertEqual(self.api("/api/run", {"seed": "x"})[0], 400)
        self.assertEqual(self.api("/api/run", {"seed": 1, "seconds": 9999})[0], 400)
        self.assertEqual(self.api("/api/fault", {"kind": "bogus"})[0], 400)
        self.assertEqual(self.api("/api/fault", {"kind": "drop_next_ack", "venue": "B"})[0], 400)
        code, _ = self.req("/api/run", None)
        self.assertEqual(code, 404)                               # GET on a POST route

    def test_published_evidence_endpoints(self):
        q = self.api("/api/quant")[1]
        self.assertTrue(q["available"])
        self.assertEqual(len(q["cells"]), 5)
        self.assertEqual(len(q["paired"]), len(q["seeds"]))
        self.assertTrue(self.api("/api/replay")[1]["available"])
        e = self.api("/api/evidence")[1]
        self.assertTrue(e["hashes"])
        self.assertEqual(console.verify_hashes()["all_match"], True)

    def test_run_fault_and_reconciliation(self):
        self.assertEqual(self.api("/api/fault", {"kind": "drop_next_ack"})[0], 409)   # no run yet
        code, r = self.api("/api/run", {"seed": 7, "seconds": 5})
        self.assertEqual(code, 200, r)
        self.assertEqual(self.api("/api/run", {"seed": 7, "seconds": 5})[0], 409)     # one run at a time
        self.wait(lambda s: s.get("counters", {}).get("filled", 0) > 5)
        self.assertEqual(self.api("/api/fault", {"kind": "drop_next_ack"})[0], 200)
        s = self.wait(lambda s: s["counters"]["resolved_by_status"] >= 1)
        self.assertGreaterEqual(s["counters"]["timeouts"], 1)
        self.assertEqual(self.api("/api/fault", {"kind": "pause_feed", "venue": "A"})[0], 200)
        s = self.wait(lambda s: s["venues"]["A"]["status"] == "stale")
        self.assertEqual(s["trading"], "no_signal")
        self.assertEqual(s["route_preview"]["buy"]["venue"], "B")
        self.assertIn(["A", "stale"], s["route_preview"]["buy"]["excluded"])
        self.assertEqual(self.api("/api/stop", {})[0], 200)
        s = self.wait(lambda s: s["state"] == "complete", timeout=10)
        rec = s["reconciliation"]
        self.assertEqual(rec["mismatched"], 0)
        self.assertEqual(rec["unbooked_venue_fills"], 0)
        self.assertEqual(rec["venue_fills"], rec["ledger_fills"])
        self.assertLessEqual(s["ledger"]["max_abs_position"], 5)
        self.assertEqual(s["unresolved"], [])


if __name__ == "__main__":
    unittest.main()
