"""Switch plates (the top plate, starting point for a case), one per switch type.

Run with KiCad's bundled Python:

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 tools/gen_plate.py

Same outline, coordinates and mounting holes as the matching PCB, a switch
cutout at every key, an opening for the trackpad, and the same 36-key break
lines. Writes plate/unicorne-{mx,choc}-plate.kicad_pcb, then exports a JLCPCB
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
import lanes  # noqa: E402
from layout import rot  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
PLATES = {
    # switch cutout (square, mm), plate thickness, matching PCB
    "mx": dict(cut=14.0, thickness=1.5, pcb="unicorne.kicad_pcb"),
    "choc": dict(cut=13.8, thickness=1.2, pcb="unicorne-choc.kicad_pcb"),
}
TRACKPAD_CLEAR = 0.5         # around the TPS43's 43.3 x 40.3 mm



def build(variant):
    v, pv = PLATES[variant], G.VARIANTS[variant]
    layout = json.loads((ROOT / "mock" / "layout.json").read_text())
    b = G.Board(layout)
    b.board.GetDesignSettings().SetBoardThickness(G.mm(v["thickness"]))
    tb = b.board.GetTitleBlock()
    tb.SetTitle(f"unicorne {variant.upper()} switch plate ({v['thickness']} mm)")
    tb.SetDate("2026-10-25")
    tb.SetRevision("0.1")

    outline = [tuple(p) for p in layout["outline"]]
    G.polygon(b, outline, pcbnew.Edge_Cuts)

    # switch cutouts, square to each key
    h = v["cut"] / 2
    for k in layout["keys"]:
        corners = []
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            dx, dy = rot(sx * h, sy * h, k["rot"])
            corners.append((k["x"] + dx, k["y"] + dy))
        G.polygon(b, corners, pcbnew.Edge_Cuts)

    # trackpad opening
    pad = layout["trackpad"]
    tw, th = G.TRACKPAD[0] / 2 + TRACKPAD_CLEAR, G.TRACKPAD[1] / 2 + TRACKPAD_CLEAR
    cy = pad["y"] + pad["h"] / 2
    G.polygon(b, [(-tw, cy - th), (tw, cy - th), (tw, cy + th), (-tw, cy + th)], pcbnew.Edge_Cuts)

    # mounting holes where the PCB has them
    pcb = pcbnew.LoadBoard(str(ROOT / v["pcb"]))
    for fp in pcb.GetFootprints():
        if fp.GetReference().startswith("H") and fp.GetReference()[1:].isdigit():
            hole = G.load_footprint(G.HOLE_FP)
            hole.SetReference(fp.GetReference())
            hole.Reference().SetVisible(False)
            hole.SetValue("M2")
            hole.SetAttributes(hole.GetAttributes() | pcbnew.FP_BOARD_ONLY | pcbnew.FP_EXCLUDE_FROM_BOM
                               | pcbnew.FP_EXCLUDE_FROM_POS_FILES)
            hole.Models().clear()
            b.board.Add(hole)
            hole.SetPosition(fp.GetPosition())

    # same 36-key break lines as the PCB
    keys = {f'{k["side"]} {k["name"]}': k for k in layout["keys"]}
    for i, (side, sign) in enumerate((("L", 1), ("R", -1))):
        k0 = keys[f"{side} K00"]
        ends = []
        for y in (-40.0, 80.0):
            dx, dy = rot(sign * pv["break_at"][i], y, k0["rot"])
            ends.append((k0["x"] + dx, k0["y"] + dy))
        rows = [(keys[f"{side} K{r}0"]["x"], keys[f"{side} K{r}0"]["y"]) for r in range(3)]
        G.break_line(b, ends, outline, rows, i + 1, lanes.LAYOUTS[variant]["tabs"])

    # a label, top side, below the trackpad
    t = pcbnew.PCB_TEXT(b.board)
    t.SetText(f"unicorne {variant.upper()} plate")
    t.SetLayer(pcbnew.F_SilkS)
    t.SetTextSize(pcbnew.VECTOR2I(G.mm(1.5), G.mm(1.5)))
    t.SetTextThickness(G.mm(0.2))
    t.SetPosition(b.xy(0, cy + th + 3.0))
    b.board.Add(t)

    out = ROOT / "plate" / f"unicorne-{variant}-plate.kicad_pcb"
    out.parent.mkdir(exist_ok=True)
    b.board.Save(str(out))
    return out


def export(variant, board):
    out = ROOT / "export" / "plate"
    out.mkdir(parents=True, exist_ok=True)
    stem = f"unicorne-{variant}-plate"
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
    return out


if __name__ == "__main__":
    for variant in PLATES:
        board = build(variant)
        out = export(variant, board)
        print(f"{board.relative_to(ROOT)} -> {out.relative_to(ROOT)}/unicorne-{variant}-plate.(zip|dxf|step)")
