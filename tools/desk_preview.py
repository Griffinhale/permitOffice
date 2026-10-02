"""Render the real PermitDeskView from fixed fake game states to PNG files.

Runs on Linux without ArcGIS Pro: the scenarios come from pure rules plus an
arcpy stand-in (tests/fixtures/desk_preview/scenarios.py), and the window is
drawn on Xvfb when no display is available.

    uv run --with pillow python tools/desk_preview.py            # PNGs to a temp dir
    uv run --with pillow python tools/desk_preview.py --out DIR  # PNGs to DIR
    uv run --with pillow python tools/desk_preview.py --check    # nonzero on any failure

Xvfb is used even when DISPLAY is set; pass --use-display to draw on screen.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = ROOT / "tests" / "fixtures" / "desk_preview"
for _path in (ROOT, SCENARIO_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

SIZES = ((1280, 1000), (1180, 860))
XVFB_FALLBACK = "/run/current-system/sw/bin/Xvfb"


def _free_display():
    """Return the first X display number with no lock file or socket."""

    for number in range(90, 200):
        if not os.path.exists(f"/tmp/.X{number}-lock") and not os.path.exists(f"/tmp/.X11-unix/X{number}"):
            return number
    raise RuntimeError("no free X display number between :90 and :199")


# Stand-ins for Segoe UI, best metric match first. Tk builds without Xft only see
# X core fonts, so the private Xvfb serves one of these under the Segoe UI name.
STAND_IN_FONTS = ("Liberation Sans", "DejaVu Sans")
_STYLES = (("Regular", "medium", "r"), ("Bold", "bold", "r"), ("Italic", "medium", "i"), ("Bold Italic", "bold", "i"))


def _segoe_font_dir(work_dir):
    """Write an X font dir that maps a stand-in TTF family to "segoe ui".

    Returns (dir, stand-in family), or (None, "") when fc-match finds none.
    """

    fc_match = shutil.which("fc-match")
    if fc_match is None:
        return None, ""
    for family in STAND_IN_FONTS:
        entries = []
        for style, weight, slant in _STYLES:
            found = subprocess.run([fc_match, "-f", "%{family}\\n%{file}", f"{family}:style={style}"], capture_output=True, text=True).stdout.split("\n")
            if len(found) < 2 or family not in found[0] or not found[1].endswith(".ttf"):
                break
            entries.append((found[1], weight, slant))
        if len(entries) != len(_STYLES):
            continue
        font_dir = Path(work_dir) / "segoe-stand-in"
        font_dir.mkdir(parents=True, exist_ok=True)
        lines = []
        for index, (source, weight, slant) in enumerate(entries):
            link = font_dir / f"segoe-{index}.ttf"
            if not link.exists():
                link.symlink_to(source)
            lines.append(f"{link.name} -misc-segoe ui-{weight}-{slant}-normal--0-0-0-0-p-0-iso10646-1")
        body = f"{len(lines)}\n" + "\n".join(lines) + "\n"
        (font_dir / "fonts.scale").write_text(body)
        (font_dir / "fonts.dir").write_text(body)
        return font_dir, family
    return None, ""


def _start_xvfb(work_dir):
    """Start Xvfb on a free display and return (process, display, stand-in family)."""

    binary = shutil.which("Xvfb") or (XVFB_FALLBACK if os.path.exists(XVFB_FALLBACK) else None)
    if binary is None:
        raise RuntimeError("Xvfb not found; set DISPLAY or install Xvfb")
    number = _free_display()
    display = f":{number}"
    font_dir, stand_in = _segoe_font_dir(work_dir)
    font_path = ["-fp", f"{font_dir},built-ins"] if font_dir else []
    proc = subprocess.Popen(
        [binary, display, "-screen", "0", "1600x1200x24", "-nolisten", "tcp", *font_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    sock = f"/tmp/.X11-unix/X{number}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"Xvfb exited early with code {proc.returncode}")
        if os.path.exists(sock):
            probe = socket.socket(socket.AF_UNIX)
            try:
                probe.connect(sock)
                return proc, display, stand_in
            except OSError:
                pass
            finally:
                probe.close()
        time.sleep(0.05)
    proc.terminate()
    raise RuntimeError("Xvfb did not accept connections within 10 seconds")


def _noop(*_args):
    """Swallow a desk callback; previews never act on the game."""


def _callbacks():
    """Return a DeskCallbacks bundle where every action does nothing."""

    from toolbox.permit_office_arcgis.desk_model import DeskCallbacks

    names = ("toggle_exhibit", "update_from_map", "inspect", "approve", "approve_mitigated", "deny", "advance_turn", "new_game", "scorecard", "close")
    extra = ("select_desk_tab", "select_report", "show_help", "end_game", "cancel_queue_autoclose", "pause_queue_autoclose")
    return DeskCallbacks(**{name: _noop for name in names + extra})


def _grab(root, display, path):
    """Save the root window to ``path``; fall back to canvas postscript."""

    root.update_idletasks()
    root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    try:
        from PIL import ImageGrab

        image = ImageGrab.grab(bbox=(x, y, x + w, y + h), xdisplay=display)
        image.save(path)
        return image
    except Exception as exc:  # Pillow without XCB: postscript needs Ghostscript to rasterize.
        from PIL import Image

        canvas = root.winfo_children()[0]
        ps = Path(path).with_suffix(".ps")
        canvas.postscript(file=str(ps), colormode="color", width=w, height=h)
        try:
            image = Image.open(ps)
            image.load(scale=1)
            image.save(path)
            return image
        except Exception as ps_exc:
            raise RuntimeError(f"screen grab failed ({exc}); postscript fallback failed ({ps_exc})") from ps_exc


def _is_blank(image):
    """Return True when the image is empty or a single flat color."""

    if image.width == 0 or image.height == 0:
        return True
    extrema = image.convert("RGB").getextrema()
    return all(low == high for low, high in extrema)


def render_all(out_dir, display, scenario_names=None, stand_in=""):
    """Render each scenario at each size and return a list of (path, image)."""

    import tkinter as tk
    import tkinter.font as tkfont

    from scenarios import SCENARIOS, TICKER_OFFSETS
    from toolbox.permit_office_arcgis.desk_view import PermitDeskView

    names = scenario_names or list(SCENARIOS)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for width, height in SIZES:
        for name in names:
            model = SCENARIOS[name]()
            root = tk.Tk()
            try:
                root.geometry(f"{width}x{height}+0+0")
                root.title("Permit Office preview")
                view = PermitDeskView(root, _callbacks(), _noop)
                root.update()
                view.render(model)
                if name in TICKER_OFFSETS:
                    view.update_status_marquee(TICKER_OFFSETS[name])
                if not results:
                    actual = tkfont.Font(root=root, family="Segoe UI", size=9).actual("family")
                    if actual.lower() != "segoe ui":
                        print(f"font: Segoe UI missing; Tk substituted {actual}")
                    elif stand_in:
                        print(f"font: Segoe UI drawn with {stand_in} (preview stand-in, not the real font)")
                    else:
                        print("font: Segoe UI")
                path = out_dir / f"{name}-{width}x{height}.png"
                image = _grab(root, display, path)
                results.append((path, image))
                print(f"wrote {path} ({image.width}x{image.height})")
            finally:
                root.destroy()
    return results


def main(argv=None):
    """Parse arguments, render the previews, and return a process exit code."""

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, help="directory for PNGs (default: a new temp dir)")
    parser.add_argument("--xvfb", action="store_true", help="draw on a private Xvfb (the default; kept for scripts)")
    parser.add_argument("--use-display", action="store_true", help="draw on $DISPLAY instead of a private Xvfb (real fonts, windows flash on screen)")
    parser.add_argument("--check", action="store_true", help="render every scenario; exit 1 on any error or blank image")
    parser.add_argument("--scenario", action="append", help="render only this scenario (repeatable)")
    args = parser.parse_args(argv)

    out_dir = args.out or Path(tempfile.mkdtemp(prefix="desk-preview-"))
    xvfb = None
    stand_in = ""
    display = os.environ.get("DISPLAY", "")
    work_dir = tempfile.mkdtemp(prefix="desk-preview-x-")
    try:
        # Xvfb by default even when DISPLAY is set: no windows flash on the
        # owner's screen, and the Segoe UI stand-in keeps previews comparable.
        if args.xvfb or not args.use_display or not display:
            xvfb, display, stand_in = _start_xvfb(work_dir)
            os.environ["DISPLAY"] = display
        results = render_all(out_dir, display, None if args.check else args.scenario, stand_in)
    except Exception as exc:
        print(f"preview failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if xvfb is not None:
            xvfb.terminate()
            xvfb.wait(timeout=5)
        shutil.rmtree(work_dir, ignore_errors=True)
    if args.check:
        blank = [path for path, image in results if _is_blank(image)]
        expected = len(SIZES) * len(__import__("scenarios").SCENARIOS)
        if blank or len(results) != expected:
            print(f"check failed: {len(results)}/{expected} rendered, blank: {[str(p) for p in blank]}", file=sys.stderr)
            return 1
        print(f"check ok: {len(results)} PNGs in {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
