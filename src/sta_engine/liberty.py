"""Small, intentionally limited Liberty NLDM reader for educational STA.

Supports 2-D pin timing tables with explicit indices and single-output cells.
Does not implement all Liberty grammar or state-dependent timing arcs.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Table:
    slew_points: tuple[float, ...]
    load_points: tuple[float, ...]
    values: tuple[tuple[float, ...], ...]

    def lookup(self, slew: float, load: float) -> float:
        """Bilinear interpolation; clamp queries outside characterized bounds."""
        def bracket(axis: tuple[float, ...], x: float) -> tuple[int, int, float]:
            if len(axis) == 1 or x <= axis[0]:
                return 0, 0, 0.0
            if x >= axis[-1]:
                end = len(axis) - 1
                return end, end, 0.0
            for i in range(len(axis) - 1):
                if axis[i] <= x <= axis[i + 1]:
                    return i, i + 1, (x - axis[i]) / (axis[i + 1] - axis[i])
            raise ValueError("Invalid NLDM axis")

        x0, x1, tx = bracket(self.slew_points, slew)
        y0, y1, ty = bracket(self.load_points, load)
        lower = self.values[x0][y0] * (1 - ty) + self.values[x0][y1] * ty
        upper = self.values[x1][y0] * (1 - ty) + self.values[x1][y1] * ty
        return lower * (1 - tx) + upper * tx


@dataclass(frozen=True)
class Arc:
    related_pin: str
    sense: str
    tables: dict[str, Table]


@dataclass(frozen=True)
class Pin:
    direction: str
    capacitance: float
    arcs: tuple[Arc, ...]


@dataclass(frozen=True)
class Cell:
    pins: dict[str, Pin]


def _blocks(source: str, name: str):
    """Yield (argument, balanced-block-content) for named Liberty groups."""
    pattern = re.compile(r"\b" + re.escape(name) + r"\s*\(\s*([^()]*)\s*\)\s*\{", re.S)
    offset = 0
    while match := pattern.search(source, offset):
        depth = 1
        i = match.end()
        start = i
        quoted = False
        while i < len(source):
            ch = source[i]
            if ch == '"' and (i == 0 or source[i - 1] != "\\"):
                quoted = not quoted
            if not quoted:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        yield match.group(1).strip().strip('"'), source[start:i]
                        offset = i + 1
                        break
            i += 1
        else:
            raise ValueError(f"Unbalanced Liberty group: {name}")


def _attribute(source: str, name: str, default: str | None = None) -> str | None:
    m = re.search(r"\b" + re.escape(name) + r'\s*:\s*(?:"([^"]*)"|([^;\n]+))\s*;', source)
    return (m.group(1) if m.group(1) is not None else m.group(2)).strip() if m else default


def _numbers(raw: str) -> tuple[float, ...]:
    return tuple(float(v.strip()) for v in raw.split(",") if v.strip())


def _table(body: str) -> Table:
    axes = []
    for key in ("index_1", "index_2"):
        m = re.search(r"\b" + key + r'\s*\(\s*"([^"]+)"\s*\)\s*;', body, re.S)
        if not m:
            raise ValueError(f"NLDM {key} must be explicit in this starter parser")
        axis = _numbers(m.group(1))
        if not axis or any(a >= b for a, b in zip(axis, axis[1:])):
            raise ValueError(f"NLDM {key} must be strictly increasing")
        axes.append(axis)
    m = re.search(r"\bvalues\s*\((.*?)\)\s*;", body, re.S)
    if not m:
        raise ValueError("Missing NLDM values")
    rows = tuple(_numbers(row) for row in re.findall(r'"([^"]+)"', m.group(1)))
    if len(rows) != len(axes[0]) or any(len(row) != len(axes[1]) for row in rows):
        raise ValueError("NLDM dimensions do not match indices")
    return Table(axes[0], axes[1], rows)


def parse_liberty(text: str) -> dict[str, Cell]:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    cells: dict[str, Cell] = {}
    for cell_name, cell_body in _blocks(text, "cell"):
        pins: dict[str, Pin] = {}
        for pin_name, pin_body in _blocks(cell_body, "pin"):
            direction = _attribute(pin_body, "direction")
            if direction not in {"input", "output"}:
                continue
            capacitance = float(_attribute(pin_body, "capacitance", "0") or "0")
            arcs = []
            for _, timing_body in _blocks(pin_body, "timing"):
                related_pin = _attribute(timing_body, "related_pin")
                if not related_pin:
                    continue
                sense = _attribute(timing_body, "timing_sense", "non_unate")
                if sense not in {"negative_unate", "positive_unate", "non_unate"}:
                    raise ValueError(f"Unsupported timing_sense: {sense}")
                tables = {}
                for table_name in ("cell_rise", "cell_fall", "rise_transition", "fall_transition"):
                    entries = list(_blocks(timing_body, table_name))
                    if entries:
                        tables[table_name] = _table(entries[0][1])
                if all(t in tables for t in ("cell_rise", "cell_fall", "rise_transition", "fall_transition")):
                    arcs.append(Arc(related_pin, sense, tables))
            pins[pin_name] = Pin(direction, capacitance, tuple(arcs))
        if pins:
            cells[cell_name] = Cell(pins)
    if not cells:
        raise ValueError("No supported cells found in Liberty")
    return cells
