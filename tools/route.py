"""Autoroute a unicorne board with Freerouting.

Run with KiCad's bundled Python after tools/gen_pcb.py:

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 tools/route.py [mx|choc] [passes]

Exports the board to Specctra DSN, routes it with Freerouting (run on
Homebrew's OpenJDK), imports the session back, pours GND on both layers and
saves the board. Run it on a freshly generated board (no tracks).
"""
import re
import subprocess
import sys
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_schematic import child, parse  # noqa: E402
import lanes  # noqa: E402
from maze_route import pour_gnd, route_first  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
JAVA = Path("/opt/homebrew/opt/openjdk/bin/java")
FREEROUTING = Path.home() / "Development/tools/freerouting-2.5.0/freerouting-2.5.0.jar"
BOARDS = {"mx": "unicorne.kicad_pcb", "choc": "unicorne-choc.kicad_pcb"}
UNROUTED_NETS = []           # nets Freerouting should skip (none: GND is routed, then poured)
FIRST_NETS = ["LED_VDD"]     # laid first by tools/maze_route.py and locked, so the long LED
                             # supply gets its corridor before the signals fill the board


def drop_nets(dsn, names):
    """Remove nets from a Specctra file so the router leaves them alone; their
    pads stay in the board as obstacles."""
    text = dsn.read_text()
    for name in names:
        start = text.find(f"(net {name}\n")
        if start < 0:
            start = text.find(f"(net {name} ")
        assert start >= 0, f"net {name} not in DSN"
        depth, i = 0, start
        while True:
            depth += {"(": 1, ")": -1}.get(text[i], 0)
            i += 1
            if depth == 0:
                break
        text = text[:start] + text[i:]
        text = re.sub(rf"(\(class [^()]*?)\s{re.escape(name)}(?=[\s)])", r"\1", text)
    dsn.write_text(text)


def import_ses(board, ses):
    """Add a Specctra session's wires and vias to the board. (pcbnew's own
    ImportSpecctraSES rejects Freerouting 2.5's sessions without saying why.)"""
    tree = parse(ses.read_text())
    routes = child(tree, "routes")
    res = child(routes, "resolution")
    per_mm = float(res[2]) * (1000 if res[1] == "um" else 1)

    def pos(x, y):
        return pcbnew.VECTOR2I(pcbnew.FromMM(float(x) / per_mm), pcbnew.FromMM(-float(y) / per_mm))

    layers = {"F.Cu": pcbnew.F_Cu, "B.Cu": pcbnew.B_Cu}
    count = [0, 0]
    for net in child(routes, "network_out")[1:]:
        info = board.FindNet(net[1])
        assert info, f"net {net[1]} not on board"
        for item in net[2:]:
            kind = child(item, "type") if isinstance(item, list) else None
            if kind and kind[1] in ("fix", "protect"):
                continue                       # already on the board (pre-routed)
            if item[0] == "wire":
                path = child(item, "path")
                width = pcbnew.FromMM(float(path[2]) / per_mm)
                pts = path[3:]
                for i in range(0, len(pts) - 2, 2):
                    t = pcbnew.PCB_TRACK(board)
                    t.SetStart(pos(pts[i], pts[i + 1]))
                    t.SetEnd(pos(pts[i + 2], pts[i + 3]))
                    t.SetWidth(width)
                    t.SetLayer(layers[path[1]])
                    t.SetNet(info)
                    board.Add(t)
                    count[0] += 1
            elif item[0] == "via":
                size, drill = re.search(r"_(\d+):(\d+)_um", item[1]).groups()
                v = pcbnew.PCB_VIA(board)
                v.SetPosition(pos(item[2], item[3]))
                v.SetWidth(pcbnew.FromMM(int(size) / 1000))
                v.SetDrill(pcbnew.FromMM(int(drill) / 1000))
                v.SetNet(info)
                board.Add(v)
                count[1] += 1
    return count


def route(variant, passes):
    path = ROOT / BOARDS[variant]
    board = pcbnew.LoadBoard(str(path))
    # removing thousands of tracks upsets KiCad's Python bindings, so route a fresh board
    if len(board.GetTracks()):
        sys.exit(f"{path.name} already has tracks: run tools/gen_pcb.py {variant} first")
    work = ROOT / "build" / "route" / variant
    work.mkdir(parents=True, exist_ok=True)
    dsn, ses = work / "board.dsn", work / "board.ses"
    if True:
        if variant == "mx":
            # rows, columns and per-key LED wiring in fixed lanes (tools/lanes.py)
            print(f"laid {lanes.lay(board)} lane tracks", flush=True)
        else:
            failed = route_first(board, FIRST_NETS)
            print(f"pre-routed {FIRST_NETS}: {len(failed)} connections failed", flush=True)
            for f in failed:
                print("  unrouted:", *f)
        assert pcbnew.ExportSpecctraDSN(board, str(dsn)), "DSN export failed"
        if UNROUTED_NETS:
            drop_nets(dsn, UNROUTED_NETS)
        subprocess.run([str(JAVA), "--enable-final-field-mutation=ALL-UNNAMED", "-jar", str(FREEROUTING),
                        "-de", str(dsn), "-do", str(ses), "-mp", str(passes), "-l", "en",
                        "--gui.enabled=false"], check=True)
        tracks, vias = import_ses(board, ses)
        print(f"imported {tracks} track segments and {vias} vias")
    outline = next(d for d in board.GetDrawings()
                   if d.GetLayer() == pcbnew.Edge_Cuts and d.GetShape() == pcbnew.SHAPE_T_POLY)
    pts = outline.GetPolyShape().Outline(0)
    pour_gnd(board, [(pcbnew.ToMM(pts.CPoint(i).x), pcbnew.ToMM(pts.CPoint(i).y))
                     for i in range(pts.PointCount())])
    board.Save(str(path))
    return path


if __name__ == "__main__":
    variant = sys.argv[1] if len(sys.argv) > 1 else "mx"
    passes = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    print("routed", route(variant, passes))
