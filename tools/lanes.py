"""Structured routing for the key grid.

Every switch cell has its pads in the same place, so rows, columns and the
per-key LED connections run in fixed lanes, given per switch type in the
switch's frame (mm, x along the row, y down). Columns are the only top-layer
lanes and run straight down each column; rows, LED data and LED supply are
horizontal bottom-layer lanes that keep their spacing, so where the stagger
jogs between columns they stay parallel. Per-key LED GND goes through a via
into the top-layer pour. Tracks are locked; tools/finish_route.py does the rest.

MX (diode north-south east of the switch):
    rows y +2.45, LED data y +4.0, LED supply y +6.9, columns at x -7.08
Choc (diode east-west below-right, anode stub blocks mid-height lanes):
    rows y -8.0 (cathode reaches it by a short top-layer hop between the
    switch holes), LED data y +6.45, LED supply y +7.25, columns at x -3.5
"""
import math

import pcbnew

W = {"row": 0.25, "col": 0.25, "data": 0.2, "vdd": 0.3, "gnd": 0.3}
VIA_D, VIA_DRILL = 0.6, 0.3
GAP_X = 8.6                  # lanes jog between x = +8.6 here and -8.6 in the next cell

LAYOUTS = {
    "mx": dict(
        col_via=(-7.08, -0.6),           # under socket pad 1 (-7.08, -2.54)
        gnd_via=(-2.73, 3.15),           # above the LED's GND pad (-2.73, 4.0)
        # cathode to the row lane: (layer, points), the last point is on the lane
        row_stub=[("B", [(7.63, 2.45)])],
        row=(7.63, 2.45),                # where the row lane meets this cell's stub
        data_out=[(GAP_X, 4.0)],         # after DOUT (2.72, 4.0)
        data_y=4.0,
        data_in=[(-4.0, 4.0), (-4.0, 5.5)],  # then DIN (-2.73, 5.5)
        vdd_y=6.9,
        row_jog=(GAP_X, GAP_X),          # row lane jogs from x here to -x in the next cell
        row_jog_down=(GAP_X, GAP_X),     # ... when the next column sits lower
        lane_jog=GAP_X,                  # LED data / supply jog
        tabs=[(4.7, 9.0)],               # break-line tabs: (offset from row centre, width)
        keep_out=[(0.85, 8.5)],          # mounting holes stay out of this lane band
    ),
    "choc": dict(
        col_via=(-3.5, -4.1),            # below socket pad 1 (-3.5, -6.0)
        gnd_via=(-2.73, 3.15),
        # diode points down the key: cathode (6.5, 5.55) west to a via between it
        # and the LED data lane, top layer between the centre post, side post and
        # socket holes, back down onto the row lane
        row_stub=[("B", [(4.95, 5.55)]), ("via", (4.95, 5.55)),
                  ("F", [(4.95, 5.55), (4.95, 3.2), (3.55, 1.5), (3.55, 0.0), (2.6, -2.6), (2.6, -8.0)]),
                  ("via", (2.6, -8.0))],
        row=(2.6, -8.0),
        data_out=[(3.9, 4.0), (3.9, 6.3), (8.3, 6.3)],
        data_y=6.3,
        data_in=[(-3.75, 6.3), (-3.75, 5.5)],
        vdd_y=7.4,
        # the row lane stays above socket pad 2 and jogs in the next cell's
        # left side; data and supply jog over 2.4 mm so they stay apart
        row_jog=(8.0, 8.5),
        row_jog_down="top",              # drop on the top layer: the row-above supply lane
                                         # drops through the same spot on the bottom
        lane_jog=8.3,
        tabs=[(-8.0, 3.0), (7.4, 3.0)],
        keep_out=[(-9.6, -6.4), (4.7, 9.0)],
    ),
}


def mm(v):
    return pcbnew.FromMM(v)


class Cell:
    """One key: its switch, diode and LED, with a switch-frame transform."""

    def __init__(self, sw, d, led):
        self.sw, self.d, self.led = sw, d, led
        self.c = sw.GetPosition()
        self.a = math.radians(sw.GetOrientationDegrees())

    def at(self, lx, ly):
        """Board point (VECTOR2I) for a switch-frame position in mm."""
        a = self.a
        dx = lx * math.cos(a) + ly * math.sin(a)
        dy = -lx * math.sin(a) + ly * math.cos(a)
        return pcbnew.VECTOR2I(self.c.x + mm(dx), self.c.y + mm(dy))

    def local(self, p):
        """Switch-frame position (mm) of a board point."""
        a = self.a
        dx, dy = pcbnew.ToMM(p.x - self.c.x), pcbnew.ToMM(p.y - self.c.y)
        return dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)

    def along(self):
        """Position along this cell's row direction (for left-to-right order)."""
        return self.c.x * math.cos(self.a) - self.c.y * math.sin(self.a)

    def pad(self, fp, number):
        return next(p for p in fp.Pads() if p.GetNumber() == number)


def add_track(board, a, b, layer, width, net):
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(a)
    t.SetEnd(b)
    t.SetLayer(layer)
    t.SetWidth(mm(width))
    t.SetNet(net)
    t.SetLocked(True)
    board.Add(t)


def add_path(board, pts, layer, width, net):
    for a, b in zip(pts, pts[1:]):
        if a != b:
            add_track(board, a, b, layer, width, net)


def add_via(board, p, net):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(p)
    v.SetWidth(mm(VIA_D))
    v.SetDrill(mm(VIA_DRILL))
    v.SetNet(net)
    v.SetLocked(True)
    board.Add(v)


def cells(board):
    by_key = {}
    for fp in board.GetFootprints():
        key = fp.GetFieldText("Key") if fp.HasField("Key") else ""
        if key:
            by_key.setdefault(key, {})[fp.GetReference().rstrip("0123456789")] = fp
    out = {}
    for key, parts in by_key.items():
        side, name = key.split()
        if name.startswith("K") and {"SW", "D", "LED"} <= parts.keys():
            r, c = int(name[1]), int(name[2])
            out[(side, r, c)] = Cell(parts["SW"], parts["D"], parts["LED"])
    return out


def lay(board, variant="mx"):
    L = LAYOUTS[variant]
    cs = cells(board)
    layer = {"B": pcbnew.B_Cu, "F": pcbnew.F_Cu}
    before = len(board.GetTracks())
    for (side, r, c), k in cs.items():
        sw1, sw2 = k.pad(k.sw, "1"), k.pad(k.sw, "2")
        cat, an = k.pad(k.d, "1"), k.pad(k.d, "2")
        gnd, vdd = k.pad(k.led, "1"), k.pad(k.led, "3")
        # diode anode to switch pad 2
        add_path(board, [an.GetPosition(), sw2.GetPosition()], pcbnew.B_Cu, W["row"], an.GetNet())
        # cathode to the row lane
        prev = cat.GetPosition()
        for kind, pts in L["row_stub"]:
            if kind == "via":
                add_via(board, k.at(*pts), cat.GetNet())
                continue
            path = [k.at(*p) for p in pts]
            add_path(board, [prev] + path, layer[kind], W["row"], cat.GetNet())
            prev = path[-1]
        # column via
        add_path(board, [sw1.GetPosition(), k.at(*L["col_via"])], pcbnew.B_Cu, W["col"], sw1.GetNet())
        add_via(board, k.at(*L["col_via"]), sw1.GetNet())
        # LED GND into the top-layer pour, LED supply down to its lane
        add_path(board, [gnd.GetPosition(), k.at(*L["gnd_via"])], pcbnew.B_Cu, W["gnd"], gnd.GetNet())
        add_via(board, k.at(*L["gnd_via"]), gnd.GetNet())
        add_path(board, [vdd.GetPosition(), k.at(2.72, L["vdd_y"])], pcbnew.B_Cu, W["vdd"], vdd.GetNet())

    # columns: top layer, via to via down each column
    for (side, r, c), k in cs.items():
        below = cs.get((side, r + 1, c))
        if below and k.pad(k.sw, "1").GetNetname() == below.pad(below.sw, "1").GetNetname():
            add_path(board, [k.at(*L["col_via"]), below.at(*L["col_via"])], pcbnew.F_Cu, W["col"],
                     k.pad(k.sw, "1").GetNet())

    # rows, LED data and supply: left to right across each row
    rx, ry = L["row"]
    vy = L["vdd_y"]
    for side in "LR":
        for r in range(3):
            row = sorted((k for (s, rr, c), k in cs.items() if s == side and rr == r), key=lambda k: k.along())
            for k, nxt in zip(row, row[1:]):
                net = k.pad(k.d, "1").GetNet()
                if net.GetNetname() == nxt.pad(nxt.d, "1").GetNetname():
                    down = k.local(nxt.c)[1] > 0.5
                    jog = L["row_jog_down"] if down else L["row_jog"]
                    if jog == "top":
                        jo, ji = L["row_jog"]
                        add_path(board, [k.at(rx, ry), k.at(jo, ry)], pcbnew.B_Cu, W["row"], net)
                        add_via(board, k.at(jo, ry), net)
                        add_path(board, [k.at(jo, ry), nxt.at(-ji, ry)], pcbnew.F_Cu, W["row"], net)
                        add_via(board, nxt.at(-ji, ry), net)
                        add_path(board, [nxt.at(-ji, ry), nxt.at(rx, ry)], pcbnew.B_Cu, W["row"], net)
                    else:
                        jo, ji = jog
                        add_path(board, [k.at(rx, ry), k.at(jo, ry), nxt.at(-ji, ry), nxt.at(rx, ry)],
                                 pcbnew.B_Cu, W["row"], net)
                lj = L["lane_jog"]
                add_path(board, [k.at(2.72, vy), k.at(lj, vy), nxt.at(-lj, vy), nxt.at(2.72, vy)],
                         pcbnew.B_Cu, W["vdd"], k.pad(k.led, "3").GetNet())
                dout, din = k.pad(k.led, "4"), nxt.pad(nxt.led, "2")
                if dout.GetNetname() == din.GetNetname():
                    pts = ([dout.GetPosition()] + [k.at(*p) for p in L["data_out"]]
                           + [nxt.at(-lj, L["data_y"])] + [nxt.at(*p) for p in L["data_in"]]
                           + [din.GetPosition()])
                    add_path(board, pts, pcbnew.B_Cu, W["data"], dout.GetNet())
    return len(board.GetTracks()) - before
