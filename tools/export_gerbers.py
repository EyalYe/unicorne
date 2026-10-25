"""Gerbers and drill files for JLCPCB, one zip per board.

    python3 tools/export_gerbers.py   # writes export/jlcpcb/unicorne-{mx,choc}-jlcpcb.zip

Runs DRC first and stops if a board has unconnected items or copper errors.
2 layers, Protel extensions, silkscreen clipped by the solder mask, zones
refilled; Excellon drills in mm with plated and non-plated holes in separate
files.
"""
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
BOARDS = {"mx": "unicorne.kicad_pcb", "choc": "unicorne-choc.kicad_pcb"}
LAYERS = "F.Cu,B.Cu,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts"
# DRC findings that don't affect fabrication
HARMLESS = {"silk_edge_clearance", "starved_thermal", "silk_overlap", "silk_over_copper",
            "copper_sliver"}          # a thin point in a GND pour, which fabs etch fine


def run(*args):
    r = subprocess.run([CLI, *args], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"kicad-cli {' '.join(args[:3])} failed:\n{r.stdout}{r.stderr}")


def check(board):
    with tempfile.TemporaryDirectory() as tmp:
        rep = Path(tmp) / "drc.json"
        subprocess.run([CLI, "pcb", "drc", "--format", "json", "-o", str(rep), str(board)], capture_output=True)
        d = json.loads(rep.read_text())
    bad = [v["type"] for v in d["violations"] if v["type"] not in HARMLESS]
    if d["unconnected_items"] or bad:
        sys.exit(f"{board.name}: {len(d['unconnected_items'])} unconnected, problems: {sorted(set(bad))}")


if __name__ == "__main__":
    out = ROOT / "export" / "jlcpcb"
    out.mkdir(parents=True, exist_ok=True)
    for variant, name in BOARDS.items():
        board = ROOT / name
        check(board)
        with tempfile.TemporaryDirectory() as tmp:
            run("pcb", "export", "gerbers", "--layers", LAYERS, "--subtract-soldermask", "--check-zones",
                "-o", tmp + "/", str(board))
            run("pcb", "export", "drill", "--format", "excellon", "--excellon-separate-th",
                "--excellon-units", "mm", "--excellon-zeros-format", "decimal", "-o", tmp + "/", str(board))
            files = sorted(p for p in Path(tmp).iterdir() if p.is_file())
            zpath = out / f"unicorne-{variant}-jlcpcb.zip"
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
                for p in files:
                    z.write(p, p.name)
        print(f"{zpath.relative_to(ROOT)}: {len(files)} files, {zpath.stat().st_size / 1e3:.0f} kB")
        for p in files:
            print("   ", p.name)
