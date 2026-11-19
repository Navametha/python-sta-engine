"""Command-line interface for an illustrative combinational STA engine."""
import argparse
import json
from pathlib import Path
from .engine import run


def main() -> None:
    parser = argparse.ArgumentParser(description="Run 2-D NLDM combinational max-delay timing analysis")
    parser.add_argument("--liberty", type=Path, required=True, help="Liberty .lib file with supported NLDM tables")
    parser.add_argument("--netlist", type=Path, required=True, help="JSON netlist and constraints")
    parser.add_argument("--out", type=Path, default=Path("timing_results.json"))
    args = parser.parse_args()
    result = run(args.liberty, args.netlist)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("Endpoint       Arrival(ns) Required(ns) Slack(ns)  Edge")
    for e in result["endpoints"]:
        print(f"{e['endpoint']:<14} {e['arrival_ns']:>10.3f} {e['required_ns']:>12.3f} {e['slack_ns']:>9.3f}  {e['worst_transition']}")
    m = result["metrics"]
    print(f"WNS={m['wns_ns']:.3f} ns  TNS={m['tns_ns']:.3f} ns  Violations={m['violating_endpoints']}")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
