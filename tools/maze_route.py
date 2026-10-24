"""Grid maze router for the unicorne boards (no external tools).

Run with KiCad's bundled Python after tools/gen_pcb.py:

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 tools/maze_route.py [mx|choc]

Two layers on a 0.25 mm grid, A* with vias. Copper of each net is stamped onto
the grid with a clearance halo; a track centre may only enter cells that are
free or belong to its own net. Nets are routed shortest first; anything that
can't be routed is reported and left for a second pass (or Freerouting).
Run it on a freshly generated board (no tracks).
"""
import heapq
import math
import sys
import time
from array import array
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
BOARDS = {"mx": "unicorne.kicad_pcb", "choc": "unicorne-choc.kicad_pcb"}
P = 0.25                     # grid pitch, mm
CLEAR = 0.2                  # copper clearance
EDGE_CLEAR = 0.3             # copper to board edge / cutouts
SIGNAL_W, POWER_W = 0.2, 0.4
VIA_D, VIA_DRILL = 0.6, 0.3
VIA_COST = 12.0
LAYERS = (pcbnew.F_Cu, pcbnew.B_Cu)
BLOCK = -1
SLACK = P / 2                # a diagonal step passes up to this much closer than its end cells
ZONE_CLEAR = 0.3
FIRST_WIDTH = {"LED_VDD": 0.3}   # matches the LED net class


def mm(v):
    return pcbnew.FromMM(v)


class Grid:
    def __init__(self, outline):
        xs, ys = [x for x, _ in outline], [y for _, y in outline]
        self.x0, self.y0 = min(xs) - 1, min(ys) - 1
        self.nx = int((max(xs) - min(xs)) / P) + 9
        self.ny = int((max(ys) - min(ys)) / P) + 9
        self.n = self.nx * self.ny
        self.own = [array("i", [0]) * self.n for _ in LAYERS]

    def cell(self, x, y):
        return int(round((x - self.x0) / P)), int(round((y - self.y0) / P))

    def xy(self, cx, cy):
        return self.x0 + cx * P, self.y0 + cy * P

    def stamp(self, layer, idx, net):
        o = self.own[layer]
        cur = o[idx]
        if cur == 0:
            o[idx] = net
        elif cur != net and cur != BLOCK:
            o[idx] = BLOCK

    def disc(self, layers, x, y, r, net):
        cx0, cy0 = self.cell(x - r, y - r)
        cx1, cy1 = self.cell(x + r, y + r)
        r2 = (r / P) ** 2
        fx, fy = (x - self.x0) / P, (y - self.y0) / P
        for cy in range(max(cy0, 0), min(cy1, self.ny - 1) + 1):
            for cx in range(max(cx0, 0), min(cx1, self.nx - 1) + 1):
                if (cx - fx) ** 2 + (cy - fy) ** 2 <= r2:
                    for L in layers:
                        self.stamp(L, cy * self.nx + cx, net)


def edge_mask(board, grid, outline_pts):
    """Block everything outside the outline (scanline fill) and near any edge."""
    poly = outline_pts
    for cy in range(grid.ny):
        y = grid.y0 + cy * P
        xs = []
        for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
            if (y1 > y) != (y2 > y):
                xs.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
        xs.sort()
        inside = [False] * grid.nx
        for a, b in zip(xs[0::2], xs[1::2]):
            for cx in range(max(0, int(math.ceil((a - grid.x0) / P))),
                            min(grid.nx, int(math.floor((b - grid.x0) / P)) + 1)):
                inside[cx] = True
        for cx in range(grid.nx):
            if not inside[cx]:
                for L in range(2):
                    grid.own[L][cy * grid.nx + cx] = BLOCK
    # every Edge.Cuts item: outline, slots, LED cutouts
    shapes = [d for d in board.GetDrawings() if d.GetLayer() == pcbnew.Edge_Cuts]
    for fp in board.GetFootprints():
        shapes += [g for g in fp.GraphicalItems() if g.GetLayer() == pcbnew.Edge_Cuts]
    halo = EDGE_CLEAR + SIGNAL_W / 2 + SLACK
    for s in shapes:
        box = s.GetBoundingBox()
        x0, y0 = pcbnew.ToMM(box.GetLeft()) - halo, pcbnew.ToMM(box.GetTop()) - halo
        x1, y1 = pcbnew.ToMM(box.GetRight()) + halo, pcbnew.ToMM(box.GetBottom()) + halo
        c0, r0 = grid.cell(x0, y0)
        c1, r1 = grid.cell(x1, y1)
        for cy in range(max(r0, 0), min(r1, grid.ny - 1) + 1):
            for cx in range(max(c0, 0), min(c1, grid.nx - 1) + 1):
                x, y = grid.xy(cx, cy)
                if s.HitTest(pcbnew.VECTOR2I(mm(x), mm(y)), mm(halo)):
                    for L in range(2):
                        grid.own[L][cy * grid.nx + cx] = BLOCK
    # inside the LED cutouts (HitTest only sees their outline)
    for fp in board.GetFootprints():
        cut = [g for g in fp.GraphicalItems() if g.GetLayer() == pcbnew.Edge_Cuts]
        if cut:
            c = fp.GetPosition()
            grid.disc((0, 1), pcbnew.ToMM(c.x), pcbnew.ToMM(c.y), 1.9, BLOCK)


def stamp_pads(board, grid, netcode):
    """Pad copper plus clearance halo; NPTH holes block both layers."""
    pads = {}
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            net = netcode.get(pad.GetNetname(), BLOCK) if pad.GetNetname() else BLOCK
            npth = pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH
            layers = [i for i, L in enumerate(LAYERS) if pad.IsOnLayer(L)] or [0, 1]
            if npth:
                layers, net = [0, 1], BLOCK
            halo = (EDGE_CLEAR if npth else CLEAR) + SIGNAL_W / 2 + SLACK
            box = pad.GetBoundingBox()
            c0, r0 = grid.cell(pcbnew.ToMM(box.GetLeft()) - halo, pcbnew.ToMM(box.GetTop()) - halo)
            c1, r1 = grid.cell(pcbnew.ToMM(box.GetRight()) + halo, pcbnew.ToMM(box.GetBottom()) + halo)
            inner = []
            for cy in range(max(r0, 0), min(r1, grid.ny - 1) + 1):
                for cx in range(max(c0, 0), min(c1, grid.nx - 1) + 1):
                    x, y = grid.xy(cx, cy)
                    pt = pcbnew.VECTOR2I(mm(x), mm(y))
                    if pad.HitTest(pt, mm(halo)):
                        for L in layers:
                            grid.stamp(L, cy * grid.nx + cx, net)
                        if net != BLOCK and pad.HitTest(pt, 0):
                            inner += [(L, cy * grid.nx + cx) for L in layers]
            if net != BLOCK:
                pads.setdefault(net, []).append((pad, inner))
    return pads


def astar(grid, net, sources, targets, fat, goal_xy, vias=None):
    """Shortest path from any source (layer, idx) to any target; returns cells."""
    nx = grid.nx
    own = grid.own
    INF = 1e18
    g = {}
    prev = {}
    heap = []
    tx, ty = goal_xy

    def h(idx):
        cx, cy = idx % nx, idx // nx
        dx, dy = abs(cx - tx), abs(cy - ty)
        return (max(dx, dy) + 0.414 * min(dx, dy)) * 1.02

    def free(L, idx):
        o = own[L][idx]
        if o != 0 and o != net:
            return False
        if fat:
            for d in (1, -1, nx, -nx):
                o2 = own[L][idx + d]
                if o2 != 0 and o2 != net:
                    return False
        return True

    for s in sources:
        g[s] = 0.0
        heapq.heappush(heap, (h(s[1]), 0.0, s))
    steps = ((1, 1.0), (-1, 1.0), (nx, 1.0), (-nx, 1.0),
             (nx + 1, 1.414), (nx - 1, 1.414), (-nx + 1, 1.414), (-nx - 1, 1.414))
    expanded = 0
    while heap:
        f, gc, s = heapq.heappop(heap)
        if gc > g.get(s, INF):
            continue
        if s in targets:
            path = [s]
            while path[-1] in prev:
                path.append(prev[path[-1]])
            return path[::-1]
        expanded += 1
        if expanded > 3000000:
            return None
        L, idx = s
        for d, c in steps:
            j = idx + d
            if j < 0 or j >= grid.n:
                continue
            t = (L, j)
            if t not in targets and not free(L, j):
                continue
            ng = gc + c
            if ng < g.get(t, INF):
                g[t] = ng
                prev[t] = s
                heapq.heappush(heap, (ng + h(j), ng, t))
        # via: reuse one of this net's vias here, else a new one needs room on
        # both layers and a hole's distance from every other via
        o = 1 - L
        t = (o, idx)
        cost = VIA_COST
        if vias is not None and vias.at.get(idx) == net:
            ok, cost = True, 1.0
        else:
            ok = vias is None or idx not in vias.near
            for d in (0, 1, -1, nx, -nx, nx + 1, nx - 1, -nx + 1, -nx - 1):
                for LL in (0, 1):
                    v = own[LL][idx + d]
                    if v != 0 and v != net:
                        ok = False
        if ok:
            ng = gc + cost
            if ng < g.get(t, INF):
                g[t] = ng
                prev[t] = s
                heapq.heappush(heap, (ng + h(idx), ng, t))
    return None


class Vias:
    """Where vias are: `at` maps a grid cell to the net of the via on it, `near`
    holds every cell too close to an existing via for a new one."""

    def __init__(self, grid):
        self.grid = grid
        self.at = {}
        self.near = set()

    def add(self, x, y, net):
        g = self.grid
        cx, cy = g.cell(x, y)
        self.at[cy * g.nx + cx] = net
        r = int(math.ceil((VIA_D + 0.3) / P))
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if (dx * dx + dy * dy) * P * P <= (VIA_D + 0.3) ** 2:
                    self.near.add((cy + dy) * g.nx + cx + dx)


def commit(board, grid, net, netinfo, path, width, vias=None):
    """Stamp the path's halo and add its tracks and vias to the board."""
    halo = width / 2 + CLEAR + SIGNAL_W / 2
    vhalo = VIA_D / 2 + CLEAR + SIGNAL_W / 2
    for (L, idx) in path:
        x, y = grid.xy(idx % grid.nx, idx // grid.nx)
        grid.disc((L,), x, y, halo, net)

    def pos(c):
        x, y = grid.xy(c[1] % grid.nx, c[1] // grid.nx)
        return pcbnew.VECTOR2I(mm(x), mm(y))

    def track(a, b):
        if a == b:
            return
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(pos(a))
        t.SetEnd(pos(b))
        t.SetWidth(mm(width))
        t.SetLayer(LAYERS[a[0]])
        t.SetNet(netinfo)
        board.Add(t)

    start, direction = path[0], None
    for a, b in zip(path, path[1:]):
        if b[0] != a[0]:                       # layer change: via at this cell
            track(start, a)
            if vias is not None and vias.at.get(a[1]) == net:
                start, direction = b, None     # an existing via of this net
                continue
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(pos(a))
            v.SetWidth(mm(VIA_D))
            v.SetDrill(mm(VIA_DRILL))
            v.SetNet(netinfo)
            board.Add(v)
            x, y = grid.xy(a[1] % grid.nx, a[1] // grid.nx)
            grid.disc((0, 1), x, y, vhalo, net)
            if vias is not None:
                vias.add(x, y, net)
            start, direction = b, None
            continue
        d = b[1] - a[1]
        if direction is not None and d != direction:
            track(start, a)
            start = a
        direction = d
    track(start, path[-1])


def pour_gnd(board, outline):
    """GND copper on both layers over the whole board, then fill."""
    for z in list(board.Zones()):
        board.Remove(z)
    gnd = board.FindNet("GND")
    for layer in LAYERS:
        z = pcbnew.ZONE(board)
        z.SetLayer(layer)
        z.SetNet(gnd)
        z.SetLocalClearance(mm(ZONE_CLEAR))
        z.SetMinThickness(mm(0.25))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_THT_THERMAL)   # SMD solid, THT spokes for hand soldering
        z.SetThermalReliefGap(mm(0.3))
        z.SetThermalReliefSpokeWidth(mm(0.4))
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        ol = z.Outline()
        ol.NewOutline()
        for x, y in outline:
            ol.Append(mm(x), mm(y))
        board.Add(z)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())


def route_first(board, only, lock=True):
    """Route just the named nets on a board that has no tracks yet (used to lay
    the LED supply before Freerouting does the rest). Returns failed connections."""
    outline = None
    for d in board.GetDrawings():
        if d.GetLayer() == pcbnew.Edge_Cuts and d.GetShape() == pcbnew.SHAPE_T_POLY:
            pts = d.GetPolyShape().Outline(0)
            outline = [(pcbnew.ToMM(pts.CPoint(i).x), pcbnew.ToMM(pts.CPoint(i).y))
                       for i in range(pts.PointCount())]
    grid = Grid(outline)
    names = sorted({p.GetNetname() for fp in board.GetFootprints() for p in fp.Pads() if p.GetNetname()})
    netcode = {n: i + 1 for i, n in enumerate(names)}
    edge_mask(board, grid, outline)
    pads = stamp_pads(board, grid, netcode)
    failed = []
    for name in only:
        net = netcode[name]
        info = board.FindNet(name)
        width = FIRST_WIDTH.get(name, POWER_W)
        group = pads[net]
        tree = set(group[0][1])
        joined = [group[0][0].GetPosition()]
        todo = list(group[1:])
        while todo:
            k = min(range(len(todo)), key=lambda i: min(abs(todo[i][0].GetPosition().x - q.x)
                                                        + abs(todo[i][0].GetPosition().y - q.y) for q in joined))
            pad, cells = todo.pop(k)
            joined.append(pad.GetPosition())
            if tree & set(cells):
                tree |= set(cells)
                continue
            c = pad.GetPosition()
            p = astar(grid, net, list(tree), set(cells), width > SIGNAL_W, grid.cell(pcbnew.ToMM(c.x), pcbnew.ToMM(c.y)))
            if p is None:
                failed.append((name, pad.GetParentFootprint().GetReference(), pad.GetNumber()))
                continue
            commit(board, grid, net, info, p, width)
            tree |= set(p) | set(cells)
    if lock:
        for t in board.GetTracks():
            t.SetLocked(True)
    return failed


def route(variant, attempts=4):
    """Route, and if anything fails, start over from the placed board with the
    failed nets first. Keeps the attempt with the fewest failures."""
    import shutil
    import tempfile
    path = ROOT / BOARDS[variant]
    with tempfile.TemporaryDirectory() as tmp:
        placed = Path(tmp) / path.name
        shutil.copy(path, placed)
        for pro in (".kicad_pro", ".kicad_dru"):
            if path.with_suffix(pro).exists():
                shutil.copy(path.with_suffix(pro), placed.with_suffix(pro))
        first, best = [], None
        for attempt in range(attempts):
            board, failed = route_once(placed, first)
            print(f"attempt {attempt + 1}: {len(failed)} connections failed", flush=True)
            if best is None or len(failed) < len(best[1]):
                best = (board, failed)
            if not failed:
                break
            first = list(dict.fromkeys(first + [f[0] for f in failed]))
    board, failed = best
    board.Save(str(path))
    print(f"saved best attempt: {len(failed)} connections failed before the GND pour")
    for f in failed:
        print("  unrouted:", *f)


def route_once(path, first):
    t0 = time.time()
    board = pcbnew.LoadBoard(str(path))
    # removing thousands of tracks upsets KiCad's Python bindings, so route a fresh board
    if len(board.GetTracks()):
        sys.exit(f"{path.name} already has tracks: run tools/gen_pcb.py {variant} first")
    outline = None
    for d in board.GetDrawings():
        if d.GetLayer() == pcbnew.Edge_Cuts and d.GetShape() == pcbnew.SHAPE_T_POLY:
            pts = d.GetPolyShape().Outline(0)
            outline = [(pcbnew.ToMM(pts.CPoint(i).x), pcbnew.ToMM(pts.CPoint(i).y))
                       for i in range(pts.PointCount())]
    grid = Grid(outline)
    names = sorted({p.GetNetname() for fp in board.GetFootprints() for p in fp.Pads() if p.GetNetname()})
    netcode = {n: i + 1 for i, n in enumerate(names)}
    edge_mask(board, grid, outline)
    pads = stamp_pads(board, grid, netcode)
    print(f"grid {grid.nx}x{grid.ny}, {len(pads)} nets, setup {time.time() - t0:.0f}s", flush=True)

    power = {n for n in names if board.FindNet(n).GetNetClassName() == "Power"}

    def span(net):
        cs = [p.GetPosition() for p, _ in pads[net]]
        xs = [pcbnew.ToMM(c.x) for c in cs]
        ys = [pcbnew.ToMM(c.y) for c in cs]
        return (max(xs) - min(xs)) + (max(ys) - min(ys))

    # power except GND first (wide tracks need the room), then signals shortest
    # first, then GND; GND copper pours on both layers pick up what's left
    # nets on the nice!nano go first so its pins keep their escape routes
    on_mcu = {n for n in pads if any(p.GetParentFootprint().GetReference() == "U1" for p, _ in pads[n])}

    def rank(n):
        name = names[n - 1]
        if name in first:
            return (-1, first.index(name))
        if name == "GND":
            return (3, span(n))
        if name in power:
            return (0, span(n))
        return (1 if n in on_mcu else 2, span(n))
    order = sorted((n for n in pads if len(pads[n]) > 1), key=rank)
    failed = []
    done = 0
    for net in order:
        name = names[net - 1]
        width = POWER_W if name in power else SIGNAL_W
        netinfo = board.FindNet(name)
        group = pads[net]
        tree = set(group[0][1])
        joined = [group[0][0].GetPosition()]
        todo = list(group[1:])
        while todo:
            # Prim: next is the pad closest to anything already joined
            k = min(range(len(todo)), key=lambda i: min(abs(todo[i][0].GetPosition().x - q.x)
                                                        + abs(todo[i][0].GetPosition().y - q.y)
                                                        for q in joined))
            todo[0], todo[k] = todo[k], todo[0]
            pad, cells = todo.pop(0)
            joined.append(pad.GetPosition())
            if not cells:
                failed.append((name, pad.GetParentFootprint().GetReference(), "no pad cell"))
                continue
            if tree & set(cells):
                tree |= set(cells)
                continue
            c = pad.GetPosition()
            goal = grid.cell(pcbnew.ToMM(c.x), pcbnew.ToMM(c.y))
            p = astar(grid, net, list(tree), set(cells), width > SIGNAL_W, goal)
            if p is None:
                failed.append((name, pad.GetParentFootprint().GetReference(), pad.GetNumber()))
                continue
            commit(board, grid, net, netinfo, p, width)
            tree |= set(p) | set(cells)
        done += 1
    pour_gnd(board, outline)
    print(f"  routed {len(order)} nets in {time.time() - t0:.0f}s", flush=True)
    return board, failed


if __name__ == "__main__":
    route(sys.argv[1] if len(sys.argv) > 1 else "mx")
