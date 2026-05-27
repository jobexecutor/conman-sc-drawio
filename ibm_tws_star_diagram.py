#!/usr/bin/env python3
"""
Create a star-style IBM Workload Scheduler network diagram from `conman sc` output.

The relationship rules are based on IBM Workload Scheduler documentation:

- In a single-domain network, the master domain manager communicates directly
  with workstations in that domain.
- A broker workstation hosts dynamic agents, pools, dynamic pools, and remote engines.
- An extended agent (X-AGENT) is hosted by a physical workstation or broker.

The script applies those documented rules and a small amount of naming-based
inference for hosted nodes commonly found in `conman sc` output, then writes a
draw.io diagram with the MASTER workstation at the center.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
import xml.etree.ElementTree as ET


NODE_LINE_RE = re.compile(r"^\S")
HEADER_PREFIXES = ("CPUID", "%sc", "/")
FIELD_SPECS = [
    ("node", 0, 12),
    ("limit", 12, 18),
    ("fence", 18, 24),
    ("date", 24, 33),
    ("time", 33, 39),
    ("state", 39, 51),
    ("method", 51, 92),
    ("domain", 92, None),
]
LINE_RE = re.compile(r"^(?P<cpuid>\S+)\s+(?P<run>\S+)(?P<rest>.*)$")

TYPE_COLORS = {
    "MASTER": "#d94841",
    "BMASTER": "#c05621",
    "MANAGER": "#dd6b20",
    "BROKER": "#805ad5",
    "FTA": "#2b6cb0",
    "S-AGENT": "#3182ce",
    "AGENT": "#38a169",
    "X-AGENT": "#2f855a",
    "POOL": "#718096",
    "D-POOL": "#4a5568",
    "REM-ENG": "#d69e2e",
    "UNKNOWN": "#718096",
}

PHYSICAL_TYPES = {"MASTER", "BMASTER", "MANAGER", "FTA", "S-AGENT", "BROKER"}
BROKER_HOSTED_TYPES = {"AGENT", "POOL", "D-POOL", "REM-ENG"}


@dataclass
class Workstation:
    cpuid: str
    run: str
    node_os: str
    ws_type: str
    limit: str
    fence: str
    date: str
    time: str
    state: str
    method: str
    domain: str
    parent: str | None = None
    relation: str = "derived"
    children: list[str] = field(default_factory=list)
    depth: int = 0
    angle: float = 0.0
    x: float = 0.0
    y: float = 0.0

    @property
    def display_type(self) -> str:
        return self.ws_type or "UNKNOWN"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a star network diagram from IBM Workload Scheduler `conman sc` output."
    )
    parser.add_argument("input_file", help="Path to the text file produced by `conman sc`.")
    parser.add_argument(
        "-o",
        "--output",
        default="ibm_tws_star_network.drawio",
        help="Output draw.io path. Default: ibm_tws_star_network.drawio",
    )
    parser.add_argument(
        "--title",
        default="IBM Workload Scheduler Star Network",
        help="Diagram title.",
    )
    return parser.parse_args()


def parse_input(path: Path) -> list[Workstation]:
    workstations: list[Workstation] = []

    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.rstrip()
        if not line or line.startswith(HEADER_PREFIXES):
            continue
        if not NODE_LINE_RE.match(line):
            continue

        match = LINE_RE.match(line)
        if not match:
            continue

        cpuid = match.group("cpuid").strip()
        run = match.group("run").strip()
        remainder = match.group("rest").lstrip()
        if len(remainder) < 92:
            remainder = remainder.ljust(92)

        fields = {}
        for field_name, start, end in FIELD_SPECS:
            raw_value = remainder[start:end] if end is not None else remainder[start:]
            fields[field_name] = raw_value.strip()

        node = fields["node"]
        limit = fields["limit"]
        fence = fields["fence"]
        date = fields["date"]
        time = fields["time"]
        state = fields["state"]
        method = fields["method"]
        domain = fields["domain"]

        if not cpuid or not node:
            continue

        node_tokens = node.replace("*", "").split()
        node_os = node_tokens[0] if node_tokens else "UNKNOWN"
        ws_type = node_tokens[-1] if node_tokens else "UNKNOWN"

        workstations.append(
            Workstation(
                cpuid=cpuid,
                run=run,
                node_os=node_os,
                ws_type=ws_type,
                limit=limit,
                fence=fence,
                date=date,
                time=time,
                state=state,
                method=method,
                domain=domain,
            )
        )

    if not workstations:
        raise ValueError("No workstation rows were parsed from the input file.")

    return workstations


def prompt_backup_masters() -> set[str]:
    prompt = (
        "Enter Backup Master Domain Manager workstation names separated by commas "
        "(leave blank if none): "
    )
    response = input(prompt).strip()
    if not response:
        return set()
    return {item.strip().upper() for item in response.split(",") if item.strip()}


def apply_backup_master_overrides(
    workstations: list[Workstation],
    backup_master_names: set[str],
) -> None:
    if not backup_master_names:
        return

    for workstation in workstations:
        if workstation.cpuid.upper() in backup_master_names and workstation.ws_type == "FTA":
            workstation.ws_type = "BMASTER"


def first_of_type(items: Iterable[Workstation], ws_type: str) -> Workstation | None:
    for item in items:
        if item.ws_type == ws_type:
            return item
    return None


def infer_parent_for_xagent(
    item: Workstation,
    all_nodes: dict[str, Workstation],
    brokers: list[Workstation],
    master: Workstation,
) -> tuple[str, str]:
    name = item.cpuid
    candidates: list[str] = []

    if name.startswith("XA-"):
        candidates.append(name[3:])

    for suffix in ("_XA", "_XAGENT", "_1"):
        if name.endswith(suffix):
            candidates.append(name[: -len(suffix)])

    candidates.append(name.split("_", 1)[0])

    for candidate in candidates:
        if candidate in all_nodes and all_nodes[candidate].ws_type in PHYSICAL_TYPES:
            return candidate, "named host"

    best_match = None
    best_score = -1
    physical_names = [node.cpuid for node in all_nodes.values() if node.ws_type in PHYSICAL_TYPES]
    for candidate in candidates:
        for physical_name in physical_names:
            if physical_name.startswith(candidate) or candidate.startswith(physical_name):
                score = min(len(candidate), len(physical_name))
                if score > best_score:
                    best_score = score
                    best_match = physical_name

    if best_match:
        return best_match, "prefix host"

    if len(brokers) == 1:
        return brokers[0].cpuid, "broker fallback"

    return master.cpuid, "master fallback"


def assign_relationships(workstations: list[Workstation]) -> tuple[Workstation, dict[str, Workstation]]:
    nodes = {item.cpuid: item for item in workstations}
    master = first_of_type(workstations, "MASTER")
    if master is None:
        raise ValueError("Could not find a MASTER workstation in the input.")

    brokers = [item for item in workstations if item.ws_type == "BROKER"]

    for item in workstations:
        if item.cpuid == master.cpuid:
            item.parent = None
            item.relation = "root"
            continue

        if item.ws_type == "BROKER":
            item.parent = master.cpuid
            item.relation = "broker on master"
        elif item.ws_type in BROKER_HOSTED_TYPES:
            parent = brokers[0].cpuid if brokers else master.cpuid
            item.parent = parent
            item.relation = "broker hosted" if brokers else "master fallback"
        elif item.ws_type == "X-AGENT":
            item.parent, item.relation = infer_parent_for_xagent(item, nodes, brokers, master)
        else:
            item.parent = master.cpuid
            item.relation = "direct to master"

    for item in workstations:
        item.children.clear()
    for item in workstations:
        if item.parent and item.parent in nodes:
            nodes[item.parent].children.append(item.cpuid)

    set_depths(master.cpuid, nodes, 0)
    return master, nodes


def set_depths(node_id: str, nodes: dict[str, Workstation], depth: int) -> None:
    node = nodes[node_id]
    node.depth = depth
    for child_id in node.children:
        set_depths(child_id, nodes, depth + 1)


def count_leaves(node_id: str, nodes: dict[str, Workstation]) -> int:
    node = nodes[node_id]
    if not node.children:
        return 1
    return sum(count_leaves(child_id, nodes) for child_id in node.children)


def max_subtree_depth(node_id: str, nodes: dict[str, Workstation]) -> int:
    node = nodes[node_id]
    if not node.children:
        return 1
    return 1 + max(max_subtree_depth(child_id, nodes) for child_id in node.children)


def compute_layout_dimensions(master: Workstation, nodes: dict[str, Workstation]) -> tuple[int, int, float]:
    child_ids = sorted(master.children)
    ring_step = 240.0
    min_arc_spacing = 170.0

    if not child_ids:
        outer_radius = ring_step
    else:
        ring_capacities: list[int] = []
        placed = 0
        ring_index = 1
        while placed < len(child_ids):
            radius = ring_step * ring_index
            capacity = max(6, int((2 * math.pi * radius) / min_arc_spacing))
            ring_capacities.append(capacity)
            placed += capacity
            ring_index += 1

        outer_depth = 1
        start_index = 0
        for ring_depth, capacity in enumerate(ring_capacities, start=1):
            ring_children = child_ids[start_index : start_index + capacity]
            for child_id in ring_children:
                subtree_depth = max_subtree_depth(child_id, nodes)
                outer_depth = max(outer_depth, ring_depth + subtree_depth - 1)
            start_index += capacity

        outer_radius = ring_step * outer_depth

    sidebar_width = 430
    margin = 120
    width = int(sidebar_width + outer_radius * 2 + margin * 2)
    height = int(outer_radius * 2 + margin * 2)
    return width, height, ring_step


def layout_tree(master: Workstation, nodes: dict[str, Workstation], width: int, height: int, ring_step: float) -> None:
    sidebar_width = 430
    margin = 120
    center_x = sidebar_width + margin + (width - sidebar_width - margin * 2) / 2
    center_y = height / 2
    master.x = center_x
    master.y = center_y
    master.angle = 0.0

    child_ids = sorted(master.children)
    if not child_ids:
        return

    min_arc_spacing = 170.0
    placed = 0
    ring_depth = 1
    while placed < len(child_ids):
        radius = ring_step * ring_depth
        capacity = max(6, int((2 * math.pi * radius) / min_arc_spacing))
        ring_children = child_ids[placed : placed + capacity]
        leaf_weights = {child_id: count_leaves(child_id, nodes) for child_id in ring_children}
        total_weight = sum(leaf_weights.values())
        start_angle = -math.pi / 2
        running = start_angle

        for child_id in ring_children:
            span = (2 * math.pi) * (leaf_weights[child_id] / total_weight)
            child_angle = running + span / 2
            place_subtree(
                child_id,
                nodes,
                center_x,
                center_y,
                ring_step,
                ring_depth,
                child_angle,
                min(span * 0.92, math.radians(34)),
            )
            running += span

        placed += len(ring_children)
        ring_depth += 1


def place_subtree(
    node_id: str,
    nodes: dict[str, Workstation],
    center_x: float,
    center_y: float,
    ring_step: float,
    depth: int,
    angle: float,
    span: float,
) -> None:
    node = nodes[node_id]
    radius = ring_step * depth
    node.angle = angle
    node.x = center_x + math.cos(angle) * radius
    node.y = center_y + math.sin(angle) * radius

    if not node.children:
        return

    child_ids = sorted(node.children)
    weights = {child_id: count_leaves(child_id, nodes) for child_id in child_ids}
    total_weight = sum(weights.values())
    local_start = angle - span / 2
    running = local_start
    for child_id in child_ids:
        child_span = span * (weights[child_id] / total_weight)
        child_angle = running + child_span / 2
        place_subtree(
            child_id,
            nodes,
            center_x,
            center_y,
            ring_step,
            depth + 1,
            child_angle,
            max(child_span * 0.9, math.radians(10)),
        )
        running += child_span


def render_drawio(
    title: str,
    master: Workstation,
    nodes: dict[str, Workstation],
    output_path: Path,
    source_name: str,
) -> None:
    width, height, ring_step = compute_layout_dimensions(master, nodes)
    layout_tree(master, nodes, width, height, ring_step)

    def add_geometry(parent: ET.Element, **attrs: str) -> ET.Element:
        geometry = ET.SubElement(parent, "mxGeometry", attrib=attrs)
        geometry.set("as", "geometry")
        return geometry

    def add_waypoint(geometry: ET.Element, x: float, y: float) -> None:
        array = geometry.find("Array")
        if array is None:
            array = ET.SubElement(geometry, "Array")
            array.set("as", "points")
        ET.SubElement(array, "mxPoint", x=str(int(x)), y=str(int(y)))

    def is_master_bmaster_link(parent: Workstation, child: Workstation) -> bool:
        return {parent.ws_type, child.ws_type} == {"MASTER", "BMASTER"}

    all_nodes = list(nodes.values())
    doc = ET.Element("mxfile", host="app.diagrams.net", modified="2026-05-05T00:00:00Z", agent="Codex")
    diagram = ET.SubElement(doc, "diagram", id="ibm-tws-network", name="IBM Workload Scheduler")
    graph = ET.SubElement(
        diagram,
        "mxGraphModel",
        dx="1600",
        dy="1200",
        grid="1",
        gridSize="10",
        guides="1",
        tooltips="1",
        connect="1",
        arrows="1",
        fold="1",
        page="1",
        pageScale="1",
        pageWidth=str(max(width + 100, 2200)),
        pageHeight=str(max(height + 100, 1700)),
        math="0",
        shadow="0",
    )
    root = ET.SubElement(graph, "root")
    ET.SubElement(root, "mxCell", id="0")
    ET.SubElement(root, "mxCell", id="1", parent="0")

    ET.SubElement(
        root,
        "mxCell",
        id="title",
        value=f"{title}&#xa;Source: {source_name}",
        style="text;html=1;strokeColor=none;fillColor=none;align=left;verticalAlign=top;"
        "whiteSpace=wrap;rounded=0;fontSize=22;fontStyle=1;",
        vertex="1",
        parent="1",
    )
    add_geometry(root[-1], x="40", y="30", width="700", height="60")

    note_value = (
        "MASTER is centered.&#xa;"
        "BMASTER nodes are prompted for at runtime and rendered as physical workstations.&#xa;"
        "BROKER-hosted nodes attach to a broker when present.&#xa;"
        "X-AGENT hosting is inferred from IBM rules plus workstation naming."
    )
    ET.SubElement(
        root,
        "mxCell",
        id="notes",
        value=note_value,
        style="rounded=1;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#cbd5e0;"
        "fontSize=12;align=left;verticalAlign=top;spacing=10;",
        vertex="1",
        parent="1",
    )
    add_geometry(root[-1], x="40", y="110", width="380", height="130")

    for depth in range(1, max(node.depth for node in all_nodes) + 1):
        radius = ring_step * depth
        ET.SubElement(
            root,
            "mxCell",
            id=f"ring-{depth}",
            style="ellipse;html=1;aspect=fixed;fillColor=none;strokeColor=#e2e8f0;"
            "dashed=1;dashPattern=6 6;pointerEvents=0;",
            vertex="1",
            parent="1",
        )
        add_geometry(
            root[-1],
            x=str(int(master.x - radius)),
            y=str(int(master.y - radius)),
            width=str(int(radius * 2)),
            height=str(int(radius * 2)),
        )

    node_ids: dict[str, str] = {}
    for index, node in enumerate(sorted(all_nodes, key=lambda item: (item.depth, item.cpuid)), start=1):
        cell_id = f"node-{index}"
        node_ids[node.cpuid] = cell_id
        color = TYPE_COLORS.get(node.display_type, TYPE_COLORS["UNKNOWN"])
        size = 92 if node.ws_type == "MASTER" else 78
        label = f"{node.cpuid}\n{node.node_os} {node.ws_type}"
        ET.SubElement(
            root,
            "mxCell",
            id=cell_id,
            value=label,
            style=(
                "ellipse;whiteSpace=wrap;html=1;aspect=fixed;align=center;verticalAlign=middle;"
                f"fillColor={color};strokeColor=#1a202c;fontColor=#ffffff;fontStyle=1;fontSize=12;"
            ),
            vertex="1",
            parent="1",
        )
        add_geometry(
            root[-1],
            x=str(int(node.x - size / 2)),
            y=str(int(node.y - size / 2)),
            width=str(size),
            height=str(size),
        )

    edge_index = 1
    for node in all_nodes:
        if not node.parent or node.parent not in nodes:
            continue
        parent = nodes[node.parent]
        if is_master_bmaster_link(parent, node):
            dx = node.x - parent.x
            dy = node.y - parent.y
            length = math.hypot(dx, dy) or 1.0
            offset_x = -dy / length * 8.0
            offset_y = dx / length * 8.0
            for index_in_pair, direction in enumerate((1.0, -1.0), start=1):
                value = node.relation if index_in_pair == 1 else ""
                ET.SubElement(
                    root,
                    "mxCell",
                    id=f"edge-{edge_index}",
                    value=value,
                    style="edgeStyle=none;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;"
                    "strokeColor=#94a3b8;strokeWidth=2;fontSize=10;labelBackgroundColor=#f7fafc;"
                    "endArrow=none;startArrow=none;",
                    edge="1",
                    parent="1",
                    source=node_ids[node.parent],
                    target=node_ids[node.cpuid],
                )
                geometry = add_geometry(root[-1], relative="1")
                midpoint_x = (parent.x + node.x) / 2 + offset_x * direction
                midpoint_y = (parent.y + node.y) / 2 + offset_y * direction
                add_waypoint(geometry, midpoint_x, midpoint_y)
                edge_index += 1
            continue

        ET.SubElement(
            root,
            "mxCell",
            id=f"edge-{edge_index}",
            value=node.relation,
            style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;"
            "strokeColor=#94a3b8;fontSize=10;labelBackgroundColor=#f7fafc;",
            edge="1",
            parent="1",
            source=node_ids[node.parent],
            target=node_ids[node.cpuid],
        )
        add_geometry(root[-1], relative="1")
        edge_index += 1

    tree = ET.ElementTree(doc)
    ET.indent(tree, space="  ")
    xml_bytes = ET.tostring(doc, encoding="utf-8", xml_declaration=True)
    output_path.write_bytes(xml_bytes)


def main() -> int:
    args = parse_args()
    input_path = Path(args.input_file).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    try:
        workstations = parse_input(input_path)
        backup_master_names = prompt_backup_masters()
        apply_backup_master_overrides(workstations, backup_master_names)
        master, nodes = assign_relationships(workstations)
        render_drawio(args.title, master, nodes, output_path, input_path.name)
    except Exception as exc:  # pragma: no cover - CLI error path
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote diagram to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
