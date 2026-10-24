"""Generate the unicorne schematic (root, key matrix and LED sheets).

Reads mock/layout.json for the key list, so reference designators follow the
physical layout: SW1-SW21 / D1-D21 are the left half (rows 0-2, then thumbs),
SW22-SW42 the right half. Run with plain python3:

    python3 tools/gen_schematic.py

This overwrites unicorne.kicad_sch, matrix.kicad_sch and leds.kicad_sch, so
edits made in the KiCad GUI are lost on regeneration.
"""
import copy
import json
import math
import re
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KICAD = Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport")
PROJECT = "unicorne"
VERSION = "20260306"
STUB = 2.54


# --- s-expressions -----------------------------------------------------------

class Q(str):
    """A quoted string atom."""


def parse(text):
    stack = [[]]
    for m in re.finditer(r'\(|\)|"((?:[^"\\]|\\.)*)"|[^\s()"]+', text):
        tok = m.group(0)
        if tok == "(":
            stack.append([])
        elif tok == ")":
            node = stack.pop()
            stack[-1].append(node)
        elif tok.startswith('"'):
            stack[-1].append(Q(m.group(1)))
        else:
            stack[-1].append(tok)
    return stack[0][0]


def dump(node, depth=0):
    if not isinstance(node, list):
        if isinstance(node, Q):
            return '"' + node.replace("\n", "\\n") + '"'
        if isinstance(node, float):
            return f"{node:.4f}".rstrip("0").rstrip(".")
        return str(node)
    if not any(isinstance(c, list) for c in node):
        return "(" + " ".join(dump(c) for c in node) + ")"
    pad = "\t" * (depth + 1)
    head = [dump(c) for c in node if not isinstance(c, list)]
    body = [pad + dump(c, depth + 1) for c in node if isinstance(c, list)]
    return "(" + " ".join(head) + "\n" + "\n".join(body) + "\n" + "\t" * depth + ")"


def child(node, key):
    return next((c for c in node if isinstance(c, list) and c and c[0] == key), None)


def uid():
    return Q(str(uuid.uuid4()))


# --- library symbols ---------------------------------------------------------

_libs = {}


def library(lib):
    if lib not in _libs:
        path = ROOT / "lib" / f"{lib}.kicad_sym" if lib == PROJECT else KICAD / "symbols" / f"{lib}.kicad_sym"
        tree = parse(path.read_text())
        _libs[lib] = {c[1]: c for c in tree if isinstance(c, list) and c[0] == "symbol"}
    return _libs[lib]


def flatten(lib, name):
    """Library symbol with any `extends` resolved, named `name`."""
    node = copy.deepcopy(library(lib)[name])
    ext = child(node, "extends")
    if not ext:
        return node
    parent = flatten(lib, ext[1])
    props = [c for c in node if isinstance(c, list) and c[0] == "property"]
    names = {p[1] for p in props}
    out = [parent[0], Q(name)]
    for c in parent[2:]:
        if isinstance(c, list) and c[0] == "property":
            if c[1] in names:
                continue
        if isinstance(c, list) and c[0] == "symbol":
            c[1] = Q(name + c[1][len(parent[1]):])
        out.append(c)
    first = next(i for i, c in enumerate(out)
                 if isinstance(c, list) and c[0] in ("property", "symbol", "embedded_fonts"))
    out[first:first] = props
    return out


class LibSymbol:
    def __init__(self, lib_id):
        lib, name = lib_id.split(":")
        self.lib_id = lib_id
        self.node = flatten(lib, name)
        self.node[1] = Q(lib_id)
        self.pins = {}            # number -> (x, y, angle, name)
        for unit in self.node:
            if isinstance(unit, list) and unit[0] == "symbol":
                for p in unit:
                    if isinstance(p, list) and p[0] == "pin":
                        at = child(p, "at")
                        self.pins[child(p, "number")[1]] = (
                            float(at[1]), float(at[2]), float(at[3]), child(p, "name")[1])
        self.by_name = {}
        for num, (_, _, _, nm) in self.pins.items():
            self.by_name.setdefault(nm, num)
        self.is_power = child(self.node, "power") is not None

    def pin(self, key):
        key = str(key)
        return key if key in self.pins else self.by_name[key]


_symcache = {}


def libsym(lib_id):
    if lib_id not in _symcache:
        _symcache[lib_id] = LibSymbol(lib_id)
    return _symcache[lib_id]


def check_footprint(fp):
    if not fp:
        return
    lib, name = fp.split(":")
    base = ROOT / "lib" / f"{lib}.pretty" if lib == PROJECT else KICAD / "footprints" / f"{lib}.pretty"
    assert (base / f"{name}.kicad_mod").exists(), f"missing footprint {fp}"


# --- sheets ------------------------------------------------------------------

POWER_NETS = {"GND": "power:GND", "+3V3": "power:+3V3", "LED_VDD": "unicorne:LED_VDD"}
GLOBAL_NETS = {f"ROW{i}" for i in range(6)} | {f"COL{i}" for i in range(7)} | {"LED_DIN"}
# Direction a power symbol's body points, by symbol: GND hangs down, the rest point up
POWER_BODY = {"power:GND": 270, "power:PWR_FLAG": 90}


def font(size=1.27, justify=None, hide=False):
    e = ["effects", ["font", ["size", size, size]]]
    if justify:
        e.append(["justify"] + justify.split())
    if hide:
        e.append(["hide", "yes"])
    return e


class Inst:
    def __init__(self, sheet, sym, ref, x, y, rot):
        self.sheet, self.sym, self.ref, self.x, self.y, self.rot = sheet, sym, ref, x, y, rot

    def pin_at(self, key):
        px, py, pa, _ = self.sym.pins[self.sym.pin(key)]
        a = math.radians(self.rot)
        rx = px * math.cos(a) - py * math.sin(a)
        ry = px * math.sin(a) + py * math.cos(a)
        return (round(self.x + rx, 3), round(self.y - ry, 3)), (pa + self.rot) % 360

    def connect(self, key, net):
        self.sheet.attach(self, key, net)


class Sheet:
    def __init__(self, filename, title, paper="A3"):
        self.filename, self.title, self.paper = filename, title, paper
        self.uuid = uid()
        self.items = []
        self.used = {}
        self.path = None           # instance path, set once the hierarchy is known
        self.page = "1"

    def symbol(self, lib_id, ref, value, x, y, rot=0, footprint="", fields=None, dnp=False,
               text_dx=0, text_dy=5.08, in_bom=True):
        sym = libsym(lib_id)
        self.used[lib_id] = sym
        check_footprint(footprint)
        inst = Inst(self, sym, ref, x, y, rot)
        hide_ref = ref.startswith("#")
        node = ["symbol", ["lib_id", Q(lib_id)], ["at", x, y, rot], ["unit", 1],
                ["exclude_from_sim", "no"], ["in_bom", "yes" if in_bom and not hide_ref else "no"],
                ["on_board", "no" if hide_ref else "yes"], ["dnp", "yes" if dnp else "no"],
                ["uuid", uid()],
                ["property", Q("Reference"), Q(ref), ["at", x + text_dx, y - text_dy, 0],
                 font(justify="left" if text_dx else None, hide=hide_ref)],
                ["property", Q("Value"), Q(value), ["at", x + text_dx, y + text_dy, 0],
                 font(justify="left" if text_dx else None)],
                ["property", Q("Footprint"), Q(footprint), ["at", x, y, 0], font(hide=True)],
                ["property", Q("Datasheet"), Q(""), ["at", x, y, 0], font(hide=True)]]
        for k, v in (fields or {}).items():
            node.append(["property", Q(k), Q(v), ["at", x, y, 0], font(hide=True)])
        for num in sym.pins:
            node.append(["pin", Q(num), ["uuid", uid()]])
        node.append(["instances", ["project", Q(PROJECT),
                                   ["path", None, ["reference", Q(ref)], ["unit", 1]]]])
        self.items.append(node)
        return inst

    def wire(self, a, b):
        self.items.append(["wire", ["pts", ["xy", *a], ["xy", *b]],
                           ["stroke", ["width", 0], ["type", "default"]], ["uuid", uid()]])

    def label(self, net, p, out):
        just = {0: "left", 90: "left", 180: "right", 270: "right"}[out]
        if net in GLOBAL_NETS:
            self.items.append(["global_label", Q(net), ["shape", "passive"], ["at", *p, out],
                               ["fields_autoplaced", "yes"], font(justify=just), ["uuid", uid()]])
        else:
            self.items.append(["label", Q(net), ["at", *p, out],
                               font(justify=just + " bottom"), ["uuid", uid()]])

    def no_connect(self, p):
        self.items.append(["no_connect", ["at", *p], ["uuid", uid()]])

    def power(self, lib_id, p, out):
        n = sum(1 for i in self.items if i[0] == "symbol" and str(i[1][1]).startswith(("power:", "unicorne:LED_VDD")))
        prefix = "#FLG" if lib_id == "power:PWR_FLAG" else "#PWR"
        body = POWER_BODY.get(lib_id, 90)
        rot = (out - body) % 360
        self.symbol(lib_id, f"{prefix}{self.filename[:1].upper()}{n + 1:02d}",
                    lib_id.split(":")[1], p[0], p[1], rot)

    def attach(self, inst, key, net):
        p, angle = inst.pin_at(key)
        if net is None:
            self.no_connect(p)
            return
        out = (angle + 180) % 360
        dx, dy = round(math.cos(math.radians(out)), 6), -round(math.sin(math.radians(out)), 6)
        end = (round(p[0] + dx * STUB, 3), round(p[1] + dy * STUB, 3))
        self.wire(p, end)
        if net in POWER_NETS:
            self.power(POWER_NETS[net], end, out)
        else:
            self.label(net, end, out)

    def flag(self, net, x, y):
        """PWR_FLAG on a net, hung below a short label or power symbol."""
        self.power("power:PWR_FLAG", (x, y), 90)
        self.wire((x, y), (x, y + STUB))
        if net in POWER_NETS:
            self.power(POWER_NETS[net], (x, y + STUB), 270)
        else:
            self.label(net, (x, y + STUB), 270)

    def text(self, s, x, y, size=1.27):
        self.items.append(["text", Q(s), ["exclude_from_sim", "no"], ["at", x, y, 0],
                           font(size, "left top"), ["uuid", uid()]])

    def sheet_block(self, sub, name, x, y, w=30.48, h=10.16):
        self.items.append(["sheet", ["at", x, y], ["size", w, h], ["fields_autoplaced", "yes"],
                           ["stroke", ["width", 0.1524], ["type", "solid"]],
                           ["fill", ["color", 0, 0, 0, 0.0]], ["uuid", sub.uuid],
                           ["property", Q("Sheetname"), Q(name), ["at", x, y - 0.7, 0], font(justify="left bottom")],
                           ["property", Q("Sheetfile"), Q(sub.filename), ["at", x, y + h + 0.6, 0], font(justify="left top")],
                           ["instances", ["project", Q(PROJECT), ["path", None, ["page", Q(sub.page)]]]]])

    def write(self, root_uuid, is_root):
        # fill in instance paths now that the hierarchy is known
        for item in self.items:
            inst = child(item, "instances")
            if inst:
                path = inst[1][2]
                path[1] = Q(f"/{root_uuid}" if item[0] == "sheet" or is_root else self.path)
        lib_symbols = ["lib_symbols"] + [s.node for _, s in sorted(self.used.items())]
        tree = ["kicad_sch", ["version", VERSION], ["generator", Q("eeschema")],
                ["generator_version", Q("10.0")], ["uuid", self.uuid], ["paper", Q(self.paper)],
                ["title_block", ["title", Q(self.title)], ["date", Q("2026-10-24")], ["rev", Q("0.1")],
                 ["company", Q("unicorne")]],
                lib_symbols] + self.items
        if is_root:
            tree.append(["sheet_instances", ["path", Q("/"), ["page", Q("1")]]])
        tree.append(["embedded_fonts", "no"])
        (ROOT / self.filename).write_text(dump(tree) + "\n")


# --- design ------------------------------------------------------------------

def ensure_led_vdd_symbol():
    """Project power symbol LED_VDD, cloned from power:+3V3."""
    path = ROOT / "lib" / "unicorne.kicad_sym"
    tree = parse(path.read_text())
    if any(isinstance(c, list) and c[0] == "symbol" and c[1] == "LED_VDD" for c in tree):
        return
    sym = flatten("power", "+3V3")
    sym[1] = Q("LED_VDD")
    for c in sym:
        if isinstance(c, list) and c[0] == "symbol":
            c[1] = Q("LED_VDD" + c[1][len("+3V3"):])
        if isinstance(c, list) and c[0] == "property" and c[1] in ("Value",):
            c[2] = Q("LED_VDD")
        if isinstance(c, list) and c[0] == "property" and c[1] == "Description":
            c[2] = Q("Switched LED supply (battery voltage)")
    for unit in sym:
        if isinstance(unit, list) and unit[0] == "symbol":
            for p in unit:
                if isinstance(p, list) and p[0] == "pin":
                    p[child(p, "name") and p.index(child(p, "name"))][1] = Q("LED_VDD")
    tree.insert(next(i for i, c in enumerate(tree) if isinstance(c, list) and c[0] == "symbol"), sym)
    path.write_text(dump(tree) + "\n")
    _libs.pop(PROJECT, None)


FP = {
    "nano": "unicorne:nice_nano",   # bstiq/nice-nano-kicad, flipped to the bottom side on the PCB
    "sw": "unicorne:SW_Hotswap_Kailh_MX_1.00u",
    "sw15": "unicorne:SW_Hotswap_Kailh_MX_1.50u",
    "diode": "Diode_SMD:D_SOD-123",
    "led_key": "LED_SMD:LED_SK6812MINI-E_3.2x2.8mm_P1.5mm_ReverseMount",
    "led_glow": "unicorne:LED_SK6812MINI_PLCC4_3.5x3.5mm_P1.75mm",   # stock + 3D model
    "r": "Resistor_SMD:R_0805_2012Metric_Pad1.20x1.40mm_HandSolder",
    "c": "Capacitor_SMD:C_0805_2012Metric_Pad1.18x1.45mm_HandSolder",
    "sot23": "Package_TO_SOT_SMD:SOT-23",
    "jumper": "Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm",
    "jumper3": "Jumper:SolderJumper-3_P1.3mm_Open_RoundedPad1.0x1.5mm",
    "sot23_5": "Package_TO_SOT_SMD:SOT-23-5_HandSoldering",
    "hdr2": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
    "batt": "Connector_Wire:SolderWire-0.5sqmm_1x02_P4.8mm_D0.9mm_OD2.3mm",
    "pwr_sw": "unicorne:SW_SPDT_Shouhan_MSK12C02",                   # stock + 3D model
    "reset": "Button_Switch_THT:SW_PUSH_6mm_H5mm",   # 6x6x5 mm DIP tactile switch
    "hdr6": "Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical",
    "hdr4": "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
    "sensor": "Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical",
    "trackpad": "Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical",
}

NANO_PINS = {
    1: "P0.06", 2: "P0.08", 3: "GND", 4: "GND", 5: "SDA_MOSI", 6: "SCL_SCK", 7: "RDY_MOTION",
    8: "P0.24_MISO", 9: "LED_EN", 10: "P0.11_CS", 11: "ROW3", 12: "ROW4",
    13: "COL4", 14: "COL3", 15: "COL2", 16: "COL1", 17: "COL0",
    18: "ROW2", 19: "ROW1", 20: "ROW0", 21: "+3V3", 22: "RST", 23: "GND", 24: "VBAT",
    31: "COL5", 32: "COL6", 33: "ROW5",
}


def keys_with_refs(layout):
    keys = layout["keys"]
    for i, k in enumerate(keys):
        k["ref"] = i + 1
        k["label"] = f'{k["side"]} {k["name"]}'
    return keys


def led_chain(keys):
    """One pass across the board with a single long hop at the end:
    left underglow, left rows, left thumbs, right thumbs (they sit next to each
    other), right rows, right underglow, then the breakable outer pinky columns,
    right then left. Each row runs left to right as seen from above (DIN is on
    an LED's left, DOUT on its right). Breaking a pinky column off never cuts
    an LED that is still fitted."""
    half = {side: {(k["row"] % 3, k["col"]): k for k in keys if k["side"] == side} for side in "LR"}
    # underglow spots (mock/layout.json order: (0,1) (0,3) (1,5) (1,1) (2,3) (2,5)),
    # walked so the left set starts by the nice!nano and ends by the first key
    # LED, and the right set ends by the pinky column that follows it
    chain = [("glow", "L", i) for i in (5, 2, 4, 1, 3, 0)]
    for r in range(3):
        chain += [("key", half["L"][(r, c)]) for c in range(1, 6)]
    chain += [("key", half["L"][(t, 6)]) for t in range(3)]
    chain += [("key", half["R"][(t, 6)]) for t in (2, 1, 0)]
    for r in range(3):
        chain += [("key", half["R"][(r, c)]) for c in range(5, 0, -1)]
    chain += [("glow", "R", i) for i in (3, 4, 5, 2, 1, 0)]
    chain += [("key", half["R"][(r, 0)]) for r in range(3)]
    chain += [("key", half["L"][(r, 0)]) for r in range(3)]
    return chain


def build():
    ensure_led_vdd_symbol()
    layout = json.loads((ROOT / "mock" / "layout.json").read_text())
    keys = keys_with_refs(layout)

    root = Sheet("unicorne.kicad_sch", "unicorne")
    matrix = Sheet("matrix.kicad_sch", "unicorne - key matrix")
    leds = Sheet("leds.kicad_sch", "unicorne - LEDs")
    matrix.page, leds.page = "2", "3"

    # ---- root: controller
    u1 = root.symbol("unicorne:nice_nano_v2", "U1", "nice!nano v2", 203.2, 101.6,
                     footprint=FP["nano"], fields={"Mounting": "bottom side, USB at top edge"})
    for pin, net in NANO_PINS.items():
        u1.connect(pin, net)
    root.text("nice!nano v2, mounted on the bottom side under the trackpad.\n"
              "Pins 31-33 are the three inner pads (low-frequency GPIO), used for the matrix.\n"
              "P0.09 / P0.10 are NFC pins: ZMK needs CONFIG_NFCT_PINS_AS_GPIOS=y.", 172.72, 139.7)

    # ---- root: battery, power switch, reset
    j1 = root.symbol("Connector_Generic:Conn_01x02", "J1", "Battery", 40.64, 45.72, footprint=FP["batt"], in_bom=False,
                     fields={"Note": "Through-hole B+ / B- pads, reachable from both sides"})
    j1.connect(1, "BATT+")
    j1.connect(2, "GND")
    sw = root.symbol("Switch:SW_SPDT", "SW43", "Power", 76.2, 45.72, footprint=FP["pwr_sw"],
                     fields={"Note": "Battery cut-off slide switch (part to confirm)"})
    sw.connect(2, "BATT+")
    sw.connect(1, "VBAT")
    sw.connect(3, None)
    rst = root.symbol("Switch:SW_Push", "SW44", "Reset", 76.2, 66.04, footprint=FP["reset"])
    rst.connect(1, "RST")
    rst.connect(2, "GND")
    root.text("Battery: J1 pads -> power switch -> nice!nano RAW/B+.\n"
              "With the switch off the battery is disconnected; USB still powers the board.", 27.94, 76.2)

    # ---- root: LED supply. JP2 picks the source (VCC, or J6 for an external
    # 5 V supply for full brightness); Q1 switches it onto LED_VDD from P1.00.
    jp2 = root.symbol("Jumper:SolderJumper_3_Open", "JP2", "LED source", 50.8, 96.52, footprint=FP["jumper3"],
                      in_bom=False, fields={"Note": "Bridge A-C for VCC (3.3 V) or B-C for J6 (external, e.g. 5 V)"})
    jp2.connect("A", "+3V3")
    jp2.connect("C", "LED_SRC")
    jp2.connect("B", "LED_EXT")
    j6 = root.symbol("Connector_Generic:Conn_01x02", "J6", "LED power in", 27.94, 96.52, footprint=FP["hdr2"],
                     in_bom=False, fields={"Note": "External LED supply, up to 5 V (e.g. a USB-C breakout)"})
    j6.connect(1, "LED_EXT")
    j6.connect(2, "GND")
    q1 = root.symbol("Transistor_FET:AO3401A", "Q1", "AO3401A", 76.2, 111.76, footprint=FP["sot23"])
    q1.connect("G", "LED_EN_N")
    q1.connect("S", "LED_SRC")
    q1.connect("D", "LED_VDD")
    q2 = root.symbol("Transistor_FET:2N7002", "Q2", "2N7002", 50.8, 132.08, footprint=FP["sot23"])
    q2.connect("G", "LED_EN")
    q2.connect("S", "GND")
    q2.connect("D", "LED_EN_N")
    for ref, val, a, b, x, y in (("R1", "100k", "LED_SRC", "LED_EN_N", 101.6, 106.68),
                                 ("R2", "100k", "LED_EN", "GND", 101.6, 132.08)):
        r = root.symbol("Device:R_Small", ref, val, x, y, footprint=FP["r"])
        r.connect(1, a)
        r.connect(2, b)
    c1 = root.symbol("Device:C_Small", "C1", "10uF", 121.92, 106.68, footprint=FP["c"])
    c1.connect(1, "LED_VDD")
    c1.connect(2, "GND")
    jp1 = root.symbol("Jumper:SolderJumper_2_Open", "JP1", "LED data", 121.92, 132.08, footprint=FP["jumper"],
                      in_bom=False)
    jp1.connect(1, "P0.06")
    jp1.connect(2, "LED_DATA")
    # SN74LV1T125 runs from the LED supply and takes 3.3 V logic in, so the data
    # level is right whether the LEDs get 3.3 V or 5 V
    u2 = root.symbol("74xGxx:74LVC1G125", "U2", "SN74LV1T125", 152.4, 132.08, footprint=FP["sot23_5"],
                     fields={"Note": "Must be SN74LV1T125 (TTL inputs at 5 V), not 74LVC1G125"})
    u2.connect(1, "GND")
    u2.connect(2, "LED_DATA")
    u2.connect(3, "GND")
    u2.connect(4, "LED_DIN")
    u2.connect(5, "LED_VDD")
    # JP3 bypasses U2: bridge it when the LEDs run from VCC and U2 is left off
    jp3 = root.symbol("Jumper:SolderJumper_2_Open", "JP3", "U2 bypass", 152.4, 147.32, footprint=FP["jumper"],
                      in_bom=False, fields={"Note": "Bridge with JP2 on VCC and U2 not fitted; open with U2 fitted"})
    jp3.connect(1, "LED_DATA")
    jp3.connect(2, "LED_DIN")
    c2 = root.symbol("Device:C_Small", "C2", "100nF", 167.64, 121.92, footprint=FP["c"])
    c2.connect(1, "LED_VDD")
    c2.connect(2, "GND")
    root.text("LED supply: bridge JP2 A-C for VCC (3.3 V, up to the regulator's 1 A) or B-C for J6\n"
              "(external supply up to 5 V, for full brightness). Never from the battery: RAW is the\n"
              "charger's battery pin. Q1 switches it onto LED_VDD when P1.00 (LED_EN) is high;\n"
              "R1/R2 keep it off during boot. ZMK: ext-power on P1.00, VCC kept on by a gpio-hog.\n"
              "Close JP1 to drive the LEDs from P0.06. U2 shifts the data level for a 5 V supply;\n"
              "with VCC, leave U2 and C2 off and bridge JP3 instead.", 27.94, 142.24)

    # ---- root: trackpad
    j2 = root.symbol("Connector_Generic:Conn_01x06", "J2", "TPS43", 307.34, 50.8, footprint=FP["trackpad"], in_bom=False,
                     fields={"Note": "Bare pads for wires to the TPS43, in the module's pad order"})
    for pin, net in enumerate(["SDA_MOSI", "SCL_SCK", "+3V3", "GND", "NRST", "RDY_MOTION"], 1):
        j2.connect(pin, net)
    for ref, val, net, x in (("R3", "4.7k", "SDA_MOSI", 335.28), ("R4", "4.7k", "SCL_SCK", 350.52),
                             ("R5", "10k", "NRST", 365.76)):
        r = root.symbol("Device:R_Small", ref, val, x, 50.8, footprint=FP["r"])
        r.connect(1, "+3V3")
        r.connect(2, net)
    # Optical sensor pads: alternative to the trackpad, sharing its pins over SPI
    j5 = root.symbol("Connector_Generic:Conn_01x08", "J5", "PAW3395 / PMW3360", 307.34, 165.1,
                     footprint=FP["sensor"], in_bom=False,
                     fields={"Note": "Bare pads for a PAW3395 or PMW3360 sensor board, used instead of the trackpad"})
    for pin, net in enumerate(["+3V3", "GND", "SCL_SCK", "SDA_MOSI", "P0.24_MISO", "P0.11_CS",
                               "RDY_MOTION", "NRST"], 1):
        j5.connect(pin, net)
    root.text("J5: optical sensor (PAW3395 / PMW3360) instead of the trackpad, over SPI:\n"
              "SCK = P0.20, MOSI = P0.17, MISO = P0.24, CS = P0.11, MOTION = P0.22, RST = NRST.\n"
              "SCK/MOSI/MOTION share the trackpad's SCL/SDA/RDY: fit one or the other, and leave\n"
              "R3/R4 off with a sensor. Check the sensor board's supply: some want 5 V.", 292.1, 182.88)
    root.text("Trackpad: Azoteq TPS43 (IQS572), I2C at 1.65-3.6 V.\n"
              "J2 is six bare pads for short wires to the module's own pads (no FPC, keeps it thin),\n"
              "in the module's order: SDA, SCL, 3V3, GND, RST, RDY (datasheet v1.06, figure 2.1).", 292.1, 66.04)

    # ---- root: exposed headers
    j3 = root.symbol("Connector_Generic:Conn_01x06", "J3", "Expansion", 307.34, 101.6, footprint=FP["hdr6"])
    for pin, net in enumerate(["+3V3", "GND", "P0.08", "P0.06", "SDA_MOSI", "SCL_SCK"], 1):
        j3.connect(pin, net)
    j4 = root.symbol("Connector_Generic:Conn_01x04", "J4", "GPIO", 307.34, 132.08, footprint=FP["hdr4"])
    for pin, net in enumerate(["GND", "P0.24_MISO", "LED_EN", "P0.11_CS"], 1):
        j4.connect(pin, net)
    root.text("J3 expansion: UART on P0.08/P0.06, I2C shared with the trackpad.\n"
              "J4 GPIO: P0.24 and P0.11 (sensor MISO/CS if J5 is used); P1.00 is LED_EN.", 292.1, 142.24)

    # ---- root: power flags and sub-sheets
    for i, net in enumerate(["GND", "VBAT", "LED_VDD", "BATT+", "LED_EXT"]):
        root.flag(net, 27.94 + i * 15.24, 172.72)
    root.sheet_block(matrix, "Key matrix", 27.94, 200.66)
    root.sheet_block(leds, "LEDs", 76.2, 200.66)

    # ---- matrix: 6 rows x 7 cols, COL2ROW (col -> switch -> diode anode -> cathode -> row)
    for k in keys:
        r, c = k["row"], k["col"]
        x, y = 30.48 + c * 53.34, 40.64 + r * 33.02
        fp = FP["sw15"] if k["w"] > 1 else FP["sw"]
        s = matrix.symbol("Switch:SW_Push", f'SW{k["ref"]}', f"R{r}C{c}", x, y, footprint=fp,
                          fields={"Key": k["label"]})
        s.connect(1, f"COL{c}")
        d = matrix.symbol("Diode:1N4148W", f'D{k["ref"]}', "1N4148W", x + 8.89, y, rot=180,
                          footprint=FP["diode"], fields={"Key": k["label"]})
        d.connect("K", f"ROW{r}")
    matrix.text("COL2ROW. Left half: rows 0-2 (SW1-SW21). Right half: rows 3-5 (SW22-SW42).\n"
                "Column 6 is the thumb cluster. Columns count from the outer pinky on both halves;\n"
                "column 0 breaks off for the 36-key version.", 25.4, 246.38)

    # ---- LEDs: one chain from LED_DIN
    chain = led_chain(keys)
    prev = "LED_DIN"
    glow_xy = layout["underglow"]
    for i, item in enumerate(chain):
        n = i + 1
        x, y = 30.48 + (i % 9) * 43.18, 38.1 + (i // 9) * 35.56
        if item[0] == "glow":
            _, side, j = item
            led = leds.symbol("LED:SK6812MINI", f"LED{n}", "SK6812MINI", x, y, footprint=FP["led_glow"],
                              fields={"Key": f"{side} UG{j}"}, text_dx=6.35, text_dy=8.89)
        else:
            k = item[1]
            led = leds.symbol("LED:SK6812MINI-E", f"LED{n}", "SK6812MINI-E", x, y, footprint=FP["led_key"],
                              fields={"Key": k["label"]}, text_dx=6.35, text_dy=8.89)
        led.connect("VDD", "LED_VDD")
        led.connect("VSS", "GND")
        led.connect("DIN", prev)
        nxt = f"LED_D{n}" if n < len(chain) else None
        led.connect("DOUT", nxt)
        prev = nxt
    leds.text("Chain: left underglow, left rows (left to right), left thumbs, right thumbs, right rows,\n"
              "right underglow, then the breakable outer pinky columns (right, then left). LED_VDD powers all.",
              25.4, 254.0)

    matrix.path = f"/{root.uuid}/{matrix.uuid}"
    leds.path = f"/{root.uuid}/{leds.uuid}"
    root.write(root.uuid, True)
    matrix.write(root.uuid, False)
    leds.write(root.uuid, False)


if __name__ == "__main__":
    build()
    print("wrote unicorne.kicad_sch, matrix.kicad_sch, leds.kicad_sch")
