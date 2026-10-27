"""Switch plates (the top plate, starting point for a case): MX and Choc, each as a
42-key and a 36-key plate.

Run with KiCad's bundled Python:

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 tools/gen_plate.py

Same outline, coordinates and mounting holes as the matching PCB, a switch
cutout at every key, openings for the trackpad, the nice!nano (through the top
edge, so the USB stays reachable) and the reset switch. Plates are solid (no break
lines): the 36-key plate is cut evenly on both sides, where the PCB snaps off its
outer columns (their average on Choc), with rounded corners, and keeps only the holes whose screw head still fits. Writes
plate/unicorne-{mx,choc}-{42,36}-plate.kicad_pcb, then exports a JLCPCB
zip (FR4 plate, no copper), a DXF for laser cutting and a STEP model into
export/plate/. The copper layers are exported empty: JLCPCB's viewer needs them to
work out the stack, and without them it shows no silkscreen.
"""
import json
import math
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_pcb as G  # noqa: E402
from layout import rot  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CLI = "/usr/bin/kicad-cli"
PLATES = {
    # switch cutout (square, mm), plate thickness, matching PCB
    "mx": dict(cut=14.0, thickness=1.5, pcb="unicorne.kicad_pcb"),
    "choc": dict(cut=13.8, thickness=1.2, pcb="unicorne-choc.kicad_pcb"),
}
TRACKPAD_CLEAR = 0.5         # around the TPS43's 43.3 x 40.3 mm
PART_CLEAR = 0.5             # around the nice!nano and the reset switch
OPENING_FILLET = 0.8         # corner radius of the openings (a router bit's)
RESET_BODY = (6.0, 3.5)      # TS-1136 H4.3 body, long axis along its pins
SIZES = (42, 36)
CUT_FILLET = 2.0             # corner radius where the 36-key plate is cut
HEAD_R = 1.75 + 0.5          # ISO7380 M2 head plus a margin to the plate edge


def to_poly(pts):
    p = pcbnew.SHAPE_POLY_SET()
    p.NewOutline()
    for x, y in pts:
        p.Append(G.mm(x), G.mm(y))
    return p


def from_poly(p):
    o = p.Outline(0)
    return [(pcbnew.ToMM(o.CPoint(i).x), pcbnew.ToMM(o.CPoint(i).y)) for i in range(o.PointCount())]


def openings(layout, pcb, b):
    """Openings in the plate (layout coordinates): the trackpad, the nice!nano run out
    through the top edge, and the reset switch, taken from where the PCB has them."""
    rects = []
    pad = layout["trackpad"]
    tw, th = G.TRACKPAD[0] / 2 + TRACKPAD_CLEAR, G.TRACKPAD[1] / 2 + TRACKPAD_CLEAR
    cy = pad["y"] + pad["h"] / 2
    rects.append((-tw, cy - th, tw, cy + th))
    nano = layout["nano"]
    nw = nano["w"] / 2 + PART_CLEAR
    rects.append((nano["x"] - nw, nano["y"] - 20.0, nano["x"] + nw, nano["y"] + nano["h"] + PART_CLEAR))
    sw = pcb.FindFootprintByReference("SW44")
    pos = sw.GetPosition()
    sx, sy = pcbnew.ToMM(pos.x) - b.ox, pcbnew.ToMM(pos.y) - b.oy
    along_y = round(sw.GetOrientationDegrees()) % 180 == 90
    hl, hw = RESET_BODY[0] / 2 + PART_CLEAR, RESET_BODY[1] / 2 + PART_CLEAR
    dx, dy = (hw, hl) if along_y else (hl, hw)
    rects.append((sx - dx, sy - dy, sx + dx, sy + dy))
    cuts = pcbnew.SHAPE_POLY_SET()
    for x0, y0, x1, y1 in rects:
        cuts.BooleanAdd(to_poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)]))
    return cuts


def trim_36(outline, keys, break_at):
    """The outline without the outer columns: cut along the PCB's break lines, a line
    along each pinky column at break_at mm towards the inside, corners rounded."""
    poly = to_poly(outline)
    for i, (side, sign) in enumerate((("L", 1), ("R", -1))):
        k0 = keys[f"{side} K00"]
        rect = []
        for lx, ly in ((sign * break_at[i], -200), (sign * break_at[i], 200), (-sign * 200, 200), (-sign * 200, -200)):
            dx, dy = rot(lx, ly, k0["rot"])
            rect.append((k0["x"] + dx, k0["y"] + dy))
        poly.BooleanSubtract(to_poly(rect))
    poly = poly.Fillet(G.mm(CUT_FILLET), G.mm(0.01))
    return from_poly(poly)



def build(variant, size):
    v, pv = PLATES[variant], G.VARIANTS[variant]
    layout = json.loads((ROOT / "mock" / "layout.json").read_text())
    b = G.Board(layout)
    b.board.GetDesignSettings().SetBoardThickness(G.mm(v["thickness"]))
    tb = b.board.GetTitleBlock()
    tb.SetTitle(f"unicorne {variant.upper()} {size}-key switch plate ({v['thickness']} mm)")
    tb.SetDate("2026-10-25")
    tb.SetRevision("0.2")

    keys = {f'{k["side"]} {k["name"]}': k for k in layout["keys"]}
    outline = [tuple(p) for p in layout["outline"]]
    plate_keys = layout["keys"]
    if size == 36:
        # the PCB's break lines differ per side on Choc (its sockets aren't mirrored);
        # the plate has no sockets to dodge, so it is cut evenly at their average
        cut = sum(pv["break_at"]) / 2
        outline = trim_36(outline, keys, (cut, cut))
        plate_keys = [k for k in plate_keys if not (k["name"].startswith("K") and k["name"].endswith("0"))]
    assert len(plate_keys) == size

    # the plate: outline minus the openings for the trackpad, the nice!nano (open
    # through the top edge, so the USB stays reachable) and the reset switch
    pcb = pcbnew.LoadBoard(str(ROOT / v["pcb"]))
    plate = to_poly(outline)
    plate.BooleanSubtract(openings(layout, pcb, b))
    plate = plate.Fillet(G.mm(OPENING_FILLET), G.mm(0.01))
    for i in range(plate.OutlineCount()):
        for chain in [plate.Outline(i)] + [plate.Hole(i, j) for j in range(plate.HoleCount(i))]:
            G.polygon(b, [(pcbnew.ToMM(chain.CPoint(k).x), pcbnew.ToMM(chain.CPoint(k).y))
                          for k in range(chain.PointCount())], pcbnew.Edge_Cuts)

    # switch cutouts, square to each key
    h = v["cut"] / 2
    for k in plate_keys:
        corners = []
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            dx, dy = rot(sx * h, sy * h, k["rot"])
            corners.append((k["x"] + dx, k["y"] + dy))
        G.polygon(b, corners, pcbnew.Edge_Cuts)

    pad = layout["trackpad"]
    th = G.TRACKPAD[1] / 2 + TRACKPAD_CLEAR
    cy = pad["y"] + pad["h"] / 2

    # mounting holes where the PCB has them
    # (on the 36-key plate, only those whose screw head stays inside the plate)
    shape = pcbnew.SHAPE_POLY_SET(plate)
    shape.Deflate(G.mm(HEAD_R), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, G.mm(0.01))
    for fp in pcb.GetFootprints():
        if fp.GetReference().startswith("H") and fp.GetReference()[1:].isdigit():
            pos = fp.GetPosition()
            if not shape.Contains(pcbnew.VECTOR2I(pos.x - G.mm(b.ox), pos.y - G.mm(b.oy))):
                continue
            hole = G.load_footprint(G.HOLE_FP)
            hole.SetReference(fp.GetReference())
            hole.Reference().SetVisible(False)
            hole.SetValue("M2")
            hole.SetAttributes(hole.GetAttributes() | pcbnew.FP_BOARD_ONLY | pcbnew.FP_EXCLUDE_FROM_BOM
                               | pcbnew.FP_EXCLUDE_FROM_POS_FILES)
            hole.Models().clear()
            b.board.Add(hole)
            hole.SetPosition(fp.GetPosition())

    # a label, top side, below the trackpad
    t = pcbnew.PCB_TEXT(b.board)
    t.SetText(f"unicorne {variant.upper()} {size}")
    t.SetLayer(pcbnew.F_SilkS)
    t.SetTextSize(pcbnew.VECTOR2I(G.mm(1.5), G.mm(1.5)))
    t.SetTextThickness(G.mm(0.2))
    t.SetPosition(b.xy(0, cy + th + 3.0))
    b.board.Add(t)

    out = ROOT / "plate" / f"unicorne-{variant}-{size}-plate.kicad_pcb"
    out.parent.mkdir(exist_ok=True)
    b.board.Save(str(out))
    return out


def export(board):
    out = ROOT / "export" / "plate"
    out.mkdir(parents=True, exist_ok=True)
    stem = board.stem
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([CLI, "pcb", "export", "gerbers", "--layers",
                        "F.Cu,B.Cu,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts",
                        "--subtract-soldermask", "-o", tmp + "/", str(board)],
                       check=True, capture_output=True)
        subprocess.run([CLI, "pcb", "export", "drill", "--format", "excellon", "--excellon-separate-th",
                        "--excellon-units", "mm", "-o", tmp + "/", str(board)], check=True, capture_output=True)
        with zipfile.ZipFile(out / f"{stem}-jlcpcb.zip", "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(Path(tmp).iterdir()):
                z.write(p, p.name)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([CLI, "pcb", "export", "dxf", "--layers", "Edge.Cuts", "--output-units", "mm",
                        "--exclude-refdes", "--exclude-value", "-o", tmp + "/", str(board)],
                       check=True, capture_output=True)
        (dxf,) = Path(tmp).glob("*.dxf")
        dxf.replace(out / f"{stem}.dxf")
    subprocess.run([CLI, "pcb", "export", "step", "--force", "--no-components", "-o", str(out / f"{stem}.step"),
                    str(board)], check=True, capture_output=True)
    subprocess.run([CLI, "pcb", "render", "--side", "top", "--width", "2400", "--height", "1350", "--zoom", "1.6",
                    "-o", str(out / f"{stem}.png"), str(board)], check=True, capture_output=True)
    return out


if __name__ == "__main__":
    for variant in PLATES:
        for size in SIZES:
            board = build(variant, size)
            out = export(board)
            holes = sum(1 for fp in pcbnew.LoadBoard(str(board)).GetFootprints() if fp.GetValue() == "M2")
            print(f"{board.relative_to(ROOT)}: {holes} holes -> {out.relative_to(ROOT)}/{board.stem}.(zip|dxf|step|png)")
