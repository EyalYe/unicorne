"""Export both boards as STEP files with their 3D models, for case design.

    python3 tools/export_3d.py        # writes export/unicorne-mx.step, export/unicorne-choc.step

Models come from lib/3d/ (hotswap sockets, Choc V1 switch, and placeholder
boxes for parts without a model: nice!nano, TPS43, SK6812MINI, MSK-12C02) and KiCad's stock 3D library for everything else.
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = "/usr/bin/kicad-cli"
BOARDS = {"mx": "unicorne.kicad_pcb", "choc": "unicorne-choc.kicad_pcb"}

if __name__ == "__main__":
    out = ROOT / "export"
    out.mkdir(exist_ok=True)
    for variant, board in BOARDS.items():
        step = out / f"unicorne-{variant}.step"
        r = subprocess.run([CLI, "pcb", "export", "step", "--subst-models", "--force", "-o", str(step),
                            str(ROOT / board)], capture_output=True, text=True)
        missing = [l for l in (r.stdout + r.stderr).splitlines() if "not found" in l.lower() or "cannot" in l.lower()]
        print(f"{step.name}: {step.stat().st_size / 1e6:.1f} MB" if step.exists() else f"{step.name}: FAILED")
        for l in missing[:10]:
            print("  ", l.strip())
