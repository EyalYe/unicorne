"""Wavy line pattern on both silkscreen layers of the routed PCBs.

Run with KiCad's bundled Python after tools/finish_route.py:

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 tools/waves.py

Flowing parallel lines that never cross, clipped to the bare board: they stay clear of
every courtyard, pad, existing silkscreen, cutout and the board edge. Vias are tented, so
the lines run over them. The lines live in a group named "waves" that is replaced on
every run, so this doesn't touch the routing.
"""
import math
import sys
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
BOARDS = {"mx": "unicorne.kicad_pcb", "choc": "unicorne-choc.kicad_pcb"}
GROUP = "waves"
PITCH = 1.6                  # line spacing
WIDTH = 0.2                  # JLCPCB's minimum silk line is 0.15
MARGIN = 0.4                 # clear of courtyards, pads, text and cut edges
STEP = 0.4                   # sampling along each wave
SIDES = ((pcbnew.F_SilkS, pcbnew.F_CrtYd, pcbnew.F_Cu), (pcbnew.B_SilkS, pcbnew.B_CrtYd, pcbnew.B_Cu))


def mm(v):
    return pcbnew.FromMM(v)


def wave_y(x, i):
    """Line i of the wave field at x. Neighbouring lines differ only by a small phase and
    amplitude change, so they never cross."""
    a = 3.2 + 0.9 * math.sin(i * 0.05)
    return (i * PITCH
            + a * math.sin(x / 14.0 + i * 0.07)
            + 1.4 * math.sin(x / 5.3 - i * 0.11 + 1.0)
            + 2.2 * math.sin(x / 31.0 + i * 0.03))


def simplify(pts, tol=0.02):
    """Douglas-Peucker, keeps segment count down without visible change."""
    if len(pts) < 3:
        return pts
    (ax, ay), (bx, by) = pts[0], pts[-1]
    dx, dy = bx - ax, by - ay
    d = math.hypot(dx, dy) or 1e-9
    far, idx = 0.0, 0
    for k, (px, py) in enumerate(pts[1:-1], 1):
        e = abs(dy * (px - ax) - dx * (py - ay)) / d
        if e > far:
            far, idx = e, k
    if far <= tol:
        return [pts[0], pts[-1]]
    return simplify(pts[:idx + 1], tol)[:-1] + simplify(pts[idx:], tol)


def box_poly(box, grow):
    p = pcbnew.SHAPE_POLY_SET()
    p.NewOutline()
    l, t, r, b = box.GetLeft() - grow, box.GetTop() - grow, box.GetRight() + grow, box.GetBottom() + grow
    for x, y in ((l, t), (r, t), (r, b), (l, b)):
        p.Append(x, y)
    return p


def clear_old(board):
    for g in list(board.Groups()):
        if g.GetName() == GROUP:
            for item in list(g.GetItems()):
                board.Remove(item)
            board.Remove(g)


def free_area(board, silk, crtyd, cu):
    """The bare board on one side, as a polygon set."""
    area = pcbnew.SHAPE_POLY_SET()
    board.GetBoardPolygonOutlines(area, False)
    area.Deflate(mm(MARGIN), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, mm(0.05))
    grow = mm(MARGIN)
    block = pcbnew.SHAPE_POLY_SET()
    for fp in board.GetFootprints():
        cy = fp.GetCourtyard(crtyd)
        if cy.OutlineCount():
            c = pcbnew.SHAPE_POLY_SET(cy)
            c.Inflate(grow, pcbnew.CORNER_STRATEGY_CHAMFER_ALL_CORNERS, mm(0.05))
            block.BooleanAdd(c)
        elif fp.GetSide() == (pcbnew.F_Cu if cu == pcbnew.F_Cu else pcbnew.B_Cu) and not fp.GetReference().startswith("MB"):
            block.BooleanAdd(box_poly(fp.GetBoundingBox(False), grow))
        for pad in fp.Pads():
            if pad.GetDrillSize().x or pad.IsOnLayer(cu):
                block.BooleanAdd(box_poly(pad.GetBoundingBox(), grow))
        for item in list(fp.GraphicalItems()) + [fp.Reference(), fp.Value()]:
            if item.GetLayer() == silk and (not hasattr(item, "IsVisible") or item.IsVisible()):
                block.BooleanAdd(box_poly(item.GetBoundingBox(), grow))
    for d in board.GetDrawings():
        if d.GetLayer() == silk:
            block.BooleanAdd(box_poly(d.GetBoundingBox(), grow))
    area.BooleanSubtract(block)
    return area


def draw(board, group, layer, area):
    box = area.BBox()
    x0, x1 = pcbnew.ToMM(box.GetLeft()), pcbnew.ToMM(box.GetRight())
    y0, y1 = pcbnew.ToMM(box.GetTop()), pcbnew.ToMM(box.GetBottom())

    def flush(run):
        for (ax, ay), (bx, by) in zip(run, run[1:]):
            seg = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
            seg.SetLayer(layer)
            seg.SetWidth(mm(WIDTH))
            seg.SetStart(pcbnew.VECTOR2I(mm(ax), mm(ay)))
            seg.SetEnd(pcbnew.VECTOR2I(mm(bx), mm(by)))
            board.Add(seg)
            group.AddItem(seg)

    for i in range(math.floor((y0 - 8) / PITCH) - 8, math.ceil((y1 + 8) / PITCH) + 8):
        run = []
        x = x0
        while x <= x1 + STEP:
            y = wave_y(x, i)
            if area.Contains(pcbnew.VECTOR2I(mm(x), mm(y))):
                run.append((x, y))
            elif run:
                flush(simplify(run))
                run = []
            x += STEP
        if run:
            flush(simplify(run))


def main(variants):
    for v in variants:
        path = ROOT / BOARDS[v]
        board = pcbnew.LoadBoard(str(path))
        clear_old(board)
        group = pcbnew.PCB_GROUP(board)
        group.SetName(GROUP)
        board.Add(group)
        for silk, crtyd, cu in SIDES:
            draw(board, group, silk, free_area(board, silk, crtyd, cu))
        board.Save(str(path))
        print(f"{path.name}: {len(group.GetItems())} wave segments")


if __name__ == "__main__":
    main(sys.argv[1:] or list(BOARDS))
