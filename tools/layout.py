"""Key, controller and trackpad geometry for the unicorne unibody Corne.

All units are mm, y points down (KiCad / SVG convention), angles are degrees
clockwise on screen. The outline uses KiCad's polygon engine, so run it with
KiCad's bundled Python:

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 tools/layout.py

It writes mock/layout.json and a 1:1 printable mock/unicorne.svg.
"""
import json
import math
from pathlib import Path

import pcbnew

U = 19.0                       # Corne key pitch (crkbd v3 uses 19.0, not 19.05)
CAP = 18.0                     # MX keycap footprint
TILT = 16                      # rotation of each half
NANO_W, NANO_H = 18.0, 33.3    # nice!nano v2 PCB
PAD_W, PAD_H = 43.0, 43.0      # Azoteq TPS43 (datasheet 43x40, Mouser lists 43x43)
CENTER_W = PAD_W               # centre column also holds power switch and reset
CAP_CLEAR = 2.0                # keycap-to-centre-column clearance
HALF_GAP = 1.5                 # minimum gap between the two halves' keycaps
EDGE = 1.5                     # board edge outside the switch cells
FILLET = 4.0                   # rounding of inside corners of the outline

# Left half switch positions copied from foostan/crkbd v3-final,
# corne-cherry/pcb/corne-cherry.kicad_pcb (SW1-SW21), relative to SW1.
# KiCad orientation is counter-clockwise; w is the key width in U.
CORNE_X0, CORNE_Y0 = 20.108, 35.78
CORNE_COL_Y = [35.78, 35.78, 31.03, 28.655, 31.03, 33.405]   # row 0 per column
CORNE_THUMBS = [  # (x, y, KiCad orientation, w)
    (86.608, 88.655, 0, 1.0),
    (107.608, 91.405, -15, 1.0),
    (129.857, 95.155, 60, 1.5),
]
LED_OFFSET = 4.75            # per-key SK6812MINI-E, south of the switch centre
# Corne underglow (WS2812B 5050, bottom side): (row, col, offset along switch y)
UNDERGLOW = [(0, 1, 9.5), (0, 3, 9.5), (1, 5, -9.5),
             (1, 1, 9.5), (2, 3, 9.5), (2, 5, 9.5)]
NANO_CLEAR_SW = 0.5          # bottom-side nice!nano to switch cell clearance
BATT_PITCH = 4.8             # battery B+ / B- pads (Connector_Wire SolderWire 1x02 P4.8mm)


def rot(x, y, deg):
    a = math.radians(deg)
    return x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)


def left_half_keys():
    """Keys of the untilted left half. Matrix is 6 rows x 7 cols for the whole
    board: left half uses rows 0-2, right half rows 3-5, col 6 is the thumbs."""
    keys = []
    for r in range(3):
        for c in range(6):
            keys.append(dict(row=r, col=c, x=c * U, y=CORNE_COL_Y[c] - CORNE_Y0 + r * U,
                             rot=0.0, w=1.0, name=f"K{r}{c}"))
    for i, (x, y, a, w) in enumerate(CORNE_THUMBS):
        keys.append(dict(row=i, col=6, x=x - CORNE_X0, y=y - CORNE_Y0,
                         rot=float(-a), w=w, name=f"T{i}"))
    return keys


def corners(k, w, h):
    """Corners of a w x h rectangle (mm) centred on key k, in board coords."""
    pts = []
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        dx, dy = rot(sx * w / 2, sy * h / 2, k["rot"])
        pts.append((k["x"] + dx, k["y"] + dy))
    return pts


def cap_poly(k):
    return corners(k, CAP + (k["w"] - 1) * U, CAP)


def cell_poly(k):
    return corners(k, U * k["w"], U)


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def polys_intersect(a, b):
    """Separating-axis test for two convex polygons."""
    for poly in (a, b):
        for i in range(len(poly)):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % len(poly)]
            nx, ny = y2 - y1, x1 - x2
            pa = [nx * x + ny * y for x, y in a]
            pb = [nx * x + ny * y for x, y in b]
            if max(pa) <= min(pb) or max(pb) <= min(pa):
                return False
    return True


def convex_hull(points):
    pts = sorted(set(points))
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def place(keys, tilt, dx, mirror):
    out = []
    for k in keys:
        x, y = rot(k["x"], k["y"], tilt)
        a = k["rot"] + tilt
        if mirror:
            x, a = -x, -a
        out.append(dict(k, x=x + (-dx if mirror else dx), y=y, rot=a))
    return out


def half_offset(base, tilt, keepouts):
    """Largest left-half x offset (closest to centre) that clears the keepouts,
    given as (key shape function, rectangle) pairs."""
    def fits(dx):
        for k in place(base, tilt, dx, False):
            if max(x for x, _ in cap_poly(k)) > -HALF_GAP / 2:
                return False
            if any(polys_intersect(shape(k), r) for shape, r in keepouts):
                return False
        return True
    lo, hi = -300.0, 0.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if fits(mid) else (lo, mid)
    return math.floor(lo * 4) / 4  # round to 0.25 mm, away from the centre


# --- outline, via KiCad's SHAPE_POLY_SET -------------------------------------

def to_kicad(polys):
    s = pcbnew.SHAPE_POLY_SET()
    for poly in polys:
        s.NewOutline()
        for x, y in poly:
            s.Append(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
    return s


def grow(s, mm):
    s.Inflate(pcbnew.FromMM(mm), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS,
              pcbnew.FromMM(0.02))


def outline_points(s):
    assert s.OutlineCount() == 1, "board outline is not one piece"
    o = s.Outline(0)
    return [(pcbnew.ToMM(o.CPoint(i).x), pcbnew.ToMM(o.CPoint(i).y))
            for i in range(o.PointCount())]


def build(tilt=TILT):
    base = left_half_keys()
    for i, a in enumerate(base):
        for b in base[i + 1:]:
            assert not polys_intersect(cap_poly(a), cap_poly(b)), (a, b)

    # Centre column: the nice!nano is on the bottom side with its USB on the
    # top edge, which lines up with the top of the innermost key column. The
    # trackpad is centred between that edge and the bottom of the board.
    placed = place(base, tilt, 0, False)
    inner_top = [k for k in placed if k["col"] == 5 and k["row"] == 0][0]
    nano_y = min(y for _, y in cell_poly(inner_top)) - EDGE
    bottom = max(y for k in placed for _, y in cell_poly(k)) + EDGE
    pad_y = (nano_y + bottom) / 2 - PAD_H / 2
    keepouts = [
        (cap_poly, rect(-PAD_W / 2 - CAP_CLEAR, pad_y - CAP_CLEAR,
                        PAD_W / 2 + CAP_CLEAR, pad_y + PAD_H + CAP_CLEAR)),
        (cell_poly, rect(-NANO_W / 2 - NANO_CLEAR_SW, nano_y,
                         NANO_W / 2 + NANO_CLEAR_SW, nano_y + NANO_H + NANO_CLEAR_SW)),
    ]
    dx = half_offset(base, tilt, keepouts)

    left = place(base, tilt, dx, False)
    right = place(base, tilt, dx, True)
    for k in right:
        k["row"] += 3
    keys = left + right
    for k in keys:
        k["side"] = "L" if k["x"] < 0 else "R"

    # Each half follows its keys; the centre is the hull of the centre column
    # and the inner keys, which ties the halves into one board.
    cells = [cell_poly(k) for k in keys]
    board = to_kicad(cells)
    grow(board, EDGE)
    inner = [p for k in keys if k["col"] >= 5 for p in cell_poly(k)]
    centre = rect(-CENTER_W / 2, nano_y, CENTER_W / 2, pad_y + PAD_H)
    bridge = to_kicad([convex_hull(inner + centre)])
    board.BooleanAdd(bridge)
    grow(board, FILLET)            # closing: fill the stagger notches with fillets
    grow(board, -FILLET)
    board.Simplify()
    outline = outline_points(board)

    xs = [x for x, _ in outline]
    ys = [y for _, y in outline]

    # Break line for the 36-key version: between the outer pinky column and the
    # next one, running past the board edge so it can be clipped to the outline.
    breaks = []
    for half, sign in ((left, 1), (right, -1)):
        k0 = [k for k in half if k["col"] == 0 and k["row"] % 3 == 0][0]
        y_top = -U / 2 - EDGE - 3
        y_bot = 2 * U + U / 2 + EDGE + 3
        ends = []
        for y in (y_top, y_bot):
            ex, ey = rot(sign * U / 2, y, k0["rot"])
            ends.append([round(k0["x"] + ex, 3), round(k0["y"] + ey, 3)])
        breaks.append(ends)

    # Battery pads, through-hole so they can be reached from either side,
    # centred in the space below the trackpad
    pad_bottom = pad_y + PAD_H
    batt_y = (pad_bottom + bottom) / 2 - 2
    battery = [dict(x=-BATT_PITCH / 2, y=batt_y, net="B+"),
               dict(x=BATT_PITCH / 2, y=batt_y, net="B-")]

    def local(k, x, y):
        dx_, dy_ = rot(x, y, k["rot"])
        return dict(x=k["x"] + dx_, y=k["y"] + dy_, rot=k["rot"])

    leds = [local(k, 0, LED_OFFSET) for k in keys]
    glow = []
    for half in (left, right):
        by = {(k["row"] % 3, k["col"]): k for k in half}
        glow += [local(by[(r, c)], 0, off) for r, c, off in UNDERGLOW]

    r3 = lambda v: round(v, 3)
    return dict(
        tilt=tilt,
        keys=[dict(name=k["name"], side=k["side"], row=k["row"], col=k["col"],
                   x=r3(k["x"]), y=r3(k["y"]), rot=r3(k["rot"]), w=k["w"])
              for k in keys],
        outline=[[r3(x), r3(y)] for x, y in outline],
        nano=dict(x=0, y=r3(nano_y), w=NANO_W, h=NANO_H, side="bottom"),
        trackpad=dict(x=0, y=r3(pad_y), w=PAD_W, h=PAD_H),
        breaks=breaks,
        battery=[dict(b, x=r3(b["x"]), y=r3(b["y"])) for b in battery],
        leds=[dict(x=r3(l["x"]), y=r3(l["y"]), rot=r3(l["rot"])) for l in leds],
        underglow=[dict(x=r3(g["x"]), y=r3(g["y"]), rot=r3(g["rot"])) for g in glow],
        width=r3(max(xs) - min(xs)), height=r3(max(ys) - min(ys)),
        area_cm2=r3(board.Area() / 1e14),
        half_offset=r3(-dx),
        bounds=[r3(min(xs)), r3(min(ys)), r3(max(xs)), r3(max(ys))],
    )


def to_svg(layout):
    """1:1 printable SVG (mm units): outline, keycaps, controller, trackpad."""
    x0, y0, x1, y1 = layout["bounds"]
    pad = 12
    w, h = x1 - x0 + 2 * pad, y1 - y0 + 2 * pad
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.1f}mm" height="{h:.1f}mm" '
         f'viewBox="{x0 - pad:.2f} {y0 - pad:.2f} {w:.2f} {h:.2f}" '
         'font-family="Helvetica, Arial, sans-serif">',
         '<polygon fill="none" stroke="#000" stroke-width="0.3" points="'
         + " ".join(f"{x},{y}" for x, y in layout["outline"]) + '"/>']
    for k in layout["keys"]:
        ww = CAP + (k["w"] - 1) * U
        s.append(f'<g transform="translate({k["x"]} {k["y"]}) rotate({k["rot"]})">'
                 f'<rect x="{-ww / 2}" y="{-CAP / 2}" width="{ww}" height="{CAP}" rx="1.5" '
                 'fill="none" stroke="#000" stroke-width="0.25"/>'
                 '<path d="M-1 0H1M0 -1V1" stroke="#000" stroke-width="0.15"/></g>')
    for part, label, dy in (("trackpad", "TPS43", 16), ("nano", "nice!nano (bottom)", -8)):
        n = layout[part]
        dash = "1 0.6" if part == "trackpad" else "0.4 0.6"
        s.append(f'<rect x="{-n["w"] / 2}" y="{n["y"]}" width="{n["w"]}" height="{n["h"]}" '
                 f'fill="none" stroke="#000" stroke-width="0.25" stroke-dasharray="{dash}"/>')
        s.append(f'<text x="0" y="{n["y"] + n["h"] / 2 + dy}" font-size="3" '
                 f'text-anchor="middle">{label}</text>')
    for (ax, ay), (bx, by) in layout["breaks"]:
        s.append(f'<line x1="{ax}" y1="{ay}" x2="{bx}" y2="{by}" stroke="#000" '
                 'stroke-width="0.3" stroke-dasharray="2 1"/>')
    for b in layout["battery"]:
        s.append(f'<circle cx="{b["x"]}" cy="{b["y"]}" r="1.2" fill="none" stroke="#000" '
                 'stroke-width="0.25"/>')
        s.append(f'<text x="{b["x"]}" y="{b["y"] + 4.5}" font-size="2.5" '
                 f'text-anchor="middle">{b["net"]}</text>')
    s.append(f'<text x="{x0}" y="{y1 + 6}" font-size="3.5">unicorne · tilt '
             f'{layout["tilt"]}° per half · print at 100%, check the 50 mm bar</text>')
    s.append(f'<path d="M{x0} {y1 + 8.5}h50" stroke="#000" stroke-width="0.6"/>')
    s.append('</svg>')
    return "\n".join(s)


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "mock"
    out.mkdir(exist_ok=True)
    layout = build()
    (out / "layout.json").write_text(json.dumps(layout, indent=1))
    (out / "unicorne.svg").write_text(to_svg(layout))
    print(f"{layout['tilt']} deg: {layout['width']:.1f} x {layout['height']:.1f} mm, "
          f"{layout['area_cm2']:.0f} cm2, halves at +/-{layout['half_offset']:.1f} mm, "
          f"nano top at y={layout['nano']['y']:.1f}, outline {len(layout['outline'])} pts")
