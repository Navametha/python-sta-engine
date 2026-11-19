"""Combinational, max-delay, NLDM static timing analysis on a JSON netlist."""
from __future__ import annotations

from dataclasses import dataclass
from collections import defaultdict
import json
from pathlib import Path
from typing import Any

from .liberty import Arc, Cell, parse_liberty


@dataclass
class Arrival:
    time: float
    slew: float
    predecessor: tuple[str, str, str, str, float] | None = None
    # (source net, source transition, instance, source pin, arc delay)


def _validate_and_order(model: dict, cells: dict[str, Cell]):
    inputs = set(model["inputs"])
    if len(inputs) != len(model["inputs"]):
        raise ValueError("Duplicate primary inputs")
    gates = model["instances"]
    driven = set(inputs)
    for gate in gates:
        cell = cells[gate["cell"]]
        outputs = [name for name, pin in cell.pins.items() if pin.direction == "output"]
        if len(outputs) != 1:
            raise ValueError(f"Only single-output cells supported: {gate['cell']}")
        if gate.get("output_pin", outputs[0]) != outputs[0]:
            raise ValueError(f"Invalid output pin for {gate['name']}")
        output = gate["output"]
        if output in driven:
            raise ValueError(f"Multiple drivers for net: {output}")
        driven.add(output)
        required_pins = {p for p, info in cell.pins.items() if info.direction == "input"}
        if set(gate["inputs"]) != required_pins:
            raise ValueError(f"Incorrect input pin mapping for {gate['name']}")
    for endpoint in model["outputs"]:
        if endpoint not in driven:
            raise ValueError(f"Undriven output: {endpoint}")
    ready = set(inputs)
    ordered = []
    pending = list(gates)
    while pending:
        runnable = [gate for gate in pending if set(gate["inputs"].values()) <= ready]
        if not runnable:
            raise ValueError("Netlist has a cycle or undriven input")
        for gate in runnable:
            ordered.append(gate)
            ready.add(gate["output"])
            pending.remove(gate)
    return ordered


def analyze(model: dict[str, Any], cells: dict[str, Cell]) -> dict[str, Any]:
    """Analyze late rise/fall arrivals. All times in ns; loads in pF."""
    ordered = _validate_and_order(model, cells)
    constraints = model["constraints"]
    input_arrivals = constraints["input_arrival_ns"]
    input_slews = constraints["input_slew_ns"]
    required = constraints["output_required_ns"]
    external_loads = constraints.get("output_load_pf", {})
    if any(float(x) < 0 for x in input_slews.values()) or any(float(x) < 0 for x in external_loads.values()):
        raise ValueError("Slew and load must be nonnegative")
    loads = defaultdict(float, {k: float(v) for k, v in external_loads.items()})
    for gate in ordered:
        cell = cells[gate["cell"]]
        for pin_name, source_net in gate["inputs"].items():
            loads[source_net] += cell.pins[pin_name].capacitance

    states: dict[str, dict[str, Arrival]] = {}
    for pi in model["inputs"]:
        states[pi] = {edge: Arrival(float(input_arrivals[pi]), float(input_slews[pi])) for edge in ("rise", "fall")}

    for gate in ordered:
        cell = cells[gate["cell"]]
        output_pin = gate.get("output_pin") or next(p for p, v in cell.pins.items() if v.direction == "output")
        out = gate["output"]
        options: dict[str, list[Arrival]] = {"rise": [], "fall": []}
        for arc in cell.pins[output_pin].arcs:
            if arc.related_pin not in gate["inputs"]:
                continue
            net = gate["inputs"][arc.related_pin]
            for out_edge in ("rise", "fall"):
                if arc.sense == "positive_unate":
                    source_edges = (out_edge,)
                elif arc.sense == "negative_unate":
                    source_edges = ("fall" if out_edge == "rise" else "rise",)
                else:
                    source_edges = ("rise", "fall")
                for in_edge in source_edges:
                    input_state = states[net][in_edge]
                    delay = arc.tables["cell_" + out_edge].lookup(input_state.slew, loads[out])
                    slew = arc.tables[out_edge + "_transition"].lookup(input_state.slew, loads[out])
                    options[out_edge].append(Arrival(
                        input_state.time + delay, slew,
                        (net, in_edge, gate["name"], arc.related_pin, delay),
                    ))
        if not all(options.values()):
            raise ValueError(f"Missing supported timing arcs for {gate['name']}")
        states[out] = {edge: max(options[edge], key=lambda state: state.time) for edge in ("rise", "fall")}

    endpoints = []
    for net in model["outputs"]:
        if net not in required:
            raise ValueError(f"Missing required time for output: {net}")
        worst_edge = max(("rise", "fall"), key=lambda edge: states[net][edge].time)
        arrival = states[net][worst_edge]
        req = float(required[net])
        path = []
        current, edge = net, worst_edge
        while True:
            state = states[current][edge]
            pred = state.predecessor
            if pred is None:
                path.append({"net": current, "transition": edge, "arrival_ns": round(state.time, 6), "source": "primary_input"})
                break
            src, src_edge, instance, pin, arc_delay = pred
            path.append({"net": current, "transition": edge, "arrival_ns": round(state.time, 6),
                         "cell_instance": instance, "related_pin": pin, "cell_delay_ns": round(arc_delay, 6)})
            current, edge = src, src_edge
        path.reverse()
        endpoints.append({
            "endpoint": net,
            "arrival_ns": round(arrival.time, 6),
            "rise_arrival_ns": round(states[net]["rise"].time, 6),
            "fall_arrival_ns": round(states[net]["fall"].time, 6),
            "required_ns": round(req, 6),
            "slack_ns": round(req - arrival.time, 6),
            "worst_transition": worst_edge,
            "critical_path": path,
        })
    endpoints.sort(key=lambda e: (e["slack_ns"], e["endpoint"]))
    return {
        "analysis": "combinational_max_delay",
        "units": {"time": "ns", "capacitance": "pF"},
        "metrics": {
            "wns_ns": round(min(0.0, min(e["slack_ns"] for e in endpoints)), 6),
            "tns_ns": round(sum(min(0.0, e["slack_ns"]) for e in endpoints), 6),
            "violating_endpoints": sum(e["slack_ns"] < 0 for e in endpoints),
            "endpoints_analyzed": len(endpoints),
        },
        "endpoints": endpoints,
    }


def run(liberty_path: Path, netlist_path: Path) -> dict[str, Any]:
    model = json.loads(netlist_path.read_text(encoding="utf-8"))
    return analyze(model, parse_liberty(liberty_path.read_text(encoding="utf-8")))
