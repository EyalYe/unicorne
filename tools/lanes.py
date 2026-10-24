"""Structured routing for the key area of the MX board.

Every MX switch cell has its pads in the same place, so rows, columns and the
per-key LED connections run in fixed lanes (positions in the switch's frame,
mm, x along the row, y down):

    columns     top layer, vertical, through a via under each socket pad 1
    rows        bottom, y = +2.45, diode cathode to diode cathode
    LED data    bottom, y = +4.0, DOUT to the next LED's DIN (to its right)
    LED supply  bottom, y = +6.9, 0.3 mm
    LED GND     a via beside each GND pad into the top-layer GND pour

Only the top layer carries columns, so nothing crosses them; the bottom lanes
keep their spacing, so where the stagger jogs they stay parallel. Tracks are
locked; Freerouting does the rest (tools/route.py).
"""
import math

import pcbnew

ROW_Y, DATA_Y, VDD_Y = 2.45, 4.0, 6.9
GAP_X = 8.6                  # lanes jog between x = +8.6 here and -8.6 in the next cell
COL_VIA = (-7.08, -0.6)      # under socket pad 1 (-7.08, -2.54)
GND_VIA = (-2.73, 3.15)      # above the LED's GND pad (-2.73, 4.0)
DIN_TURN = -4.0              # data comes in left of the GND pad, then down to DIN
W = {"row": 0.25, "col": 0.25, "data": 0.2, "vdd": 0.3, "gnd": 0.3}
VIA_D, VIA_DRILL = 0.6, 0.3


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
    fps = list(board.GetFootprints())
    by_key = {}
    for fp in fps:
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


def lay(board):
    cs = cells(board)
    B, F = pcbnew.B_Cu, pcbnew.F_Cu
    n = {"tracks": 0}
    before = len(board.GetTracks())
    for (side, r, c), k in cs.items():
        sw1, sw2 = k.pad(k.sw, "1"), k.pad(k.sw, "2")
        cat, an = k.pad(k.d, "1"), k.pad(k.d, "2")
        gnd, vdd = k.pad(k.led, "1"), k.pad(k.led, "3")
        # diode anode to switch, cathode down to the row lane
        add_path(board, [an.GetPosition(), sw2.GetPosition()], B, W["row"], an.GetNet())
        add_path(board, [cat.GetPosition(), k.at(7.63, ROW_Y)], B, W["row"], cat.GetNet())
        # column via under socket pad 1
        add_path(board, [sw1.GetPosition(), k.at(*COL_VIA)], B, W["col"], sw1.GetNet())
        add_via(board, k.at(*COL_VIA), sw1.GetNet())
        # LED GND into the top-layer pour, LED supply down to its lane
        add_path(board, [gnd.GetPosition(), k.at(*GND_VIA)], B, W["gnd"], gnd.GetNet())
        add_via(board, k.at(*GND_VIA), gnd.GetNet())
        add_path(board, [vdd.GetPosition(), k.at(2.72, VDD_Y)], B, W["vdd"], vdd.GetNet())

    # columns: top layer, via to via down each column
    for (side, r, c), k in cs.items():
        below = cs.get((side, r + 1, c))
        if below and k.pad(k.sw, "1").GetNetname() == below.pad(below.sw, "1").GetNetname():
            add_path(board, [k.at(*COL_VIA), below.at(*COL_VIA)], F, W["col"], k.pad(k.sw, "1").GetNet())

    # rows, LED data and supply: left to right across each row
    for side in "LR":
        for r in range(3):
            row = sorted((k for (s, rr, c), k in cs.items() if s == side and rr == r), key=lambda k: k.along())
            for k, nxt in zip(row, row[1:]):
                net = k.pad(k.d, "1").GetNet()
                if net.GetNetname() == nxt.pad(nxt.d, "1").GetNetname():
                    add_path(board, [k.at(7.63, ROW_Y), k.at(GAP_X, ROW_Y), nxt.at(-GAP_X, ROW_Y),
                                     nxt.at(7.63, ROW_Y)], B, W["row"], net)
                vnet = k.pad(k.led, "3").GetNet()
                add_path(board, [k.at(2.72, VDD_Y), k.at(GAP_X, VDD_Y), nxt.at(-GAP_X, VDD_Y),
                                 nxt.at(2.72, VDD_Y)], B, W["vdd"], vnet)
                dout, din = k.pad(k.led, "4"), nxt.pad(nxt.led, "2")
                if dout.GetNetname() == din.GetNetname():
                    add_path(board, [dout.GetPosition(), k.at(GAP_X, DATA_Y), nxt.at(-GAP_X, DATA_Y),
                                     nxt.at(DIN_TURN, DATA_Y), nxt.at(DIN_TURN, 5.5), din.GetPosition()],
                             B, W["data"], dout.GetNet())
    return len(board.GetTracks()) - before
