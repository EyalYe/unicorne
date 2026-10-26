"""Route a unicorne board with KiCadRoutingTools: fixed lanes, then its router for
every other signal, then our GND pours, then its plane repair.

Run with KiCad's bundled Python on a freshly generated board:

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 tools/krt_route.py [mx|choc]

1. tools/lanes.py lays rows, columns and per-key LED wiring; mounting holes move
   off the lanes. The lanes-only board is kept as build/route/<variant>_lanes.kicad_pcb.
2. KiCadRoutingTools routes every net but GND around the lanes, at standard fab
   sizes and with no via in or next to a pad, own net's included.
3. Nets it could only finish with undersized vias are stripped; those and anything
   it left are routed with tools/maze_route.py.
4. GND is poured on both layers and stitched; KiCadRoutingTools' plane repair joins
   any pour islands. Our project rules are put back (the tools relax them).
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).resolve().parent))
import finish_route as F  # noqa: E402
import lanes  # noqa: E402
import maze_route as M  # noqa: E402

ROOT = F.ROOT
KRT = Path.home() / "Development/tools/KiCadRoutingTools"
KRT_PY = KRT / ".venv/bin/python"
SIZES = ["--clearance", "0.2", "--via-size", "0.6", "--via-drill", "0.3",
         "--same-net-pad-clearance", "0.25", "--fab-tier", "standard"]
ROUTE = ["--nets", "*", "!GND", "--layers", "F.Cu", "B.Cu", "--track-width", "0.2",
         "--power-nets", "LED_VDD", "*LED_SRC", "*LED_EXT", "+3V3", "*VBAT", "*BATT+",
         "--power-nets-widths", "0.3", "0.3", "0.3", "0.3", "0.4", "0.4",
         "--board-edge-clearance", "0.25", "--hole-to-hole-clearance", "0.25",
         "--no-stub-layer-swap"] + SIZES
REPAIR = ["--overwrite", "--nets", "GND", "--track-width", "0.3", "--min-track-width", "0.3",
          "--max-track-width", "1.0"] + SIZES


def krt(script, *args, log):
    with open(log, "w") as out:
        subprocess.run([str(KRT_PY), f"py_router/{script}", *map(str, args)], cwd=KRT,
                       stdout=out, stderr=subprocess.STDOUT, check=True)


def undersized(path):
    """Nets with a via below 0.6 / 0.3 mm (the router's last-resort rescue)."""
    bad = set()
    for b in re.findall(r"\t\(via\n.*?\n\t\)\n", Path(path).read_text(), re.S):
        size = float(re.search(r"\(size ([0-9.]+)\)", b).group(1))
        drill = float(re.search(r"\(drill ([0-9.]+)\)", b).group(1))
        net = re.search(r'\(net "([^"]+)"\)', b)
        if net and (size < 0.6 or drill < 0.3):
            bad.add(net.group(1))
    return sorted(bad)


def drop_pad_vias(path):
    """Delete vias that touch a surface-mount pad (an open via in a pad wicks the
    solder away from the joint) and refill the pours under the board's own rules."""
    board = pcbnew.LoadBoard(str(path))
    smd = [q for q in board.GetPads() if not q.HasHole()]
    bad = [v.m_Uuid.AsString() for v in board.GetTracks() if isinstance(v, pcbnew.PCB_VIA)
           and any(q.HitTest(v.GetPosition(), v.GetWidth(pcbnew.F_Cu) // 2) for q in smd)]
    # text edit and a fresh process: removing items leaves pcbnew unusable afterwards.
    # Refill even with nothing removed: the plane repair filled under relaxed rules
    text = Path(path).read_text()
    text = re.sub(r"\t\(via\n.*?\n\t\)\n",
                  lambda m: "" if any(u in m.group() for u in bad) else m.group(), text, flags=re.S)
    Path(path).write_text(text)
    subprocess.run([sys.executable, "-I", "-c", "import pcbnew, sys; b = pcbnew.LoadBoard(sys.argv[1]); "
                    "pcbnew.ZONE_FILLER(b).Fill(b.Zones()); b.Save(sys.argv[1])", str(path)],
                   capture_output=True, check=True)
    return len(bad)


def route(variant="mx"):
    path = ROOT / F.BOARDS[variant]
    board = pcbnew.LoadBoard(str(path))
    if len(board.GetTracks()):
        sys.exit(f"{path.name} already has tracks: run tools/gen_pcb.py {variant} first")
    work = ROOT / "build" / "route"
    work.mkdir(parents=True, exist_ok=True)
    shutil.copy(path, work / f"{variant}_placed.kicad_pcb")
    print(f"laid {lanes.lay(board, variant)} lane tracks", flush=True)
    for ref, d in F.move_holes(board):
        print(f"  {ref} moved {d:.2f} mm off the lanes", flush=True)
    board.Save(str(path))
    shutil.copy(path, work / f"{variant}_lanes.kicad_pcb")

    rules = {s: path.with_suffix(s).read_bytes() for s in (".kicad_pro", ".kicad_dru")}
    try:
        krt("route.py", path, path, *ROUTE, log=work / f"{variant}_krt_route.log")
        for name in undersized(path):
            print(f"  {name}: undersized via, rerouting it", flush=True)
            F.strip_net(path, name)
        left = [q for q in F.unconnected(path) if F.net_of(q) not in (None, "GND")]
        board = pcbnew.LoadBoard(str(path))
        outline, grid, netcode, vias = F.grid_for(board)
        failed = F.route_pairs(board, grid, netcode, vias, sorted(left, key=F.length))
        print(f"KiCadRoutingTools left {len(left)} connections, {len(failed)} still unrouted", flush=True)
        for f in failed:
            print("    unrouted:", *f)
        M.pour_gnd(board, outline)
        print(f"added {F.stitch(board)} GND stitching vias", flush=True)
        board.Save(str(path))
        krt("repair_planes.py", path, *REPAIR, log=work / f"{variant}_krt_repair.log")
    finally:
        for s, data in rules.items():
            path.with_suffix(s).write_bytes(data)
    print(f"removed {drop_pad_vias(path)} vias touching a pad")
    print(f"removed {F.drop_stubs(path)} dangling track stubs")
    print(f"{len(F.unconnected(path))} unconnected")


if __name__ == "__main__":
    route(sys.argv[1] if len(sys.argv) > 1 else "mx")
