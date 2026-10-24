"""Route a unicorne board without external tools: fixed lanes for the key grid,
then KiCad's own ratsnest, connection by connection, with the grid maze router.

Run with KiCad's bundled Python on a freshly generated board:

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 tools/finish_route.py [mx]

1. tools/lanes.py lays rows, columns and per-key LED wiring (locked).
2. kicad-cli DRC lists what is still unconnected (pairs of items).
3. Each pair is routed with tools/maze_route.py's A*, around every existing
   track, via, pad, hole and edge; shortest first, GND last.
4. GND is poured on both layers and the board is saved.
"""
import json
import math
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lanes  # noqa: E402
import maze_route as M  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
BOARDS = {"mx": "unicorne.kicad_pcb", "choc": "unicorne-choc.kicad_pcb"}
WIDTH = {"LED_VDD": 0.3, "LED_SRC": 0.3, "LED_EXT": 0.3, "GND": 0.3, "+3V3": 0.3,
         "VBAT": 0.4, "BATT+": 0.4}


def unconnected(path):
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "drc.json"
        subprocess.run([str(CLI), "pcb", "drc", "--format", "json", "-o", str(out), str(path)],
                       capture_output=True)
        return json.loads(out.read_text())["unconnected_items"]


def stamp_copper(board, grid, netcode, vias):
    """Existing tracks and vias as obstacles (their own net may reuse them)."""
    for t in board.GetTracks():
        net = netcode.get(t.GetNetname(), M.BLOCK)
        if isinstance(t, pcbnew.PCB_VIA):
            p = t.GetPosition()
            vias.add(pcbnew.ToMM(p.x), pcbnew.ToMM(p.y), net)
            r = pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu)) / 2 + M.CLEAR + M.SIGNAL_W / 2 + M.SLACK
            grid.disc((0, 1), pcbnew.ToMM(p.x), pcbnew.ToMM(p.y), r, net)
            continue
        L = 0 if t.GetLayer() == pcbnew.F_Cu else 1
        a, b = t.GetStart(), t.GetEnd()
        ax, ay, bx, by = (pcbnew.ToMM(v) for v in (a.x, a.y, b.x, b.y))
        r = pcbnew.ToMM(t.GetWidth()) / 2 + M.CLEAR + M.SIGNAL_W / 2 + M.SLACK
        steps = max(1, int(math.hypot(bx - ax, by - ay) / (M.P / 2)))
        for i in range(steps + 1):
            f = i / steps
            grid.disc((L,), ax + (bx - ax) * f, ay + (by - ay) * f, r, net)


def copper_items(board, name):
    items = [t for t in board.GetTracks() if t.GetNetname() == name]
    items += [p for fp in board.GetFootprints() for p in fp.Pads() if p.GetNetname() == name]
    return items


def end_cells(board, grid, net, name, item):
    """Grid cells lying on the copper of the ratsnest end (a pad, track or via),
    so a route that reaches one of them really touches it."""
    d = item["description"]
    layers = (0, 1) if ("PTH" in d or "Via" in d) else ((0,) if "F.Cu" in d else (1,))
    x, y = item["pos"]["x"], item["pos"]["y"]
    pt = pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))
    obj = min(copper_items(board, name), key=lambda o: (0 if o.HitTest(pt, pcbnew.FromMM(0.05)) else 1,
              (o.GetPosition().x - pt.x) ** 2 + (o.GetPosition().y - pt.y) ** 2))
    box = obj.GetBoundingBox()
    c0, r0 = grid.cell(pcbnew.ToMM(box.GetLeft()) - M.P, pcbnew.ToMM(box.GetTop()) - M.P)
    c1, r1 = grid.cell(pcbnew.ToMM(box.GetRight()) + M.P, pcbnew.ToMM(box.GetBottom()) + M.P)
    cells = []
    for cy in range(r0, r1 + 1):
        for cx in range(c0, c1 + 1):
            px, py = grid.xy(cx, cy)
            if obj.HitTest(pcbnew.VECTOR2I(pcbnew.FromMM(px), pcbnew.FromMM(py)), pcbnew.FromMM(0.05)):
                cells += [(L, cy * grid.nx + cx) for L in layers if grid.own[L][cy * grid.nx + cx] == net]
    if not cells:
        cx, cy = grid.cell(x, y)
        cells = [(layers[0], cy * grid.nx + cx)]
    return cells


STITCH_PITCH = 8.0


def stitch(board):
    """GND vias on a coarse grid wherever both GND pours actually have copper
    (checked after filling), so the top and bottom pours are tied together."""
    zones = {z.GetLayer(): z for z in board.Zones() if z.GetNetname() == "GND"}
    holes = [p for fp in board.GetFootprints() for p in fp.Pads() if p.HasHole()]
    vias = [t for t in board.GetTracks() if isinstance(t, pcbnew.PCB_VIA)]
    gnd = board.FindNet("GND")
    box = board.GetBoardEdgesBoundingBox()
    r = M.VIA_D / 2 + 0.3                      # via plus zone clearance must sit in copper
    n = 0
    y = pcbnew.ToMM(box.GetTop())
    while y < pcbnew.ToMM(box.GetBottom()):
        x = pcbnew.ToMM(box.GetLeft())
        while x < pcbnew.ToMM(box.GetRight()):
            pts = [pcbnew.VECTOR2I(pcbnew.FromMM(x + dx), pcbnew.FromMM(y + dy))
                   for dx, dy in ((0, 0), (r, 0), (-r, 0), (0, r), (0, -r))]
            centre = pts[0]
            if (all(zones[L].HitTestFilledArea(L, p) for L in (pcbnew.F_Cu, pcbnew.B_Cu) for p in pts)
                    and not any(h.HitTest(centre, pcbnew.FromMM(M.VIA_D / 2 + 0.35)) for h in holes)
                    and not any(v.HitTest(centre, pcbnew.FromMM(M.VIA_D + 0.3)) for v in vias)):
                v = pcbnew.PCB_VIA(board)
                v.SetPosition(centre)
                v.SetWidth(pcbnew.FromMM(M.VIA_D))
                v.SetDrill(pcbnew.FromMM(M.VIA_DRILL))
                v.SetNet(gnd)
                board.Add(v)
                vias.append(v)
                n += 1
            x += STITCH_PITCH
        y += STITCH_PITCH
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    return n


def route(variant="mx"):
    t0 = time.time()
    path = ROOT / BOARDS[variant]
    board = pcbnew.LoadBoard(str(path))
    if len(board.GetTracks()):
        sys.exit(f"{path.name} already has tracks: run tools/gen_pcb.py {variant} first")
    print(f"laid {lanes.lay(board)} lane tracks", flush=True)
    board.Save(str(path))
    pairs = unconnected(path)
    print(f"{len(pairs)} connections left after the lanes", flush=True)

    board = pcbnew.LoadBoard(str(path))
    outline = None
    for d in board.GetDrawings():
        if d.GetLayer() == pcbnew.Edge_Cuts and d.GetShape() == pcbnew.SHAPE_T_POLY:
            pts = d.GetPolyShape().Outline(0)
            outline = [(pcbnew.ToMM(pts.CPoint(i).x), pcbnew.ToMM(pts.CPoint(i).y))
                       for i in range(pts.PointCount())]
    grid = M.Grid(outline)
    names = sorted({p.GetNetname() for fp in board.GetFootprints() for p in fp.Pads() if p.GetNetname()})
    netcode = {n: i + 1 for i, n in enumerate(names)}
    M.edge_mask(board, grid, outline)
    M.stamp_pads(board, grid, netcode)
    vias = M.Vias(grid)
    stamp_copper(board, grid, netcode, vias)

    def net_of(pair):
        m = re.search(r"\[([^\]]+)\]", pair["items"][0]["description"])
        return m.group(1) if m else None

    def length(pair):
        a, b = pair["items"][0]["pos"], pair["items"][1]["pos"]
        return math.hypot(a["x"] - b["x"], a["y"] - b["y"])

    # into the nice!nano first while its pins have room, GND last (the pours help)
    on_mcu = lambda p: any(" of U1" in i["description"] for i in p["items"])
    # very short local hops first (they have one sensible path), then into the
    # nice!nano while its pins have room, then the rest; GND last
    tier = lambda p: 3 if net_of(p) == "GND" else 0 if length(p) < 8 else 1 if on_mcu(p) else 2
    supply = lambda p: net_of(p).split("/")[-1] in WIDTH
    pairs.sort(key=lambda p: (tier(p), not supply(p), length(p)))
    failed = []
    for i, pair in enumerate(pairs):
        name = net_of(pair)
        if name not in netcode:
            failed.append((name, "unknown net"))
            continue
        net = netcode[name]
        a, b = pair["items"]
        src, dst = end_cells(board, grid, net, name, a), set(end_cells(board, grid, net, name, b))
        goal = grid.cell(b["pos"]["x"], b["pos"]["y"])
        width = WIDTH.get(name.split("/")[-1], M.SIGNAL_W)
        p = M.astar(grid, net, src, dst, width > M.SIGNAL_W, goal, vias)
        if p is None and width > M.SIGNAL_W and length(pair) < 8:
            # a short supply hop out of a tight pad (e.g. the middle of a 3-pad
            # solder jumper): signal width is plenty for a few millimetres
            width = M.SIGNAL_W
            p = M.astar(grid, net, src, dst, False, goal, vias)
        if p is None:
            failed.append((name, a["description"][:40], b["description"][:40]))
            continue
        M.commit(board, grid, net, board.FindNet(name), p, width, vias)
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(pairs)}, {len(failed)} failed, {time.time() - t0:.0f}s", flush=True)
    M.pour_gnd(board, outline)
    print(f"added {stitch(board)} GND stitching vias", flush=True)
    board.Save(str(path))
    print(f"done in {time.time() - t0:.0f}s: {len(failed)} of {len(pairs)} connections failed")
    for f in failed:
        print("  unrouted:", *f)


if __name__ == "__main__":
    route(sys.argv[1] if len(sys.argv) > 1 else "mx")
