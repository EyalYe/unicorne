"""Footprint for the reset switch: TS-1136 H4.3, a 2-pin through-hole tactile switch,
from its drawing: body 6.00 x 3.50 mm, button 3.00 x 1.50 mm, 4.30 mm to the top of the
button, legs 6.50 mm apart, recommended holes 2x 1.30 mm.

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 tools/fp_tactile.py
"""
from pathlib import Path

import pcbnew

ROOT = Path(__file__).resolve().parent.parent
NAME = "SW_Tactile_SPST_3.5x6mm_H4.3mm_THT"
PITCH = 6.50
DRILL = 1.30
PAD = 2.2
BODY = (6.00, 3.50)
BUTTON = (3.00, 1.50)


def mm(v):
    return pcbnew.FromMM(v)


def rect(fp, w, h, layer, width):
    s = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_RECT)
    s.SetLayer(layer)
    s.SetWidth(mm(width))
    s.SetStart(pcbnew.VECTOR2I(mm(-w / 2), mm(-h / 2)))
    s.SetEnd(pcbnew.VECTOR2I(mm(w / 2), mm(h / 2)))
    fp.Add(s)


def build():
    fp = pcbnew.FOOTPRINT(None)
    fp.SetFPID(pcbnew.LIB_ID("unicorne", NAME))
    fp.SetLibDescription("Tactile switch SPST, 6.0x3.5 mm body, 4.3 mm high, 2 pins 6.5 mm apart, through-hole (TS-1136 H4.3)")
    fp.SetKeywords("tactile switch 3x6 TS-1136 reset")
    fp.SetAttributes(pcbnew.FP_THROUGH_HOLE)
    fp.Reference().SetText("REF**")
    fp.Reference().SetPosition(pcbnew.VECTOR2I(0, mm(-2.9)))
    fp.Value().SetText(NAME)
    fp.Value().SetLayer(pcbnew.F_Fab)
    fp.Value().SetPosition(pcbnew.VECTOR2I(0, mm(2.9)))
    for num, x in (("1", -PITCH / 2), ("2", PITCH / 2)):
        p = pcbnew.PAD(fp)
        p.SetNumber(num)
        p.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
        p.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
        p.SetSize(pcbnew.VECTOR2I(mm(PAD), mm(PAD)))
        p.SetDrillSize(pcbnew.VECTOR2I(mm(DRILL), mm(DRILL)))
        p.SetLayerSet(pcbnew.PAD.PTHMask())
        p.SetPosition(pcbnew.VECTOR2I(mm(x), 0))
        fp.Add(p)
    rect(fp, *BODY, pcbnew.F_Fab, 0.1)
    # silkscreen: the body's long edges, clear of the pads
    for y in (-BODY[1] / 2 - 0.12, BODY[1] / 2 + 0.12):
        s = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_SEGMENT)
        s.SetLayer(pcbnew.F_SilkS)
        s.SetWidth(mm(0.12))
        s.SetStart(pcbnew.VECTOR2I(mm(-1.9), mm(y)))
        s.SetEnd(pcbnew.VECTOR2I(mm(1.9), mm(y)))
        fp.Add(s)
    rect(fp, *BUTTON, pcbnew.F_SilkS, 0.12)                  # the button
    rect(fp, PITCH + PAD + 0.5, BODY[1] + 0.5, pcbnew.F_CrtYd, 0.05)
    m = pcbnew.FP_3DMODEL()
    m.m_Filename = "${KIPRJMOD}/lib/3d/tactile_3.5x6_h43_box.step"
    fp.Models().push_back(m)
    pcbnew.FootprintSave(str(ROOT / "lib" / "unicorne.pretty"), fp)
    print("wrote", NAME)


if __name__ == "__main__":
    build()
