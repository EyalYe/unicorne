# unicorne

A unibody [Corne](https://github.com/foostan/crkbd): both halves of the Corne v3 key layout on one PCB, each half
rotated 16°, with an Azoteq TPS43 trackpad in the middle and a nice!nano v2 mounted underneath.

Status: layout fixed, schematic started, PCB not started.

## Design

- 42 keys (3×6 + 3 thumbs per half), outer pinky columns break off for a 36-key board
- Key spacing, stagger, thumb cluster and LED positions copied from crkbd `v3-final` (19.0 mm pitch)
- Two board versions with the same placement: Kailh MX hotswap, and Kailh Choc hotswap (fits Choc V1 and V2)
- nice!nano v2 on the bottom side, USB on the top edge
- TPS43 trackpad wired to six bare pads (no FPC, to keep it thin)
- Optional LEDs: 12 WS2812B underglow + 42 SK6812MINI-E per key, on their own MOSFET-switched supply
- MSK-12C02 battery cut-off switch, through-hole battery pads
- Exposed pads: expansion header (3V3, GND, UART, I2C) and GPIO header (P0.24, P1.00, P0.11)

The full pin plan and parts list are in `mock/index.html` (open it in a browser).

## Files

| Path | Contents |
|---|---|
| `unicorne.kicad_pro` | KiCad 10 project |
| `unicorne.kicad_sch`, `matrix.kicad_sch`, `leds.kicad_sch` | Schematic (generated, see below) |
| `unicorne.kicad_pcb` | PCB layout (not started) |
| `lib/` | Project symbols and footprints; third-party credits in `lib/ATTRIBUTION.md` |
| `tools/layout.py` | Key, trackpad and outline geometry → `mock/layout.json`, `mock/unicorne.svg` |
| `tools/gen_schematic.py` | Generates the schematic sheets from `mock/layout.json` |
| `mock/` | Layout data, a 1:1 printable SVG and the design overview page |

## Regenerating

```sh
# Geometry (needs KiCad's bundled Python for the outline)
/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 tools/layout.py

# Schematic (overwrites the three .kicad_sch files)
python3 tools/gen_schematic.py
```

## Licenses

The nice!nano footprint comes from [bstiq/nice-nano-kicad](https://github.com/bstiq/nice-nano-kicad) under GPLv3;
the hotswap footprints from [keyswitch-kicad-library](https://github.com/joe-scotto/keyswitch-kicad-library) under MIT.
