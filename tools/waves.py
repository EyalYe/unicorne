"""Wavy line pattern on both silkscreen layers of the routed PCBs.

Run with KiCad's bundled Python after tools/finish_route.py:

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 tools/waves.py

Flowing parallel lines that never cross, clipped to the bare board: they stay clear of
every courtyard, pad, existing silkscreen, cutout and the board edge. Vias are tented, so
the lines run over them. The lines live in a group named "waves" that is replaced on
every run, so this doesn't touch the routing.
"""
import math
import re
import sys
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
BOARDS = {"mx": "unicorne.kicad_pcb", "choc": "unicorne-choc.kicad_pcb"}
GROUP = "waves"
PITCH = 1.6                  # line spacing
WIDTH = 0.2                  # JLCPCB's minimum silk line is 0.15
MARGIN = 0.6                 # clear of courtyards, pads, text and cut edges
CLOSE = 1.6                  # gaps narrower than twice this between parts are filled
MIN_RUN = 3.0                # drop pieces of line shorter than this
ROUND = pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS
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


def strip_old(path):
    """Drop a previous run's waves from the board file. Removing thousands of shapes through
    the Python bindings crashes, so this edits the file text before it is loaded."""
    text = path.read_text()
    start = text.find(f'\t(group "{GROUP}"\n')
    if start < 0:
        return
    end = text.index("\n\t)\n", start) + 4
    group = text[start:end]
    members = set(re.findall(r'"([0-9a-f-]{36})"', group[group.index("(members"):]))
    out, i = [], 0
    for m in re.finditer(r"\t\(gr_line\n.*?\n\t\)\n", text, re.S):
        if re.search(r'\(uuid "([0-9a-f-]{36})"\)', m.group()).group(1) in members:
            out.append(text[i:m.start()])
            i = m.end()
    out.append(text[i:])
    text = "".join(out)
    path.write_text(text.replace(group, "", 1))


def free_area(board, silk, crtyd, cu):
    """Where the waves go on one side, as a polygon set. On the top they run over
    everything (switches, the trackpad's space) and only clear exposed copper, holes,
    cutouts and text; on the bottom every part, pad and label gets a rounded halo, and halos closer than 2 * CLOSE merge, so the lines end in smooth
    curves around parts instead of steps and short dashes between them."""
    area = pcbnew.SHAPE_POLY_SET()
    board.GetBoardPolygonOutlines(area, False)
    area.Deflate(mm(MARGIN), ROUND, mm(0.05))
    block = pcbnew.SHAPE_POLY_SET()
    top = silk == pcbnew.F_SilkS
    for fp in board.GetFootprints():
        cy = fp.GetCourtyard(crtyd)
        if cy.OutlineCount() and not top:
            block.BooleanAdd(pcbnew.SHAPE_POLY_SET(cy))
        silk_items = [i for i in list(fp.GraphicalItems()) + [fp.Reference(), fp.Value()]
                      if i.GetLayer() == silk and (not hasattr(i, "IsVisible") or i.IsVisible())
                      and (not top or isinstance(i, pcbnew.PCB_TEXT))]
        if top:                           # the top waves run over parts: only text stays clear
            for i in silk_items:
                block.BooleanAdd(box_poly(i.GetBoundingBox(), 0))
        elif silk_items:                  # the whole outline the silkscreen draws, filled
            box = silk_items[0].GetBoundingBox()
            for i in silk_items[1:]:
                box.Merge(i.GetBoundingBox())
            block.BooleanAdd(box_poly(box, 0))
        for pad in fp.Pads():
            if pad.GetDrillSize().x or pad.IsOnLayer(cu):
                shape = pcbnew.SHAPE_POLY_SET()
                pad.TransformShapeToPolygon(shape, cu, 0, mm(0.02), pcbnew.ERROR_INSIDE)
                block.BooleanAdd(shape)
    for d in board.GetDrawings():
        if d.GetLayer() == silk and (not top or isinstance(d, pcbnew.PCB_TEXT)):
            block.BooleanAdd(box_poly(d.GetBoundingBox(), 0))
    block.Inflate(mm(MARGIN + CLOSE), ROUND, mm(0.05))      # halo, then close the gaps
    block.Deflate(mm(CLOSE), ROUND, mm(0.05))
    area.BooleanSubtract(block)
    return area


def draw(board, group, layer, area):
    box = area.BBox()
    x0, x1 = pcbnew.ToMM(box.GetLeft()), pcbnew.ToMM(box.GetRight())
    y0, y1 = pcbnew.ToMM(box.GetTop()), pcbnew.ToMM(box.GetBottom())

    def flush(run):
        if sum(math.hypot(bx - ax, by - ay) for (ax, ay), (bx, by) in zip(run, run[1:])) < MIN_RUN:
            return
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
        strip_old(path)
        board = pcbnew.LoadBoard(str(path))
        group = pcbnew.PCB_GROUP(board)
        group.SetName(GROUP)
        board.Add(group)
        for silk, crtyd, cu in SIDES:
            draw(board, group, silk, free_area(board, silk, crtyd, cu))
        board.Save(str(path))
        print(f"{path.name}: {len(group.GetItems())} wave segments")


if __name__ == "__main__":
    main(sys.argv[1:] or list(BOARDS))
