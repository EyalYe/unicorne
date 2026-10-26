"""Upload files for Quilter (quilter.ai), from the lane-only boards on this branch.

Run with KiCad's bundled Python:

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 tools/quilter_export.py

Quilter takes individual files (no zip): the board with its footprints, the schematic
sheets and the project. It keeps every part and every trace and via inside the board
outline, regenerates copper pours, and supports one closed outline. Two sets per board
in export/quilter/:

  <variant>/as-is/     the real board: lanes, LED cutouts and break-line slots on Edge.Cuts
  <variant>/fallback/  only the outer outline on Edge.Cuts; every slot and LED cutout
                       becomes a keepout (no tracks, vias or pours) of its exact shape plus
                       the board's clearance to it, for when Quilter's parser trips over the cutouts

Decorative silkscreen waves are left out. Each set's files are named unicorne.* so the
project, board and schematic belong together.
"""
import re
import shutil
import sys
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).resolve().parent))
import waves  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "export" / "quilter"
BOARDS = {"mx": ("unicorne.kicad_pcb", "unicorne"), "choc": ("unicorne-choc.kicad_pcb", "unicorne-choc")}
SHEETS = ("unicorne.kicad_sch", "matrix.kicad_sch", "leds.kicad_sch")
KEEPOUT_MARGIN = 0.25        # copper to a break-line slot (the board's edge clearance)
LED_MARGIN = 0.1             # copper to an LED cutout (unicorne.kicad_dru)


def copy_set(variant, dest):
    board, project = BOARDS[variant]
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / board, dest / "unicorne.kicad_pcb")
    for ext in (".kicad_pro", ".kicad_dru"):
        if (ROOT / (project + ext)).exists():
            shutil.copy(ROOT / (project + ext), dest / ("unicorne" + ext))
    for sheet in SHEETS:
        shutil.copy(ROOT / sheet, dest / sheet)
    waves.strip_old(dest / "unicorne.kicad_pcb")
    return dest / "unicorne.kicad_pcb"


def keepout(board, poly, name):
    z = pcbnew.ZONE(board)
    z.SetIsRuleArea(True)
    z.SetDoNotAllowTracks(True)
    z.SetDoNotAllowVias(True)
    z.SetDoNotAllowZoneFills(True)
    z.SetDoNotAllowPads(False)
    z.SetDoNotAllowFootprints(False)
    ls = pcbnew.LSET()
    ls.AddLayer(pcbnew.F_Cu)
    ls.AddLayer(pcbnew.B_Cu)
    z.SetLayerSet(ls)
    z.SetZoneName(name)
    z.Outline().AddOutline(poly.Outline(0))
    board.Add(z)


def fallback(path):
    """Move every Edge.Cuts shape except the outer outline to User.Comments, and cover it
    with a keepout of the same shape plus clearance."""
    board = pcbnew.LoadBoard(str(path))
    polys = [d for d in board.GetDrawings() if d.GetLayer() == pcbnew.Edge_Cuts]
    outer = max(polys, key=lambda d: d.GetBoundingBox().GetArea())
    cuts = []
    for d in polys:
        if d is outer:
            continue
        p = pcbnew.SHAPE_POLY_SET()
        d.TransformShapeToPolygon(p, pcbnew.Edge_Cuts, 0, pcbnew.FromMM(0.01), pcbnew.ERROR_INSIDE)
        cuts.append(("slot", p))
    for fp in board.GetFootprints():
        items = [g for g in fp.GraphicalItems() if g.GetLayer() == pcbnew.Edge_Cuts]
        if items:
            p = pcbnew.SHAPE_POLY_SET()
            for g in items:
                q = pcbnew.SHAPE_POLY_SET()
                g.TransformShapeToPolygon(q, pcbnew.Edge_Cuts, 0, pcbnew.FromMM(0.01), pcbnew.ERROR_INSIDE)
                p.BooleanAdd(q)
            p.Fracture()
            cuts.append((fp.GetReference(), p))
    n = 0
    for name, p in cuts:
        margin = LED_MARGIN if name.startswith("LED") else KEEPOUT_MARGIN
        p.Inflate(pcbnew.FromMM(margin), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, pcbnew.FromMM(0.02))
        for i in range(p.OutlineCount()):
            one = pcbnew.SHAPE_POLY_SET()
            one.AddOutline(p.Outline(i))
            keepout(board, one, f"cutout {name}")
            n += 1
    board.Save(str(path))
    # the cut shapes themselves go to User.Comments (a text edit: removing them through
    # the bindings is unreliable), so Edge.Cuts holds only the outer outline
    text = path.read_text()
    outer_uuid = outer.m_Uuid.AsString()

    def move(m):
        block = m.group(0)
        return block if outer_uuid in block else block.replace('(layer "Edge.Cuts")', '(layer "Cmts.User")')
    # each shape block runs from its opening line to the closing bracket at the same indent
    text = re.sub(r"(?m)^(\t*)\((?:gr_|fp_)(?:line|arc|circle|rect|poly|curve)\n.*?\n\1\)\n", move, text, flags=re.S)
    path.write_text(text)
    path.with_suffix(".kicad_prl").unlink(missing_ok=True)
    return n


if __name__ == "__main__":
    if OUT.exists():
        shutil.rmtree(OUT)
    for v in BOARDS:
        a = copy_set(v, OUT / v / "as-is")
        b = copy_set(v, OUT / v / "fallback")
        print(f"{v}: {a.relative_to(ROOT)}; fallback with {fallback(b)} keepouts")
