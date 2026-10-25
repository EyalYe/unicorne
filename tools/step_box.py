"""Write simple placeholder 3D models (one or more boxes) as STEP files.

KiCad's STEP export only takes STEP/IGES models, and several parts here have
none (nice!nano, TPS43, SK6812MINI, MSK-12C02), so these stand in
for them at the right size. Units are mm, in the footprint's frame (z up from
the board surface).

    python3 tools/step_box.py        # writes lib/3d/*_box.step
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# vertex index = x + 2y + 4z; faces listed counter-clockwise seen from outside
FACES = [((0, 0, -1), (0, 2, 3, 1)), ((0, 0, 1), (4, 5, 7, 6)), ((0, -1, 0), (0, 1, 5, 4)),
         ((0, 1, 0), (2, 6, 7, 3)), ((-1, 0, 0), (0, 4, 6, 2)), ((1, 0, 0), (1, 3, 7, 5))]


def write(path, name, boxes):
    """boxes: list of (x0, y0, z0, x1, y1, z1)."""
    lines = []
    n = [0]

    def add(s):
        n[0] += 1
        lines.append(f"#{n[0]} = {s};")
        return f"#{n[0]}"

    def pt(p):
        return add("CARTESIAN_POINT('',({:.4f},{:.4f},{:.4f}))".format(*p))

    def direction(d):
        return add("DIRECTION('',({:.4f},{:.4f},{:.4f}))".format(*d))

    ctx2 = add("APPLICATION_CONTEXT('core data for automotive mechanical design processes')")
    add(f"APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2000,{ctx2})")
    pctx = add(f"PRODUCT_CONTEXT('',{ctx2},'mechanical')")
    prod = add(f"PRODUCT('{name}','{name}','',({pctx}))")
    add(f"PRODUCT_RELATED_PRODUCT_CATEGORY('part',$,({prod}))")
    form = add(f"PRODUCT_DEFINITION_FORMATION('','',{prod})")
    dctx = add(f"PRODUCT_DEFINITION_CONTEXT('part definition',{ctx2},'design')")
    pdef = add(f"PRODUCT_DEFINITION('design','',{form},{dctx})")
    pds = add(f"PRODUCT_DEFINITION_SHAPE('','',{pdef})")
    mm = add("( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) )")
    rad = add("( NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.) )")
    sr = add("( NAMED_UNIT(*) SI_UNIT($,.STERADIAN.) SOLID_ANGLE_UNIT() )")
    unc = add(f"UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),{mm},'distance_accuracy_value','confusion accuracy')")
    gctx = add(f"( GEOMETRIC_REPRESENTATION_CONTEXT(3) GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT(({unc})) "
               f"GLOBAL_UNIT_ASSIGNED_CONTEXT(({mm},{rad},{sr})) REPRESENTATION_CONTEXT('Context #1','3D Context with UNIT and UNCERTAINTY') )")
    origin = add("AXIS2_PLACEMENT_3D('',{},{},{})".format(pt((0, 0, 0)), direction((0, 0, 1)), direction((1, 0, 0))))
    solids = []
    for x0, y0, z0, x1, y1, z1 in boxes:
        corners = [(x1 if i & 1 else x0, y1 if i & 2 else y0, z1 if i & 4 else z0) for i in range(8)]
        verts = [add(f"VERTEX_POINT('',{pt(c)})") for c in corners]
        edges = {}
        for _, loop in FACES:
            for a, b in zip(loop, loop[1:] + loop[:1]):
                if (a, b) in edges or (b, a) in edges:
                    continue
                pa, pb = corners[a], corners[b]
                d = [pb[k] - pa[k] for k in range(3)]
                length = sum(v * v for v in d) ** 0.5
                vec = add("VECTOR('',{},{:.4f})".format(direction([v / length for v in d]), length))
                line = add("LINE('',{},{})".format(pt(pa), vec))
                edges[(a, b)] = add(f"EDGE_CURVE('',{verts[a]},{verts[b]},{line},.T.)")
        faces = []
        for normal, loop in FACES:
            oriented = []
            for a, b in zip(loop, loop[1:] + loop[:1]):
                if (a, b) in edges:
                    oriented.append(add(f"ORIENTED_EDGE('',*,*,{edges[(a, b)]},.T.)"))
                else:
                    oriented.append(add(f"ORIENTED_EDGE('',*,*,{edges[(b, a)]},.F.)"))
            eloop = add("EDGE_LOOP('',({}))".format(",".join(oriented)))
            bound = add(f"FACE_OUTER_BOUND('',{eloop},.T.)")
            p0, p1 = corners[loop[0]], corners[loop[1]]
            ref = [p1[k] - p0[k] for k in range(3)]
            rl = sum(v * v for v in ref) ** 0.5
            axis = add("AXIS2_PLACEMENT_3D('',{},{},{})".format(
                pt(p0), direction(normal), direction([v / rl for v in ref])))
            plane = add(f"PLANE('',{axis})")
            faces.append(add(f"ADVANCED_FACE('',({bound}),{plane},.T.)"))
        shell = add("CLOSED_SHELL('',({}))".format(",".join(faces)))
        solids.append(add(f"MANIFOLD_SOLID_BREP('',{shell})"))
    rep = add("ADVANCED_BREP_SHAPE_REPRESENTATION('',({}),{})".format(",".join([origin] + solids), gctx))
    add(f"SHAPE_DEFINITION_REPRESENTATION({pds},{rep})")
    body = "\n".join(lines)
    text = ("ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('unicorne placeholder'),'2;1');\n"
            f"FILE_NAME('{name}.step','2026-10-24T00:00:00',(''),(''),'unicorne step_box','','');\n"
            "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\nENDSEC;\nDATA;\n"
            f"{body}\nENDSEC;\nEND-ISO-10303-21;\n")
    Path(path).write_text(text)


MODELS = {
    # nice!nano on 2.5 mm low-profile sockets: PCB + parts, USB-C receptacle overhanging the USB end
    "nice_nano_box": [(-16.65, -9.0, 2.5, 16.65, 9.0, 3.6), (-12.0, -6.0, 3.6, 10.0, 6.0, 4.6),
                      (-18.25, -4.5, 3.6, -11.0, 4.5, 6.8)],
    # Azoteq TPS43: 43.3 x 40.3 mm module, 2.03 mm with adhesive (datasheet stack-up A)
    "trackpad_tps43_box": [(-21.65, -20.15, 0.0, 21.65, 20.15, 2.03)],
    "sk6812mini_box": [(-1.75, -1.75, 0.0, 1.75, 1.75, 1.0)],
    # MSK-12C02: body plus the lever, which is on the footprint's +y side (the
    # model's -y: KiCad flips y between footprint and model coordinates)
    # reset: TS-1136 H4.3 tactile switch, 6.0 x 3.5 x 3.35 mm body, 3.0 x 1.5 mm button to 4.3 mm
    "tactile_3.5x6_h43_box": [(-3.0, -1.75, 0.0, 3.0, 1.75, 3.35), (-1.5, -0.75, 3.35, 1.5, 0.75, 4.3)],
    "msk12c02_box": [(-3.35, -1.4, 0.0, 3.35, 1.4, 1.5), (-0.75, -3.1, 0.3, 0.75, -1.4, 1.2)],
}

if __name__ == "__main__":
    out = ROOT / "lib" / "3d"
    out.mkdir(parents=True, exist_ok=True)
    for name, boxes in MODELS.items():
        write(out / f"{name}.step", name, boxes)
        print("wrote", out / f"{name}.step")
