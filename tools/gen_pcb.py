"""Build unicorne.kicad_pcb from the schematic and mock/layout.json.

Run with KiCad's bundled Python:

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 tools/gen_pcb.py [mx|choc]

Places every footprint, links it to its schematic symbol (so Update PCB from
Schematic keeps working), draws the outline, the 36-key break lines with mouse
bites and the trackpad outline. Tracks are not routed. Regenerating overwrites
the board file.
"""
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_schematic import NANO_PINS, child, libsym, parse  # noqa: E402
from layout import rot  # noqa: E402
import lanes  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
KICAD = Path("/Applications/KiCad/KiCad.app")
CLI = KICAD / "Contents/MacOS/kicad-cli"
FP_DIR = KICAD / "Contents/SharedSupport/footprints"

# Per switch type: footprints, diode (x, y, rotation) in the switch frame, the
# break line's distance from the pinky column on the (left, right) half, and the
# underglow spots as (row, col, y offset, x offset) in a switch frame (None =
# mock/layout.json). Switch footprints are not mirrored on the right half, so
# offsets in the switch frame are the same on both halves.
VARIANTS = {
    "mx": dict(board="unicorne.kicad_pcb", sw="SW_Hotswap_Kailh_MX_1.00u",
               sw15="SW_Hotswap_Kailh_MX_1.50u", diode=(7.63, 0.0, 90), break_at=(9.5, 9.5),
               glow=None, hole_body=None),
    # Choc sockets reach 9.8 mm east and further north than MX: the diode moves
    # south-east, the break line away from whichever socket faces it, and the
    # underglow west of the sockets (column 1 spots move to column 2, clear of
    # the left break line).
    "choc": dict(board="unicorne-choc.kicad_pcb", sw="SW_Hotswap_Kailh_Choc_V1V2_1.00u",
                 sw15="SW_Hotswap_Kailh_Choc_V1V2_1.50u", diode=(6.5, 3.9, 90), break_at=(10.6, 8.4),
                 glow=[(0, 2, 9.2, -5.5), (0, 3, 9.2, -5.5), (1, 5, -9.8, -5.5),
                       (1, 2, 9.2, -5.5), (2, 3, 9.2, -5.5), (2, 5, 9.2, -5.5)],
                 # The Choc footprint courtyard is the top housing (above the plate);
                 # at PCB level the body is 13.8 mm, so holes between keys only need
                 # to clear that plus a standoff (as on Corne Chocolate)
                 hole_body=6.9),
}
LED_OFFSET = 4.75            # per-key LED, south of the switch centre (Corne v3)
NO_BOM = {"J1", "J2", "J5", "J6", "JP1", "JP2", "JP3"}  # bare pads and the solder jumpers
NANO_USB_EDGE = 17.78        # nice_nano footprint origin to its PCB edge at the USB end
# The nice!nano's USB-C is mid-mount: its shell drops 0.7 mm below the nano's
# underside, into this board, from past the edge to 11.6 mm from the nano's
# centre and 4.3 mm either side. Notch the edge for it (0.3 mm clearance), with
# inner corners a fab's router bit can cut.
USB_NOTCH = dict(half_w=4.7, inner=11.9, radius=0.8)
TRACKPAD = (43.3, 40.3)      # TPS43 outline, datasheet figure 2.1
SLOT_W = 1.0                 # break-line slot width
BRIDGE = 2.0                 # solid bridge at each board edge
BITE_D, BITE_PITCH = 0.5, 0.8
MIN_SLOT = 3.0               # shorter gaps between tabs are left solid (no sliver slots)


def mm(v):
    return pcbnew.FromMM(v)


def vec(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))


class Board:
    def __init__(self, layout):
        self.board = pcbnew.CreateEmptyBoard()
        x0, y0 = layout["bounds"][:2]
        self.ox, self.oy = round(30 - x0), round(30 - y0)
        self.nets = {}

    def xy(self, x, y):
        return vec(x + self.ox, y + self.oy)

    def net(self, name):
        if name not in self.nets:
            ni = pcbnew.NETINFO_ITEM(self.board, name)
            self.board.Add(ni)
            self.nets[name] = ni
        return self.nets[name]


def load_netlist():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "unicorne.net"
        subprocess.run([str(CLI), "sch", "export", "netlist", "--format", "kicadsexpr",
                        "-o", str(out), str(ROOT / "unicorne.kicad_sch")],
                       check=True, capture_output=True)
        tree = parse(out.read_text())
    comps = {}
    for c in child(tree, "components")[1:]:
        ref = child(c, "ref")[1]
        fields = {}
        for prop in c:
            if isinstance(prop, list) and prop[0] == "property":
                name, value = child(prop, "name"), child(prop, "value")
                if name and value and name[1] not in ("Sheetname", "Sheetfile", "ki_keywords",
                                                      "ki_fp_filters", "ki_description"):
                    fields[name[1]] = value[1]
        sheet = child(child(c, "sheetpath"), "tstamps")[1]
        comps[ref] = dict(ref=ref, value=child(c, "value")[1], footprint=child(c, "footprint")[1],
                          path=sheet + child(c, "tstamps")[1], key=fields.get("Key", ""), fields=fields)
    pins = {}
    for n in child(tree, "nets")[1:]:
        name = child(n, "name")[1]
        for node in n:
            if isinstance(node, list) and node[0] == "node":
                pins[(child(node, "ref")[1], child(node, "pin")[1])] = name
    return comps, pins


def load_footprint(fpid):
    lib, name = fpid.split(":")
    path = ROOT / "lib" / f"{lib}.pretty" if lib == "unicorne" else FP_DIR / f"{lib}.pretty"
    fp = pcbnew.FootprintLoad(str(path), name)
    assert fp, f"cannot load {fpid}"
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    return fp


def add_part(b, comp, pins, fpid=None):
    fp = load_footprint(fpid or comp["footprint"])
    fp.SetReference(comp["ref"])
    fp.SetValue(comp["value"])
    fp.SetPath(pcbnew.KIID_PATH(comp["path"]))
    for name, value in comp["fields"].items():
        fp.SetField(name, value)
        fp.GetField(name).SetVisible(False)
    if comp["ref"] in NO_BOM:
        fp.SetAttributes(fp.GetAttributes() | pcbnew.FP_EXCLUDE_FROM_BOM)
        fp.Models().clear()                  # bare pads: nothing is fitted there
    b.board.Add(fp)
    for pad in fp.Pads():
        net = pins.get((comp["ref"], pad.GetNumber()))
        if net:
            pad.SetNet(b.net(net))
    return fp


def place(b, fp, x, y, orient, bottom=False):
    """orient is KiCad degrees (counter-clockwise); bottom parts are mirrored first."""
    if bottom and not fp.IsFlipped():
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    fp.SetOrientationDegrees(orient)
    fp.SetPosition(b.xy(x, y))


def place_center(b, fp, x, y, orient, bottom=True):
    """Place so the footprint's pads and body are centred on (x, y)."""
    place(b, fp, x, y, orient, bottom)
    box = fp.GetBoundingBox(False)
    c = box.GetCenter()
    fp.Move(pcbnew.VECTOR2I(b.xy(x, y).x - c.x, b.xy(x, y).y - c.y))


# Silkscreen names for every exposed pad, by reference and pad number
PAD_LABELS = {
    "J1": ("BATT", {"1": "B+", "2": "B-"}),
    "J2": ("TPS43", {"1": "SDA", "2": "SCL", "3": "3V3", "4": "GND", "5": "RST", "6": "RDY"}),
    "J5": ("SENSOR", {"1": "3V3", "2": "GND", "3": "SCK", "4": "MOSI", "5": "MISO", "6": "CS",
                      "7": "MOT", "8": "RST"}),
    "J3": ("UART/I2C", {"1": "3V3", "2": "GND", "3": "RX", "4": "TX", "5": "SDA", "6": "SCL"}),
    "J4": ("GPIO", {"1": "GND", "2": "0.24", "3": "1.00", "4": "0.11"}),
    "J6": ("LED IN", {"1": "V+", "2": "GND"}),
}
SILK = 0.8                    # label text height


def silk_text(b, text, pos, angle=0, layers=(pcbnew.F_SilkS, pcbnew.B_SilkS)):
    """Text on the given silkscreens (both by default, for through-hole pads); the
    bottom copy is mirrored to read from below."""
    for layer in layers:
        t = pcbnew.PCB_TEXT(b.board)
        t.SetText(text)
        t.SetLayer(layer)
        t.SetTextSize(pcbnew.VECTOR2I(mm(SILK), mm(SILK)))
        t.SetTextThickness(mm(0.15))
        t.SetTextAngleDegrees(angle)
        t.SetMirrored(layer == pcbnew.B_SilkS)
        t.SetPosition(pos)
        b.board.Add(t)


def label_pads(b, fp, side):
    """Name each pad of a 1-row pad group. `side` is +1/-1: which side of the row the
    labels go (rows of vertical pads: +x/-x; rows of horizontal pads: +y/-y)."""
    title, names = PAD_LABELS[fp.GetReference()]
    fp.Reference().SetVisible(False)      # the group title names it instead
    pads = sorted(fp.Pads(), key=lambda p: int(p.GetNumber()))
    p0, p1 = pads[0].GetPosition(), pads[-1].GetPosition()
    horizontal = abs(p1.x - p0.x) > abs(p1.y - p0.y)
    longest = max(len(n) for n in names.values())
    for pad in pads:
        name = names[pad.GetNumber()]
        c = pad.GetPosition()
        if horizontal:
            # vertical text when names are too wide for the 2.54 mm pitch
            angle = 90 if longest > 3 else 0
            # start just outside the 1.4 mm header outline
            reach = (1.8 + len(name) * SILK * 0.4) if angle else 1.8 + SILK / 2
            silk_text(b, name, pcbnew.VECTOR2I(c.x, c.y + mm(side * reach)), angle)
        else:
            reach = 1.8 + len(name) * SILK * 0.4
            silk_text(b, name, pcbnew.VECTOR2I(c.x + mm(side * reach), c.y))
    # group title beyond pad 1, along the row
    ux, uy = (p0.x - p1.x), (p0.y - p1.y)
    n = math.hypot(ux, uy)
    off = mm(2.0 + len(title) * SILK * 0.4) if horizontal else mm(2.4)
    silk_text(b, title, pcbnew.VECTOR2I(int(p0.x + ux / n * off), int(p0.y + uy / n * off)))


# What each nice!nano net means, for the pin table on the bottom silkscreen
PIN_USE = {
    "P0.06": "TX / LED data", "P0.08": "RX", "SDA_MOSI": "SDA / sens MOSI",
    "SCL_SCK": "SCL / sens SCK", "RDY_MOTION": "TP RDY / sens MOT", "P0.24_MISO": "GPIO / sens MISO",
    "LED_EN": "LED power on", "P0.11_CS": "GPIO / sens CS", "+3V3": "3V3 out",
    "RST": "reset button", "VBAT": "battery (switch)", "GND": "GND",
}
TABLE_SIZE, TABLE_PITCH = 0.8, 1.25


def pin_table(b, top, bottom):
    """nice!nano pin table on the bottom silkscreen, between y=top and y=bottom
    (layout coordinates), laid out as read from below (mirrored)."""
    sym = libsym("unicorne:nice_nano_v2")
    rows = []
    for num, net in sorted(NANO_PINS.items()):
        if net == "GND" and rows and rows[-1][1] == "GND":
            continue
        pin = sym.pins[str(num)][3].split("/")[0]
        rows.append((pin if net != "GND" else "GND", PIN_USE.get(net, net), num))
    half = (len(rows) + 1) // 2
    height = 2.6 + half * TABLE_PITCH
    assert height <= bottom - top, f"pin table needs {height:.1f} mm, has {bottom - top:.1f}"
    v0 = top + (bottom - top - height) / 2

    def text(s_, u, v, size=TABLE_SIZE, centre=False):
        t = pcbnew.PCB_TEXT(b.board)
        t.SetText(s_)
        t.SetLayer(pcbnew.B_SilkS)
        t.SetMirrored(True)
        t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size)))
        t.SetTextThickness(mm(0.15))
        t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_CENTER if centre else pcbnew.GR_TEXT_H_ALIGN_LEFT)
        t.SetPosition(b.xy(-u, v))            # u runs left to right as seen from below
        b.board.Add(t)

    text("nice!nano pins", 0, v0 + 0.5, 1.0, centre=True)
    for col, chunk in enumerate((rows[:half], rows[half:])):
        u = -19.5 if col == 0 else 0.5
        for i, (pin, use, _) in enumerate(chunk):
            v = v0 + 2.6 + i * TABLE_PITCH
            text(pin, u, v)
            text(use, u + 5.0, v)
    w, h = 40.0, height + 0.6
    corners = [(-20.0, v0 - 0.4), (20.0, v0 - 0.4), (20.0, v0 - 0.4 + h), (-20.0, v0 - 0.4 + h)]
    for a_, c_ in zip(corners, corners[1:] + corners[:1]):
        line(b, a_, c_, pcbnew.B_SilkS, 0.15)
    line(b, (-20.0, v0 + 1.7), (20.0, v0 + 1.7), pcbnew.B_SilkS, 0.15)


def pad_xy(fp, number):
    p = next(p for p in fp.Pads() if p.GetNumber() == number).GetPosition()
    return pcbnew.ToMM(p.x), pcbnew.ToMM(p.y)


def shape(b, kind, layer, width=0.1):
    s = pcbnew.PCB_SHAPE(b.board)
    s.SetShape(kind)
    s.SetLayer(layer)
    s.SetWidth(mm(width))
    b.board.Add(s)
    return s


def polygon(b, pts, layer, width=0.1):
    s = shape(b, pcbnew.SHAPE_T_POLY, layer, width)
    s.SetPolyPoints([b.xy(x, y) for x, y in pts])
    s.SetFilled(False)
    return s


def line(b, a, c, layer, width=0.1):
    s = shape(b, pcbnew.SHAPE_T_SEGMENT, layer, width)
    s.SetStart(b.xy(*a))
    s.SetEnd(b.xy(*c))


def arc(b, start, mid, end, layer, width=0.1):
    s = shape(b, pcbnew.SHAPE_T_ARC, layer, width)
    s.SetArcGeometry(b.xy(*start), b.xy(*mid), b.xy(*end))


HOLE_FP = "MountingHole:MountingHole_2.2mm_M2_ISO7380"   # M2 button head, 3.5 mm
HOLE_KEEPOUT = 2.04          # its courtyard radius
HOLE_TO_COPPER = 1.1 + 0.25  # drill radius + hole clearance
HOLE_TO_EDGE = HOLE_KEEPOUT + 0.5   # the whole screw head stays on the board


def inside(poly, x, y):
    """Even-odd point-in-polygon test."""
    hit = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def usb_notch(outline, nano_y):
    """The outline with a notch at the top edge for the nice!nano's USB shell."""
    n = USB_NOTCH
    poly = pcbnew.SHAPE_POLY_SET()
    poly.NewOutline()
    for x, y in outline:
        poly.Append(mm(x), mm(y))
    cut = pcbnew.SHAPE_POLY_SET()
    cut.NewOutline()
    y_in = nano_y - n["inner"]
    for x, y in ((-n["half_w"], y_in - 40), (n["half_w"], y_in - 40), (n["half_w"], y_in), (-n["half_w"], y_in)):
        cut.Append(mm(x), mm(y))
    poly.BooleanSubtract(cut)
    poly = poly.Fillet(mm(n["radius"]), mm(0.01))
    o = poly.Outline(0)
    return [(pcbnew.ToMM(o.CPoint(i).x), pcbnew.ToMM(o.CPoint(i).y)) for i in range(o.PointCount())]


def clear_spot(b, x, y, outline, reach=10.0, step=0.25, body=None, keep_out=()):
    """Nearest point to (x, y) where an M2 hole clears every courtyard, pad and edge.
    With `body`, key switches are checked as a square of that half-width (their
    body at PCB level) instead of their courtyard."""
    edges = [d for d in b.board.GetDrawings() if d.GetLayer() == pcbnew.Edge_Cuts]
    fps = list(b.board.GetFootprints())
    for fp in fps:
        edges += [g for g in fp.GraphicalItems() if g.GetLayer() == pcbnew.Edge_Cuts]
    is_key = lambda fp: fp.GetReference().startswith("SW") and fp.HasField("Key")
    all_keys = [fp for fp in fps if is_key(fp)]
    keys = [fp for fp in fps if is_key(fp)] if body else []
    yards = [fp.GetCourtyard(layer) for fp in fps for layer in (pcbnew.F_CrtYd, pcbnew.B_CrtYd)
             if not (body and layer == pcbnew.F_CrtYd and is_key(fp))]
    pads = [p for fp in fps for p in fp.Pads()]

    def ok(px, py):
        if not inside(outline, px, py):
            return False
        pt = b.xy(px, py)
        if any(e.HitTest(pt, mm(HOLE_TO_EDGE)) for e in edges):
            return False
        if any(y_.OutlineCount() and y_.Collide(pt, mm(HOLE_KEEPOUT)) for y_ in yards):
            return False
        # stay off the routing lanes (tools/lanes.py: y = +2.45 .. +6.9 in each switch
        # frame, jogging between columns), plus the hole's own keep-out
        for k in all_keys:
            c = k.GetPosition()
            a = math.radians(k.GetOrientationDegrees())
            dx, dy = pcbnew.ToMM(pt.x - c.x), pcbnew.ToMM(pt.y - c.y)
            lx, ly = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
            if abs(lx) < 10.5 and any(lo < ly < hi for lo, hi in keep_out):
                return False
        for k in keys:
            c = k.GetPosition()
            a = math.radians(k.GetOrientationDegrees())
            dx, dy = pcbnew.ToMM(pt.x - c.x), pcbnew.ToMM(pt.y - c.y)
            lx, ly = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
            if abs(lx) < body + HOLE_KEEPOUT and abs(ly) < body + HOLE_KEEPOUT:
                return False
        return not any(p.HitTest(pt, mm(HOLE_TO_COPPER)) for p in pads)

    n = int(reach / step)
    spots = sorted(((i * step) ** 2 + (j * step) ** 2, i, j)
                   for i in range(-n, n + 1) for j in range(-n, n + 1))
    for d2, i, j in spots:
        if d2 > reach ** 2:
            break
        if ok(x + i * step, y + j * step):
            return x + i * step, y + j * step, math.sqrt(d2)
    raise RuntimeError(f"no room for a mounting hole near ({x:.1f}, {y:.1f})")


def segment_hits(a, c, poly):
    """Parameters t along a->c where the segment crosses the polygon."""
    ts = []
    for i in range(len(poly)):
        p, q = poly[i - 1], poly[i]
        d = (c[0] - a[0]) * (q[1] - p[1]) - (c[1] - a[1]) * (q[0] - p[0])
        if abs(d) < 1e-12:
            continue
        t = ((p[0] - a[0]) * (q[1] - p[1]) - (p[1] - a[1]) * (q[0] - p[0])) / d
        u = ((p[0] - a[0]) * (c[1] - a[1]) - (p[1] - a[1]) * (c[0] - a[0])) / d
        if 0 <= t <= 1 and 0 <= u <= 1:
            ts.append(t)
    return sorted(ts)


def break_line(b, ends, outline, row_points, index, tab_spec):
    """Slots along the break line, leaving a bridge at each edge and a tab at each
    key row where tracks cross. Mouse bites at both sides of every tab and bridge."""
    a, c = ends
    ts = segment_hits(a, c, outline)
    t0, t1 = ts[0], ts[-1]
    length = math.dist(a, c)
    ux, uy = (c[0] - a[0]) / length, (c[1] - a[1]) / length
    nx, ny = -uy, ux
    at = lambda s: (a[0] + ux * s, a[1] + uy * s)
    s0, s1 = t0 * length, t1 * length
    # a tab wherever routing lanes cross (tools/lanes.py: offset from each row
    # centre along the line, and width)
    centres = sorted((p[0] - a[0]) * ux + (p[1] - a[1]) * uy for p in row_points)
    tabs = sorted((c + off, w) for c in centres for off, w in tab_spec)
    solid = sorted([(s0, s0 + BRIDGE)] + [(t - w / 2, t + w / 2) for t, w in tabs] + [(s1 - BRIDGE, s1)])
    # merge solid sections whose slot between them would be a sliver
    merged = [solid[0]]
    for s_, e_ in solid[1:]:
        if s_ - merged[-1][1] < MIN_SLOT:
            merged[-1] = (merged[-1][0], max(e_, merged[-1][1]))
        else:
            merged.append((s_, e_))
    solid = merged
    r = SLOT_W / 2
    for (_, e), (n, _) in zip(solid, solid[1:]):
        p, q = at(e + r), at(n - r)
        line(b, (p[0] + nx * r, p[1] + ny * r), (q[0] + nx * r, q[1] + ny * r), pcbnew.Edge_Cuts)
        line(b, (p[0] - nx * r, p[1] - ny * r), (q[0] - nx * r, q[1] - ny * r), pcbnew.Edge_Cuts)
        arc(b, (p[0] + nx * r, p[1] + ny * r), (p[0] - ux * r, p[1] - uy * r),
            (p[0] - nx * r, p[1] - ny * r), pcbnew.Edge_Cuts)
        arc(b, (q[0] + nx * r, q[1] + ny * r), (q[0] + ux * r, q[1] + uy * r),
            (q[0] - nx * r, q[1] - ny * r), pcbnew.Edge_Cuts)
    # mouse bites: a row of holes running into each solid section from both slot ends
    fp = pcbnew.FOOTPRINT(b.board)
    fp.SetReference(f"MB{index}")
    fp.Reference().SetVisible(False)
    fp.SetValue("mouse bites")
    fp.Value().SetVisible(False)
    fp.SetAttributes(pcbnew.FP_EXCLUDE_FROM_BOM | pcbnew.FP_EXCLUDE_FROM_POS_FILES | pcbnew.FP_BOARD_ONLY)
    b.board.Add(fp)
    # perforate every solid section along its whole length, except where the
    # routing lanes cross each tab (tools/lanes.py: tab centre -2.25 .. +2.2)
    keep_clear = [(t - w / 2 + 1.7, t + w / 2 - 1.7) if w > 5 else (t - 1.2, t + 1.2) for t, w in tabs]
    holes = []
    for s_, e_ in solid:
        h = s_ + 0.5
        while h <= e_ - 0.5:
            if not any(lo < h < hi for lo, hi in keep_clear):
                holes.append(h)
            h += BITE_PITCH
    for s in holes:
        pad = pcbnew.PAD(fp)
        pad.SetAttribute(pcbnew.PAD_ATTRIB_NPTH)
        pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
        pad.SetSize(pcbnew.VECTOR2I(mm(BITE_D), mm(BITE_D)))
        pad.SetDrillSize(pcbnew.VECTOR2I(mm(BITE_D), mm(BITE_D)))
        pad.SetLayerSet(pad.UnplatedHoleMask())
        fp.Add(pad)
        pad.SetPosition(b.xy(*at(s)))


def build(variant):
    v = VARIANTS[variant]
    layout = json.loads((ROOT / "mock" / "layout.json").read_text())
    comps, pins = load_netlist()
    b = Board(layout)
    tb = b.board.GetTitleBlock()
    tb.SetTitle(f"unicorne ({variant.upper()} hotswap)")
    tb.SetRevision("0.1")
    tb.SetDate("2026-10-24")

    keys = {f'{k["side"]} {k["name"]}': k for k in layout["keys"]}
    if v["glow"] is None:
        glow = {f'{side} UG{i}': g for side, off in (("L", 0), ("R", 6))
                for i, g in enumerate(layout["underglow"][off:off + 6])}
    else:
        glow = {}
        for side in "LR":
            for i, (r, c, oy, ox) in enumerate(v["glow"]):
                k = keys[f"{side} K{r}{c}"]
                dx, dy = rot(ox, oy, k["rot"])
                glow[f"{side} UG{i}"] = dict(x=k["x"] + dx, y=k["y"] + dy, rot=k["rot"])

    for ref, comp in comps.items():
        kind = "".join(ch for ch in ref if ch.isalpha())
        if kind == "SW" and comp["key"]:
            k = keys[comp["key"]]
            fp = add_part(b, comp, pins, f'unicorne:{v["sw15"] if k["w"] > 1 else v["sw"]}')
            place(b, fp, k["x"], k["y"], -k["rot"])
        elif kind == "D" and comp["key"]:
            k = keys[comp["key"]]
            fp = add_part(b, comp, pins)
            ox, oy, orient = v["diode"]
            dx, dy = rot(ox, oy, k["rot"])
            place(b, fp, k["x"] + dx, k["y"] + dy, -k["rot"] + orient, bottom=True)
        elif kind == "LED" and comp["key"] in keys:
            k = keys[comp["key"]]
            fp = add_part(b, comp, pins)
            dx, dy = rot(0, LED_OFFSET, k["rot"])
            place(b, fp, k["x"] + dx, k["y"] + dy, -k["rot"], bottom=True)
        elif kind == "LED":
            g = glow[comp["key"]]
            fp = add_part(b, comp, pins)
            place(b, fp, g["x"], g["y"], -g["rot"] + 180, bottom=True)
        else:
            continue
        fp.Reference().SetVisible(False)

    # --- centre parts, all on the bottom except the battery and trackpad pads
    nano, pad = layout["nano"], layout["trackpad"]
    ny, py = nano["y"], pad["y"]

    def part(ref, x, y, orient=0, bottom=True):
        fp = add_part(b, comps[ref], pins)
        place_center(b, fp, x, y, orient, bottom)
        return fp

    u1 = add_part(b, comps["U1"], pins)
    # on the top, components up: pin 1 (P0.06, USB end) is top left seen from above
    for orient in (0, 90, 180, 270):
        place(b, u1, 0, 0, orient, bottom=False)
        x1, y1 = pad_xy(u1, "1")
        cx, cy = (pcbnew.ToMM(v_) for v_ in (u1.GetPosition().x, u1.GetPosition().y))
        if x1 < cx and y1 < cy:
            break
    # The footprint draws the nice!nano's PCB edge 17.78 mm from its origin at the USB
    # end (the receptacle overhangs a further 1.6 mm): put that edge flush with the board edge
    place(b, u1, 0, ny + NANO_USB_EDGE, u1.GetOrientationDegrees(), bottom=False)

    sw43 = add_part(b, comps["SW43"], pins)            # power switch, lever over the top edge
    place(b, sw43, 15.0, ny + 2.0, 0, bottom=True)
    # Right of the nice!nano: only the power switch and reset (RAW and RST are on
    # this side), leaving its other edge free for the rows and columns.
    part("SW44", 15.5, ny + 9.5, 90, bottom=False)     # reset, 3x6 THT on top beside the nano
    # Left of it: both headers and the LED parts, whose pins are all on this side.
    # SMD parts stay on the bottom.
    part("J3", -13.5, ny + 10.0, 180)                  # expansion header (6 pins)
    part("J4", -13.5, ny + 25.0, 180)                  # GPIO header (4 pins)
    part("JP1", -17.5, ny + 5.0, 90)                   # LED data jumper, by P0.06
    part("Q2", -18.0, ny + 14.0)
    part("R2", -18.0, ny + 18.0, 90)
    part("Q1", -18.0, ny + 22.0, 0)                    # source pin faces JP2
    part("R1", -18.0, ny + 26.0, 90)
    part("C1", -18.0, ny + 30.0)
    part("JP2", -22.0, ny + 22.0, 90)                  # LED source: VCC or J6
    part("U2", -20.5, ny + 10.0)                       # LED data level shifter, clear of the Choc socket
    part("C2", -21.5, ny + 5.5)
    part("JP3", -22.0, ny + 14.5, 90)                  # U2 bypass
    tp_y = py + pad["h"] + 2.5
    part("J2", 0, tp_y, 270, bottom=False)             # trackpad wire pads, pin 1 (SDA) right
    # optical sensor pads just above the trackpad pads, inside the trackpad area
    part("J5", 0, py + pad["h"] - 2.5, 90, bottom=False)
    for i, ref in enumerate(("R3", "R4", "R5")):
        part(ref, -4.5 + i * 4.5, tp_y + 4.3, 0)        # between the pad labels and the battery
    batt = layout["battery"]
    part("J1", (batt[0]["x"] + batt[1]["x"]) / 2, batt[0]["y"], 0, bottom=False)
    part("J6", 8.0, batt[0]["y"], 0, bottom=False)    # external LED supply, beside the battery

    # pad names: trackpad below its row, sensor above (inside the trackpad area),
    # battery below, headers on the side facing the nice!nano
    fps = {f.GetReference(): f for f in b.board.GetFootprints()}
    for ref, side in (("J2", 1), ("J5", -1), ("J1", 1), ("J3", 1), ("J4", 1), ("J6", 1)):
        label_pads(b, fps[ref], side)
    # which end of JP2 is which: pad 1 = VCC, pad 3 = J6 (external)
    for num, text in (("1", "VCC"), ("3", "EXT")):
        x, y = pad_xy(fps["JP2"], num)
        c = fps["JP2"].GetPosition()
        dx, dy = x - pcbnew.ToMM(c.x), y - pcbnew.ToMM(c.y)
        n = math.hypot(dx, dy) or 1
        silk_text(b, text, pcbnew.VECTOR2I(mm(x + dx / n * 1.9), mm(y + dy / n * 1.9)),
                  layers=(pcbnew.B_SilkS,))            # JP2 is an SMD jumper on the bottom
    # JP2's reference to its outer side, off Q1's pads
    ref, c = fps["JP2"].Reference(), fps["JP2"].GetPosition()
    ref.SetPosition(pcbnew.VECTOR2I(2 * c.x - ref.GetPosition().x - mm(1.0), ref.GetPosition().y))
    for ref in ("Q1", "Q2", "U2", "SW43"):             # no room for these reference labels
        fps[ref].Reference().SetVisible(False)
    # pin table under the trackpad, between the nice!nano and the sensor pad labels
    pin_table(b, ny + nano["h"] + 1.5, py + pad["h"] - 8.5)

    # --- trackpad 3D model on a board-only footprint (the module is glued on)
    tpfp = pcbnew.FOOTPRINT(b.board)
    tpfp.SetReference("TP1")
    tpfp.Reference().SetVisible(False)
    tpfp.SetValue("TPS43")
    tpfp.Value().SetVisible(False)
    tpfp.SetAttributes(pcbnew.FP_EXCLUDE_FROM_BOM | pcbnew.FP_EXCLUDE_FROM_POS_FILES | pcbnew.FP_BOARD_ONLY)
    m = pcbnew.FP_3DMODEL()
    m.m_Filename = "${KIPRJMOD}/lib/3d/trackpad_tps43_box.step"
    tpfp.Add3DModel(m)
    b.board.Add(tpfp)
    tpfp.SetPosition(b.xy(0, py + pad["h"] / 2))

    # --- outline (with the USB notch), trackpad, break lines
    outline = usb_notch(layout["outline"], ny + NANO_USB_EDGE)
    polygon(b, outline, pcbnew.Edge_Cuts)
    tw, th = TRACKPAD
    tx0, ty0 = -tw / 2, py + (pad["h"] - th) / 2
    for layer in (pcbnew.F_Fab, pcbnew.Dwgs_User):
        polygon(b, [(tx0, ty0), (tx0 + tw, ty0), (tx0 + tw, ty0 + th), (tx0, ty0 + th)], layer)
    for i, (side, sign) in enumerate((("L", 1), ("R", -1))):
        k0 = keys[f"{side} K00"]
        ends = []
        for y in (-40.0, 80.0):                # well past both board edges
            dx, dy = rot(sign * v["break_at"][i], y, k0["rot"])
            ends.append((k0["x"] + dx, k0["y"] + dy))
        rows = [(keys[f"{side} K{r}0"]["x"], keys[f"{side} K{r}0"]["y"]) for r in range(3)]
        break_line(b, ends, outline, rows, i + 1, lanes.LAYOUTS[variant]["tabs"])

    # --- M2 mounting holes (board-only), nudged clear of everything else
    for i, h in enumerate(layout["holes"]):
        fp = load_footprint(HOLE_FP)
        fp.SetReference(f"H{i + 1}")
        fp.Reference().SetVisible(False)
        fp.SetValue("M2")
        fp.SetAttributes(fp.GetAttributes() | pcbnew.FP_BOARD_ONLY | pcbnew.FP_EXCLUDE_FROM_BOM
                         | pcbnew.FP_EXCLUDE_FROM_POS_FILES)
        x, y, moved = clear_spot(b, h["x"], h["y"], outline, body=v["hole_body"],
                                 keep_out=lanes.LAYOUTS[variant]["keep_out"])
        b.board.Add(fp)
        place(b, fp, x, y, 0)
        if moved > 0.01:
            print(f"  H{i + 1} moved {moved:.2f} mm from its nominal position")

    out = ROOT / v["board"]
    b.board.Save(str(out))
    if variant != "mx":
        # same design rules as the main project
        stem = out.with_suffix("")
        pro = json.loads((ROOT / "unicorne.kicad_pro").read_text())
        pro["meta"]["filename"] = stem.name + ".kicad_pro"
        stem.with_suffix(".kicad_pro").write_text(json.dumps(pro, indent=2) + "\n")
        rules = (ROOT / "unicorne.kicad_dru").read_text()
        if v["hole_body"]:
            rules += ("\n# Mounting holes sit between Choc switches, inside the footprint courtyard\n"
                      "# (the top housing, above the plate) but clear of the body at PCB level.\n"
                      "(rule \"Holes between Choc switches\"\n"
                      "  (condition \"(A.Reference == 'H*' && B.Reference == 'SW*') || (A.Reference == 'SW*' && B.Reference == 'H*')\")\n"
                      "  (constraint courtyard_clearance (min -5mm)))\n")
        stem.with_suffix(".kicad_dru").write_text(rules)
    return out


if __name__ == "__main__":
    variant = sys.argv[1] if len(sys.argv) > 1 else "mx"
    print("wrote", build(variant))
