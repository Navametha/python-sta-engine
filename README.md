# Python Static Timing Analyzer

**An independent, educational static timing analysis (STA) engine for combinational logic, using Liberty NLDM tables.**

This project propagates late rise/fall arrival times and output slews through a gate-level connectivity graph, performs bilinear table interpolation, and reports endpoint slack, WNS/TNS, and worst paths. It is **not a replacement for PrimeTime, OpenSTA, or other signoff STA tools**.

## Architecture

```text
2-D Liberty NLDM (.lib)  ---\
                              --> Liberty reader --> timing arcs
JSON netlist + constraints ---/                         |
                                                       v
PI arrivals --> topological gate traversal --> late edge arrivals
                                                |             |
                                         output loading   path predecessor
                                                \             /
                                                 endpoint slack
                                                       |
                                             WNS/TNS + JSON report
```

## Run the reproducible example

```bash
python -m pip install -e .
python -m sta_engine.cli --liberty examples/demo.lib --netlist examples/demo_netlist.json --out results/demo_results.json
python -m unittest discover -s tests -v
```

All example Liberty coefficients, test circuits, and timing constraints are **synthetic** and included in this repository. Running the first command computes the results (no proprietary class materials required).

For the bundled synthetic example, the measured results are **WNS = −0.017 ns (−17 ps)**, **TNS = −0.027 ns (−27 ps)**, and **two violating endpoints**. See `examples/demo_results.json`.

The generated JSON contains:

- Cell delay and output slew lookups with **bilinear interpolation** of 2-D NLDM tables.
- Fanout pin capacitance plus user-specified output load.
- Late rise/fall propagation, respecting positive/negative/non-unate arcs.
- Output required time, path delay, per-endpoint slack, WNS, TNS, and critical-path reconstruction.

## Input specification

`examples/demo_netlist.json` lists primary `inputs`, ordered or unordered `instances`, `outputs`, and `constraints`.

Instance example:

```json
{"name":"U1","cell":"NAND2_X1","inputs":{"A":"a","B":"b"},"output":"n1"}
```

Constraints fields:

| Key | Meaning |
|---|---|
| `input_arrival_ns` | Initial PI arrival time in nanoseconds |
| `input_slew_ns` | Initial PI transition time in nanoseconds |
| `output_required_ns` | Max allowable output arrival time in nanoseconds |
| `output_load_pf` | Explicit external load in picofarads (optional) |

## Mathematical model

For a timing arc with input transition `s` and output capacitance `C`:

```text
arc_delay = interpolate_2d(cell_rise or cell_fall, s, C)
arrival(output, edge) = max(arrival(input, possible_input_edge) + arc_delay)
slack(output) = required(output) - max(arrival_rise, arrival_fall)
WNS = min(0, minimum endpoint slack)
TNS = sum(min(0, slack) across endpoints)
```

Output load includes fanout input pin capacitances and specified external output load. Outside NLDM grid bounds, this educational implementation **clamps** rather than extrapolating.

## Limitations and future improvements

This is a deliberately bounded **combinational max-delay analysis**. The parser supports a common subset of Liberty with explicit `index_1`, `index_2` and `values` in 2-D cell/transition tables. It does not support every Liberty grammar variation, templates without local indices, all unit declarations, multi-output gates, logic sensitization, conditional timing (`when`), interconnect parasitics, setup/hold arcs, clocks, SDC, or MCMM. State-dependent arcs are not modeled. NLDM time units must be **ns**, capacitance units **pF** for this sample.

Planned extensions: Liberty unit normalization, additional timing checks, read_verilog/read_sdc, sequential cell modeling, RC delay and comparison with OpenSTA on a public benchmark.

## Resume relevance

Demonstrates timing graph traversal, pin load computation, NLDM interpolation, late rise/fall propagation, and critical path reconstruction. Built from scratch as an independent public portfolio implementation; **not uploaded or extracted from employer tooling or course submissions**.

## License

MIT. See `LICENSE`.

## ISCAS coursework-compatible mode (optional)

An **independently implemented** second mode follows the *simplified* EE5301 PA1 timing assumptions. It parses numbered `.isc` circuits, plus a `NLDM_lib_max2Inp`-style educational library (cell-level `capacitance`, `cell_delay`, `output_slew` rather than full pin timing arcs). It is distinct from the pin-level Liberty/JSON analyzer above.

```bash
# Run with your own locally available copies of the course inputs:
python -m sta_engine.course_cli \
  --isc /path/to/c17.isc --liberty /path/to/NLDM_lib_max2Inp \
  --out results/c17.json --csv results/c17.csv --report results/c17.rpt

# Optional fixed required time and PI slew for controlled timing studies:
python -m sta_engine.course_cli \
  --isc /path/to/c17.isc --liberty /path/to/NLDM_lib_max2Inp \
  --required-ps 50 --input-slew-ps 2 --out results/c17_fixed.json
```

**Course model:** PI arrival = 0 ps; input slew = 2 ps; no wire capacitance; each gate's input capacitance contributes to each driving node's load; an endpoint has external load of four inverter input capacitances. The 7×7 NLDM tables use ns for delay/slew and fF for capacitance. n-input gate delay and slew are scaled by `n/2` only when n > 2. The analyzer traverses forward to calculate maximum arrivals and slews, traverses backward to calculate each gate's required time and slack, and follows the arrival-maximizing predecessor to recover a longest path. DFF nodes are split into pseudo primary inputs/outputs; clock/setup/hold arcs are **not** timed.

By default, endpoint required time is **1.1× the worst circuit delay**, exactly as in the teaching model. This **guarantees nonnegative endpoint slacks**, so WNS and TNS are zero under the default policy; it is *not* evidence of timing closure against a fixed clock. Use `--required-ps` to impose a fixed output budget. The exported CSV, text report or JSON can be fed to the companion `sta-regression-analyzer` project.

No instructor-provided code, classroom Liberty data, benchmark archives, or proprietary PDK content is redistributed in this repository. The user's local `.isc` benchmarks and course library were used for validation, with their sources kept out of version control. Built-in unit-test fixtures are independently constructed.

**Caveat:** Some benchmark cells (e.g., DFF) are abstracted into boundary nodes. Rise/fall, clock propagation, hold, interconnect parasitics, input pin-specific arcs, and real sequential timing checks are not modeled in this course mode. Timing results are model-specific; do not compare them directly to signoff STA.


### Verification against uploaded course inputs

The independent course-mode implementation was executed locally on the user's supplied simplified library and ISCAS netlist archive. Only derived numeric results are committed; raw course materials are **not** included. Values are outputs of the educational engine, **not independently verified golden values or signoff results**.

| Benchmark | Combinational gates | Split DFFs | Endpoints | Circuit delay |
|---|---:|---:|---:|---:|
| c17 | 6 | 0 | 2 | 62.501 ps |
| c1908 | 880 | 0 | 25 | 1,012.285 ps |
| c7552 | 3,512 | 0 | 108 | 868.304 ps |
| b15_1 | 12,543 | 449 | 519 | 1,749.591 ps |
| b19_1 | 212,728 | 6,642 | 6,669 | 3,625.913 ps |

Details: [`examples/course_validation_summary.json`](examples/course_validation_summary.json).

The b19_1 benchmark includes repeated declarations of one output; repeated output declarations are deduplicated for endpoint analysis. The engine successfully completed this benchmark run on the test machine, but broader memory/runtime benchmarking is still warranted. The class requirement was a scalable implementation, not a clock-constrained silicon signoff.
