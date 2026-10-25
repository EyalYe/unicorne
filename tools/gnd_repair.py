"""Find GND copper that is cut off from the main ground after the pours, and join it
with a via wherever such a group's pour overlaps the main ground on the other layer.

Connectivity is traced by real polygon overlap (pour islands, pads, tracks, vias), so
a pocket that is tied to a pad by its own via still counts as cut off.
"""
import pcbnew

import maze_route as M

LAYERS = (pcbnew.F_Cu, pcbnew.B_Cu)


def _poly(item, layer, grow=0):
    p = pcbnew.SHAPE_POLY_SET()
    item.TransformShapeToPolygon(p, layer, grow, pcbnew.FromMM(0.01), pcbnew.ERROR_INSIDE)
    return p


def _touch(a, b):
    c = pcbnew.SHAPE_POLY_SET(a)
    c.BooleanIntersection(b)
    return c.OutlineCount() > 0


def groups(board):
    """GND connectivity groups: lists of ("island", layer, poly) / ("item", item) nodes."""
    zones = {z.GetLayer(): z for z in board.Zones() if z.GetNetname() == "GND"}
    nodes = []
    for layer, z in zones.items():
        fill = z.GetFilledPolysList(layer)
        for i in range(fill.OutlineCount()):
            isl = pcbnew.SHAPE_POLY_SET()
            isl.AddOutline(fill.Outline(i))
            for h in range(fill.HoleCount(i)):
                isl.AddHole(fill.Hole(i, h))
            nodes.append(("island", layer, isl))
    items = [t for t in board.GetTracks() if t.GetNetname() == "GND"]
    items += [p for p in board.GetPads() if p.GetNetname() == "GND"]
    for it in items:
        layers = [L for L in LAYERS if it.IsOnLayer(L)]
        nodes.append(("item", it, {L: _poly(it, L, pcbnew.FromMM(0.02)) for L in layers}))
    parent = list(range(len(nodes)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def shapes(n):
        return {n[1]: n[2]} if n[0] == "island" else n[2]

    for i in range(len(nodes)):
        si = shapes(nodes[i])
        bi = {L: s.BBox() for L, s in si.items()}
        for j in range(i + 1, len(nodes)):
            if nodes[i][0] == "island" and nodes[j][0] == "island":
                continue                      # islands on one layer never touch, across layers only via items
            for L, s in shapes(nodes[j]).items():
                if L in si and bi[L].Intersects(s.BBox()) and _touch(si[L], s):
                    parent[find(i)] = find(j)
                    break
    out = {}
    for i, n in enumerate(nodes):
        out.setdefault(find(i), []).append(n)
    return sorted(out.values(), key=lambda g: -sum(n[2].Area() for n in g if n[0] == "island"))


def repair(board, step=0.25):
    """Join every cut-off GND group to the main one with a via where possible.
    Returns (vias added, groups still cut off)."""
    gs = groups(board)
    main = gs[0]
    main_isl = {L: [n[2] for n in main if n[0] == "island" and n[1] == L] for L in LAYERS}
    holes = [p for p in board.GetPads() if p.HasHole()]
    vias = [t for t in board.GetTracks() if isinstance(t, pcbnew.PCB_VIA)]
    tracks = [t for t in board.GetTracks() if not isinstance(t, pcbnew.PCB_VIA)]
    gnd = board.FindNet("GND")
    r = pcbnew.FromMM(M.VIA_D / 2 + 0.3)
    added, stuck = 0, 0
    for g in gs[1:]:
        done = False
        for n in g:
            if n[0] != "island" or done:
                continue
            other = pcbnew.F_Cu if n[1] == pcbnew.B_Cu else pcbnew.B_Cu
            for m in main_isl[other]:
                ov = pcbnew.SHAPE_POLY_SET(n[2])
                ov.BooleanIntersection(m)
                ov.Deflate(r, pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, pcbnew.FromMM(0.01))
                if ov.OutlineCount() == 0:
                    continue
                box = ov.BBox()
                y = box.GetTop()
                while not done and y <= box.GetBottom():
                    x = box.GetLeft()
                    while x <= box.GetRight():
                        pt = pcbnew.VECTOR2I(x, y)
                        if (ov.Contains(pt)
                                and not any(h.HitTest(pt, pcbnew.FromMM(M.VIA_D / 2 + 0.35)) for h in holes)
                                and not any(v.HitTest(pt, pcbnew.FromMM(M.VIA_D + 0.3)) for v in vias)
                                and not any(t.HitTest(pt, pcbnew.FromMM(M.VIA_D / 2 + 0.25)) for t in tracks)):
                            v = pcbnew.PCB_VIA(board)
                            v.SetPosition(pt)
                            v.SetWidth(pcbnew.FromMM(M.VIA_D))
                            v.SetDrill(pcbnew.FromMM(M.VIA_DRILL))
                            v.SetNet(gnd)
                            board.Add(v)
                            vias.append(v)
                            added += 1
                            done = True
                            break
                        x += pcbnew.FromMM(step)
                    y += pcbnew.FromMM(step)
                if done:
                    break
        stuck += not done
    if added:
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    return added, stuck


def describe(g):
    pads = [f"{n[1].GetParentFootprint().GetReference()}.{n[1].GetNumber()}" for n in g
            if n[0] == "item" and isinstance(n[1], pcbnew.PAD)]
    isl = [n for n in g if n[0] == "island"]
    pts = [n[2].BBox().GetCenter() for n in isl]
    where = (f"around x {pcbnew.ToMM(sum(p.x for p in pts) / len(pts)):.1f} y {pcbnew.ToMM(sum(p.y for p in pts) / len(pts)):.1f}"
             if pts else "no pour")
    return f"{len(isl)} pour islands, pads {pads}, {where}"
