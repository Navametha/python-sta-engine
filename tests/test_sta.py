import copy
import json
from pathlib import Path
import unittest

from sta_engine import Table, parse_liberty, analyze, run

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class TestInterpolation(unittest.TestCase):
    def test_bilinear_at_center(self):
        t = Table((0.0, 1.0), (0.0, 1.0), ((0.0, 10.0), (20.0, 30.0)))
        self.assertAlmostEqual(t.lookup(0.5, 0.5), 15.0)

    def test_clamp_outside_range(self):
        t = Table((0.0, 1.0), (0.0, 1.0), ((0.0, 10.0), (20.0, 30.0)))
        self.assertEqual(t.lookup(-1, 100), 10.0)

    def test_single_point_axis(self):
        t = Table((0.1,), (0.1,), ((0.042,),))
        self.assertEqual(t.lookup(0.3, 0.4), 0.042)


class TestEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cells = parse_liberty((EXAMPLES / "demo.lib").read_text())
        cls.model = json.loads((EXAMPLES / "demo_netlist.json").read_text())

    def test_liberty_cells_and_arcs(self):
        self.assertIn("NAND2_X1", self.cells)
        self.assertEqual(len(self.cells["NAND2_X1"].pins["Y"].arcs), 2)
        self.assertEqual(self.cells["INV_X1"].pins["Y"].arcs[0].sense, "negative_unate")

    def test_negative_unate_transition(self):
        model = copy.deepcopy(self.model)
        model["instances"] = [{"name":"I", "cell":"INV_X1", "inputs":{"A":"a"}, "output":"y"}]
        model["outputs"] = ["y"]
        result = analyze(model, self.cells)["endpoints"][0]
        self.assertEqual(result["critical_path"][1]["cell_instance"], "I")
        path_input_edge = result["critical_path"][0]["transition"]
        self.assertNotEqual(result["worst_transition"], path_input_edge)

    def test_multi_gate_path_and_metrics(self):
        r = analyze(self.model, self.cells)
        self.assertEqual(r["metrics"]["endpoints_analyzed"], 2)
        self.assertEqual(len(next(e for e in r["endpoints"] if e["endpoint"] == "y")["critical_path"]), 4)
        self.assertAlmostEqual(r["metrics"]["tns_ns"], sum(min(0,e["slack_ns"]) for e in r["endpoints"]))
        self.assertEqual(r["metrics"]["wns_ns"], min(e["slack_ns"] for e in r["endpoints"]))

    def test_topological_order_independence(self):
        m = copy.deepcopy(self.model)
        m["instances"].reverse()
        self.assertEqual(analyze(m, self.cells)["metrics"], analyze(self.model, self.cells)["metrics"])

    def test_multiple_driver_rejected(self):
        m = copy.deepcopy(self.model)
        m["instances"][1]["output"] = "n1"
        with self.assertRaisesRegex(ValueError,"Multiple drivers"):
            analyze(m,self.cells)

    def test_cycle_rejected(self):
        m = copy.deepcopy(self.model)
        m["instances"][0]["inputs"]["A"] = "n2"
        with self.assertRaisesRegex(ValueError,"cycle"):
            analyze(m,self.cells)

    def test_missing_required_time_rejected(self):
        m = copy.deepcopy(self.model)
        del m["constraints"]["output_required_ns"]["y"]
        with self.assertRaisesRegex(ValueError,"required time"):
            analyze(m,self.cells)

    def test_read_files(self):
        self.assertEqual(run(EXAMPLES / "demo.lib", EXAMPLES / "demo_netlist.json")["analysis"], "combinational_max_delay")


if __name__ == "__main__":
    unittest.main()

class TestNoViolation(unittest.TestCase):
    def test_wns_and_tns_zero_when_all_paths_meet_timing(self):
        cells = parse_liberty((EXAMPLES / "demo.lib").read_text())
        m = json.loads((EXAMPLES / "demo_netlist.json").read_text())
        m["constraints"]["output_required_ns"] = {"y": 1.0, "z": 1.0}
        r = analyze(m, cells)
        self.assertEqual(r["metrics"]["wns_ns"], 0.0)
        self.assertEqual(r["metrics"]["tns_ns"], 0.0)
