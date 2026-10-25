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
    return n + stitch_islands(board)


def stitch_islands(board, step=0.25):
    """The coarse grid can miss a small pour island entirely, leaving it (and any GND
    pad it holds) cut off: give every island without a GND via or GND through-hole
    pad one via, found on a fine grid where both pours have copper."""
    zones = {z.GetLayer(): z for z in board.Zones() if z.GetNetname() == "GND"}
    holes = [p for fp in board.GetFootprints() for p in fp.Pads() if p.HasHole()]
    gnd_links = [v.GetPosition() for v in board.GetTracks()
                 if isinstance(v, pcbnew.PCB_VIA) and v.GetNetname() == "GND"]
    gnd_links += [p.GetPosition() for p in holes if p.GetNetname() == "GND"]
    vias = [t for t in board.GetTracks() if isinstance(t, pcbnew.PCB_VIA)]
    gnd = board.FindNet("GND")
    r = M.VIA_D / 2 + 0.3
    n = 0
    for layer, zone in zones.items():
        polys = zone.GetFilledPolysList(layer)
        for i in range(polys.OutlineCount()):
            outline = polys.Outline(i)
            if any(outline.PointInside(p) for p in gnd_links):
                continue
            box = outline.BBox()
            spot = None
            y = pcbnew.ToMM(box.GetTop())
            while spot is None and y < pcbnew.ToMM(box.GetBottom()):
                x = pcbnew.ToMM(box.GetLeft())
                while x < pcbnew.ToMM(box.GetRight()):
                    pts = [pcbnew.VECTOR2I(pcbnew.FromMM(x + dx), pcbnew.FromMM(y + dy))
                           for dx, dy in ((0, 0), (r, 0), (-r, 0), (0, r), (0, -r))]
                    if (outline.PointInside(pts[0])
                            and all(zones[L].HitTestFilledArea(L, p) for L in zones for p in pts)
                            and not any(h.HitTest(pts[0], pcbnew.FromMM(M.VIA_D / 2 + 0.35)) for h in holes)
                            and not any(v.HitTest(pts[0], pcbnew.FromMM(M.VIA_D + 0.3)) for v in vias)):
                        spot = pts[0]
                        break
                    x += step
                y += step
            if spot is None:
                continue
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(spot)
            v.SetWidth(pcbnew.FromMM(M.VIA_D))
            v.SetDrill(pcbnew.FromMM(M.VIA_DRILL))
            v.SetNet(gnd)
            board.Add(v)
            vias.append(v)
            gnd_links.append(spot)
            n += 1
    if n:
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    return n


HOLE_R = 1.1                  # M2 drill radius
HOLE_KEEPOUT = 2.04           # MountingHole_2.2mm_M2_ISO7380 courtyard radius


def move_holes(board, reach=6.0, step=0.25):
    """Nudge mounting holes that collide with the lane tracks to the nearest spot
    clear of tracks, pads, courtyards and edges (the lanes are only known after
    placement)."""
    tracks = list(board.GetTracks())
    fps = list(board.GetFootprints())
    edges = [d for d in board.GetDrawings() if d.GetLayer() == pcbnew.Edge_Cuts]
    for fp in fps:
        edges += [g for g in fp.GraphicalItems() if g.GetLayer() == pcbnew.Edge_Cuts]
    outline = next(d for d in board.GetDrawings()
                   if d.GetLayer() == pcbnew.Edge_Cuts and d.GetShape() == pcbnew.SHAPE_T_POLY).GetPolyShape()
    holes = [fp for fp in fps if fp.GetReference().startswith("H") and fp.GetReference()[1:].isdigit()]
    others = [fp for fp in fps if fp not in holes]
    keys = [fp for fp in others if fp.GetReference().startswith("SW") and fp.HasField("Key")]
    choc = any("Choc" in str(k.GetFPID().GetLibItemName()) for k in keys)
    yards = [fp.GetCourtyard(L) for fp in others for L in (pcbnew.F_CrtYd, pcbnew.B_CrtYd)
             if not (choc and L == pcbnew.F_CrtYd and fp in keys)]
    pads = [p for fp in others for p in fp.Pads()]

    def ok(pt):
        if not outline.Contains(pt):
            return False
        if any(e.HitTest(pt, pcbnew.FromMM(HOLE_KEEPOUT + 0.5)) for e in edges):
            return False
        if any(t.HitTest(pt, pcbnew.FromMM(HOLE_R + 0.3)) for t in tracks):
            return False
        if any(p.HitTest(pt, pcbnew.FromMM(HOLE_R + 0.25)) for p in pads):
            return False
        if any(y.OutlineCount() and y.Collide(pt, pcbnew.FromMM(HOLE_KEEPOUT)) for y in yards):
            return False
        for k in keys if choc else ():        # Choc: the body at PCB level, as in gen_pcb
            c = k.GetPosition()
            a = math.radians(k.GetOrientationDegrees())
            dx, dy = pcbnew.ToMM(pt.x - c.x), pcbnew.ToMM(pt.y - c.y)
            lx, ly = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
            if abs(lx) < 6.9 + HOLE_KEEPOUT and abs(ly) < 6.9 + HOLE_KEEPOUT:
                return False
        return True

    moved = []
    n = int(reach / step)
    spots = sorted(((i * step) ** 2 + (j * step) ** 2, i, j) for i in range(-n, n + 1) for j in range(-n, n + 1))
    for h in holes:
        c = h.GetPosition()
        if ok(c):
            continue
        for d2, i, j in spots:
            pt = pcbnew.VECTOR2I(c.x + pcbnew.FromMM(i * step), c.y + pcbnew.FromMM(j * step))
            if ok(pt):
                h.SetPosition(pt)
                moved.append((h.GetReference(), math.sqrt(d2)))
                break
        else:
            moved.append((h.GetReference(), -1.0))
    return moved


def route(variant="mx", attempts=10):
    """Route, and if signal connections fail, start again from the placed board
    with those connections first. Keeps the attempt with the fewest failures."""
    import shutil
    path = ROOT / BOARDS[variant]
    board = pcbnew.LoadBoard(str(path))
    if len(board.GetTracks()):
        sys.exit(f"{path.name} already has tracks: run tools/gen_pcb.py {variant} first")
    placed = ROOT / "build" / "route" / f"{variant}_placed.kicad_pcb"
    placed.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(path, placed)
    first, best = [], None
    for attempt in range(attempts):
        shutil.copy(placed, path)
        failed = route_once(variant, path, first)
        signal = [f for f in failed if f[0] != "GND"]
        # GND failures are fine if the pours join them up; DRC after the pours says
        left = unconnected(path)
        print(f"attempt {attempt + 1}: {len(signal)} signal connections failed, "
              f"{len(left)} unconnected after the pours", flush=True)
        if best is None or (len(signal), len(left)) < best[0]:
            best = ((len(signal), len(left)), path.read_bytes())
        if not signal and not left:
            break
        # failed signal nets go first next time
        first = list(dict.fromkeys(first + [f[0] for f in signal]))
    path.write_bytes(best[1])
    print(f"kept the best attempt: {best[0][0]} signal connections failed, {best[0][1]} unconnected")
    print(f"removed {drop_stubs(path)} dangling track stubs")


def drop_stubs(path):
    """Delete the short dead-end stubs the maze router sometimes leaves, then refill."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "drc.json"
        subprocess.run([str(CLI), "pcb", "drc", "--format", "json", "-o", str(out), str(path)], capture_output=True)
        found = json.loads(out.read_text())["violations"]
    stubs = {i["uuid"] for v in found if v["type"] == "track_dangling" for i in v["items"]}
    if not stubs:
        return 0
    board = pcbnew.LoadBoard(str(path))
    dead = [t for t in board.GetTracks() if t.m_Uuid.AsString() in stubs and not isinstance(t, pcbnew.PCB_VIA)
            and t.GetLength() < pcbnew.FromMM(1.0)]
    for t in dead:
        board.Remove(t)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(str(path))
    return len(dead)


def route_once(variant, path, first):
    t0 = time.time()
    board = pcbnew.LoadBoard(str(path))
    print(f"laid {lanes.lay(board, variant)} lane tracks", flush=True)
    for ref, d in move_holes(board):
        print(f"  {ref} moved {d:.2f} mm off the lanes", flush=True)
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
    tier = lambda p: (-1 if net_of(p) in first else 3 if net_of(p) == "GND"
                      else 0 if length(p) < 8 else 1 if on_mcu(p) else 2)
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
    print(f"  done in {time.time() - t0:.0f}s: {len(failed)} of {len(pairs)} connections failed")
    for f in failed:
        if f[0] != "GND":
            print("    unrouted:", *f)
    return failed


if __name__ == "__main__":
    route(sys.argv[1] if len(sys.argv) > 1 else "mx")
