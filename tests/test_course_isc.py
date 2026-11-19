"""Tests use original, synthetic fixtures instead of redistributing course data."""
import tempfile
import unittest
from pathlib import Path
from sta_engine.course_isc import (read_course_library, read_isc, analyze_course, write_course_reports)


def table(value):
    return ('index_1 ("0.001,0.010"); index_2 ("1,20"); '
            f'values ("{value},{value}", "{value},{value}");')


LIB = '''library (toy) {\n''' + "\n".join(
    f'cell ({name}) {{ capacitance : {cap}; cell_delay(Timing_7_7) {{ {table(delay)} }} '
    f'output_slew(Timing_7_7) {{ {table(.002)} }} }}'
    for name, cap, delay in [('NAND',2,.010), ('AND',2,.012), ('INV',1,.005)]
) + '\n}'
ISC = '''# arbitrary order and case
OUTPUT (5)
INPUT (1)
5 = NAND (3,4)
3 = NAND (1,2)
INPUT (2)
4 = INV (2)
'''


class CourseModeTests(unittest.TestCase):
    def test_custom_liberty_table_parser(self):
        cells = read_course_library(LIB)
        self.assertEqual(set(cells), {'NAND','AND','INV'})
        self.assertAlmostEqual(cells['NAND'].delay.lookup(.002,3), .010)

    def test_duplicate_output_declarations_are_deduplicated(self):
        inputs, outputs, gates = read_isc(ISC + "\nOUTPUT(5)\n")
        self.assertEqual(outputs, ["5"])

    def test_parse_and_critical_path(self):
        ins, outs, gates = read_isc(ISC)
        self.assertEqual(ins,['1','2'])
        self.assertEqual(outs,['5'])
        self.assertEqual(gates['5'].fanins,('3','4'))
        r = analyze_course(ISC,LIB)
        self.assertAlmostEqual(r['metrics']['circuit_delay_ns'], .020)
        self.assertEqual(r['metrics']['combinational_gates'],3)
        self.assertEqual(r['critical_path'],['INP-n1','NAND-n3','NAND-n5'])
        self.assertAlmostEqual(r['endpoints'][0]['slack_ns'],.002)
        self.assertEqual(r['metrics']['tns_ns'],0)
        self.assertEqual(len(r['gates']), 3)
        self.assertAlmostEqual(next(x for x in r['gates'] if x['node']=='NAND-n3')['load_ff'],2)

    def test_fixed_required_and_worst_slack(self):
        r=analyze_course(ISC, LIB, required_ps=15)
        self.assertAlmostEqual(r['metrics']['wns_ns'],-.005)
        self.assertAlmostEqual(r['metrics']['tns_ns'],-.005)
        self.assertEqual(r['metrics']['violating_endpoints'],1)
        self.assertAlmostEqual(next(x for x in r['gates'] if x['node']=='NAND-n3')['slack_ns'],-.005)

    def test_more_than_two_inputs_scales_delay_and_slew(self):
        r=analyze_course('INPUT(1)\nINPUT(2)\nINPUT(3)\nOUTPUT(4)\n4 = NAND(1,2,3)',LIB)
        self.assertAlmostEqual(r['metrics']['circuit_delay_ns'],.015)

    def test_dff_boundary_split(self):
        net = 'INPUT(1)\nOUTPUT(4)\n3 = DFF(2)\n2 = NAND(1,3)\n4 = INV(3)'
        r=analyze_course(net, LIB)
        self.assertEqual(r['metrics']['split_dff_count'],1)
        self.assertEqual(r['metrics']['endpoints_analyzed'],2)
        self.assertEqual(r['metrics']['combinational_gates'],2)

    def test_cycle_rejection(self):
        with self.assertRaisesRegex(ValueError,'cycle'):
            analyze_course('INPUT(1)\nOUTPUT(3)\n3=NAND(2,1)\n2=NAND(3,1)',LIB)

    def test_undriven_rejection(self):
        with self.assertRaisesRegex(ValueError,'Undriven'):
            analyze_course('INPUT(1)\nOUTPUT(3)\n3=NAND(2,1)', LIB)

    def test_csv_and_text_report(self):
        r=analyze_course(ISC, LIB, required_ps=15)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            write_course_reports(r,p/'results.json',p/'results.csv',p/'results.rpt')
            self.assertIn('slack (VIOLATED)',(p/'results.rpt').read_text())
            self.assertIn('startpoint,endpoint,path_group,path_type,slack_ns',(p/'results.csv').read_text())


if __name__=='__main__': unittest.main()
