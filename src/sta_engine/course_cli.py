"""Run the separate ISCAS/course simplified-Liberty compatibility mode."""
import argparse
from pathlib import Path
from .course_isc import run_course, write_course_reports


def main() -> None:
    parser = argparse.ArgumentParser(description="Simplified NLDM STA for ISCAS .isc netlists (not signoff STA)")
    parser.add_argument("--liberty", type=Path, required=True, help="Course-style simplified NLDM_lib_max2Inp")
    parser.add_argument("--isc", type=Path, required=True, help="ISCAS .isc netlist")
    parser.add_argument("--out", type=Path, default=Path("results/course_sta.json"))
    parser.add_argument("--csv", type=Path, help="Optional CSV for sta-regression-analyzer")
    parser.add_argument("--report", type=Path, help="Optional report_timing-like report")
    parser.add_argument("--input-slew-ps", type=float, default=2.0)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--required-ps", type=float, help="Fixed max endpoint required time in ps")
    group.add_argument("--required-scale", type=float, default=1.1, help="Scale factor times circuit delay (default 1.1)")
    args = parser.parse_args()
    output = run_course(args.liberty, args.isc, input_slew_ps=args.input_slew_ps,
                        required_ps=args.required_ps, required_scale=args.required_scale)
    write_course_reports(output, args.out, args.csv, args.report)
    m = output["metrics"]
    print(f"Gates={m['combinational_gates']} DFFs={m['split_dff_count']} endpoints={m['endpoints_analyzed']}")
    print(f"Circuit delay: {m['circuit_delay_ns']*1000:.4f} ps")
    print(f"WNS: {m['wns_ns']*1000:.4f} ps | TNS: {m['tns_ns']*1000:.4f} ps")
    print(f"Critical path: {' -> '.join(output['critical_path'])}")
    print(f"Saved {args.out}")


if __name__ == "__main__":
    main()
