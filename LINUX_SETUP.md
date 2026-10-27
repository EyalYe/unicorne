# Linux setup for Unicorne tooling

Branch: `linux-paths`

## Remaining gap: KiCad libraries

Run these yourself (sudo needs a password at the keyboard):

```bash
sudo pacman -S kicad-library kicad-library-3d
```

- `kicad-library` — symbols and footprints (required by the board generation script)
- `kicad-library-3d` — 3D models (optional, but recommended)

## Verify afterwards

```bash
ls /usr/share/kicad/footprints | head
kicad-cli version
python -c "import pcbnew; print(pcbnew.GetBuildVersion())"
```

Once the footprints are present, the tool scripts on the `linux-paths`
branch should run without further changes.

## Routing tools

Autorouting needs Java, the Freerouting jar, and the Python `shapely` module.

```bash
sudo pacman -S jre-openjdk python-shapely
mkdir -p ~/.local/share/freerouting
curl -L -o ~/.local/share/freerouting/freerouting.jar \
  https://github.com/freerouting/freerouting/releases/latest/download/freerouting.jar
```

Verify:

```bash
java -version
python -c "import shapely; print(shapely.__version__)"
java -jar ~/.local/share/freerouting/freerouting.jar -h
```

The in-house maze router and lane tools need only KiCad and already work.
