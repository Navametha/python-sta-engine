"""Independent implementation of the EE5301-style simplified ISCAS STA model.

Inputs are ISCAS .isc netlists and a deliberately simplified library with
cell_delay/output_slew tables and cell-level input capacitance. Not full Liberty.

All internal times are in ns, capacitances in fF; neither clock constraints nor
true setup/hold checks are modeled. DFF boundaries are split into pseudo-PI/PO.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import csv
import json
from pathlib import Path
import re
from typing import Any

from .liberty import Table, _blocks, _table


@dataclass(frozen=True)
class SimpleCell:
    capacitance_ff: float
    delay: Table
    slew: Table


def read_course_library(text: str) -> dict[str, SimpleCell]:
    """Read the course's custom cell-level NLDM layout, not full pin Liberty."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    result = {}
    for name, body in _blocks(text, "cell"):
        cap = re.search(r"\bcapacitance\s*:\s*([\d.eE+-]+)\s*;", body)
        delay = list(_blocks(body, "cell_delay"))
        slew = list(_blocks(body, "output_slew"))
        if not (cap and delay and slew):
            continue
        if name.upper() in result:
            raise ValueError(f"Duplicate cell type: {name}")
        result[name.upper()] = SimpleCell(float(cap.group(1)), _table(delay[0][1]), _table(slew[0][1]))
    if not result:
        raise ValueError("No cell_delay/output_slew pairs found in custom course library")
    return result


@dataclass(frozen=True)
class Gate:
    identifier: str
    kind: str
    fanins: tuple[str, ...]


def read_isc(text: str) -> tuple[list[str], list[str], dict[str, Gate]]:
    """Read numbered INPUT/OUTPUT/gate assignments; comments and order are irrelevant."""
    inputs: list[str] = []
    outputs: list[str] = []
    gates: dict[str, Gate] = {}
    declaration = re.compile(r"^\s*(INPUT|OUTPUT)\s*\(\s*(\d+)\s*\)\s*$", re.I)
    gate_regex = re.compile(r"^\s*(\d+)\s*=\s*([a-zA-Z][\w]*)\s*\(([^)]*)\)\s*$")
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = declaration.fullmatch(line)
        if m:
            (inputs if m.group(1).upper() == "INPUT" else outputs).append(m.group(2))
            continue
        m = gate_regex.fullmatch(line)
        if m:
            node, kind, raw = m.groups()
            fanins = tuple(tok.strip() for tok in raw.split(","))
            if not fanins or not all(x.isdigit() for x in fanins):
                raise ValueError(f"Invalid fanin list at line {lineno}")
            if node in gates:
                raise ValueError(f"Duplicate gate identifier: {node}")
            gates[node] = Gate(node, kind.upper(), fanins)
            continue
        raise ValueError(f"Cannot parse .isc line {lineno}: {line[:100]}")
    if not outputs or not inputs:
        raise ValueError("ISC netlist must contain INPUT and OUTPUT declarations")
    if len(set(inputs)) != len(inputs):
        raise ValueError("Duplicate primary inputs")
    # Some ISCAS89/99 .isc files declare the same output repeatedly. Treat
    # output nodes as a set of timing endpoints, not repeated observations.
    outputs = list(dict.fromkeys(outputs))
    return inputs, outputs, gates


def _prepare_graph(inputs: list[str], outputs: list[str], gates: dict[str, Gate]):
    # A DFF's Q output (the numbered left-hand side) becomes a pseudo-input;
    # its D input becomes a pseudo-output. This does not time the DFF itself.
    dff = [g for g in gates.values() if g.kind == "DFF"]
    pi = list(dict.fromkeys(inputs + [g.identifier for g in dff]))
    po = list(dict.fromkeys(outputs + [g.fanins[0] for g in dff]))
    gate_map = {n: g for n, g in gates.items() if g.kind != "DFF"}
    if set(pi) & set(gate_map):
        raise ValueError("Primary inputs must not have combinational gate drivers")
    fanouts: dict[str, list[str]] = defaultdict(list)
    indegree = {}
    drivers = set(pi) | set(gate_map)
    for g in gate_map.values():
        if any(n not in drivers for n in g.fanins):
            missing = [n for n in g.fanins if n not in drivers]
            raise ValueError(f"Undriven fanins for node {g.identifier}: {missing[:5]}")
        indegree[g.identifier] = sum(n in gate_map for n in g.fanins)
        for n in g.fanins:
            fanouts[n].append(g.identifier)
    for n in po:
        if n not in drivers:
            raise ValueError(f"Undriven primary output: {n}")
    queue = deque(n for n, d in indegree.items() if d == 0)
    ordered: list[Gate] = []
    while queue:
        node = queue.popleft()
        ordered.append(gate_map[node])
        for consumer in fanouts.get(node, ()):
            indegree[consumer] -= 1
            if indegree[consumer] == 0:
                queue.append(consumer)
    if len(ordered) != len(gate_map):
        raise ValueError("Combinational cycle detected after DFF splitting")
    return pi, po, ordered, gate_map, fanouts, len(dff)


def analyze_course(
    isc: str,
    library: str,
    *,
    input_slew_ps: float = 2.0,
    required_ps: float | None = None,
    required_scale: float = 1.1,
) -> dict[str, Any]:
    """Run the course's simplified max-delay analysis with full gate slacks.

    ``required_ps`` is an optional externally assigned budget, useful for
    comparing runs; default reproduces the course's relative required time.
    """
    if input_slew_ps < 0 or (required_ps is not None and required_ps < 0) or required_scale <= 0:
        raise ValueError("Slew, fixed required time and required scale must be nonnegative/positive")
    cells = read_course_library(library)
    inputs, outputs, gates = read_isc(isc)
    pi, po, ordered, gate_map, fanouts, num_dff = _prepare_graph(inputs, outputs, gates)
    inv = cells.get("INV") or cells.get("NOT")
    if inv is None:
        raise ValueError("INV or NOT cell is required for four-inverter output load")
    loads: dict[str, float] = defaultdict(float)
    for gate in ordered:
        cell = cells.get(gate.kind)
        if cell is None:
            raise ValueError(f"Missing library cell for gate type {gate.kind}")
        for n in gate.fanins:
            loads[n] += cell.capacitance_ff
    # Treat each observed output as an external load; internal loads remain.
    for output in po:
        loads[output] += 4 * inv.capacitance_ff

    arrivals: dict[str, float] = {n: 0.0 for n in pi}
    slews: dict[str, float] = {n: input_slew_ps / 1000.0 for n in pi}
    predecessor: dict[str, str] = {}
    input_arc_delay: dict[str, list[tuple[str, float]]] = {}
    for gate in ordered:
        cell = cells[gate.kind]
        mult = len(gate.fanins) / 2 if len(gate.fanins) > 2 else 1.0
        candidates = []
        for fanin in gate.fanins:
            d = cell.delay.lookup(slews[fanin], loads[gate.identifier]) * mult
            s = cell.slew.lookup(slews[fanin], loads[gate.identifier]) * mult
            candidates.append((arrivals[fanin] + d, fanin, d, s))
        max_arrival, chosen, _, chosen_slew = max(candidates, key=lambda x: x[0])
        arrivals[gate.identifier] = max_arrival
        slews[gate.identifier] = chosen_slew
        predecessor[gate.identifier] = chosen
        input_arc_delay[gate.identifier] = [(source, delay) for _, source, delay, _ in candidates]

    delay_ns = max(arrivals[n] for n in po)
    req_ns = (required_ps / 1000.0) if required_ps is not None else delay_ns * required_scale
    required: dict[str, float] = {n: float("inf") for n in (*pi, *gate_map)}
    for end in po:
        required[end] = req_ns
    # Backward traversal computes per-gate required times using every input arc.
    for gate in reversed(ordered):
        gate_req = required[gate.identifier]
        if gate_req == float("inf"):
            continue
        for fanin, arc_delay in input_arc_delay[gate.identifier]:
            required[fanin] = min(required[fanin], gate_req - arc_delay)

    def path_to(end: str) -> list[str]:
        path = [end]
        while path[-1] in predecessor:
            path.append(predecessor[path[-1]])
        return list(reversed(path))

    endpoint_rows = []
    for end in po:
        path = path_to(end)
        endpoint_rows.append({
            "endpoint": f"n{end}", "startpoint": f"n{path[0]}",
            "arrival_ns": round(arrivals[end], 9), "required_ns": round(req_ns, 9),
            "slack_ns": round(req_ns - arrivals[end], 9),
            "critical_path": [f"{gate_map[n].kind}-n{n}" if n in gate_map else f"INP-n{n}" for n in path],
            "path_group": "course_isc", "path_type": "max",
        })
    endpoint_rows.sort(key=lambda x: (x["slack_ns"], x["endpoint"]))
    gate_rows = [
        {"node": f"{g.kind}-n{g.identifier}", "arrival_ns": round(arrivals[g.identifier], 9),
         "required_ns": round(required[g.identifier], 9) if required[g.identifier] != float("inf") else None,
         "slack_ns": round(required[g.identifier] - arrivals[g.identifier], 9) if required[g.identifier] != float("inf") else None,
         "load_ff": round(loads[g.identifier], 6), "slew_ns": round(slews[g.identifier], 9)}
        for g in ordered
    ]
    slack = [e["slack_ns"] for e in endpoint_rows]
    return {
        "analysis": "course_isc_combinational_max_delay",
        "units": {"time": "ns", "capacitance": "fF"},
        "assumptions": {"pi_arrival_ps": 0, "pi_slew_ps": input_slew_ps,
                        "required_time_policy": "fixed_ps" if required_ps is not None else "scale_of_circuit_delay",
                        "required_scale": None if required_ps is not None else required_scale,
                        "output_load": "4 x inverter input cap plus any fanout pin capacitance",
                        "dff_policy": "Q pseudo-PI and D pseudo-PO; no clock/sequential arcs",
                        "n_input_policy": "(n/2) delay/slew scale for n>2"},
        "metrics": {"circuit_delay_ns": round(delay_ns, 9), "wns_ns": round(min(0.0, min(slack)), 9),
                    "tns_ns": round(sum(min(0.0, s) for s in slack), 9),
                    "violating_endpoints": sum(s < 0 for s in slack),
                    "endpoints_analyzed": len(endpoint_rows),
                    "combinational_gates": len(ordered), "split_dff_count": num_dff},
        "endpoints": endpoint_rows,
        "gates": gate_rows,
        "critical_path": endpoint_rows[0]["critical_path"],
    }


def run_course(liberty_path: Path, isc_path: Path, **kwargs: Any) -> dict[str, Any]:
    return analyze_course(isc_path.read_text(encoding="utf-8"),
                          liberty_path.read_text(encoding="utf-8"), **kwargs)


def write_course_reports(result: dict[str, Any], json_path: Path, csv_path: Path | None = None,
                         text_path: Path | None = None) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if csv_path is not None:
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=("startpoint", "endpoint", "path_group", "path_type", "slack_ns"))
            writer.writeheader()
            writer.writerows({k: row[k] for k in writer.fieldnames} for row in result["endpoints"])
    if text_path is not None:
        text_path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for e in result["endpoints"]:
            lines.extend([f"Startpoint: {e['startpoint']}", f"Endpoint: {e['endpoint']}",
                          f"Path Group: {e['path_group']}", "Path Type: max",
                          f"  slack ({'VIOLATED' if e['slack_ns'] < 0 else 'MET'}) {e['slack_ns']:+.9f}", ""])
        text_path.write_text("\n".join(lines), encoding="utf-8")
