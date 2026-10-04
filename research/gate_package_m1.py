"""Chart-gate package for the user (R27): the 8 representative strategy charts as PNG files, a guide to the
markings, an HTML gallery and one ZIP with every gate chart and both setup tables. Uses the charts already rendered
from the pilot runs (python research/cli.py charts); no tester run.

python research/gate_package_m1.py -> results/pilot/gate_package/
"""
import base64
import html
import pathlib
import shutil
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[1]
PILOT = REPO / "results" / "pilot"
OUT = PILOT / "gate_package"

# (variant, direction, setup id, category file stem, what the chart is meant to show)
PICKS = [
    ("A", "long", 3836, "A_long_winner_setup003836", "A long: OB touch, HH, new M1 FVG, reaction candle, entry, TP"),
    ("A", "short", 4830, "A_short_loser_setup004830",
     "A short: break and return, LL, R16 losses on two reaction candles, entry on the third, SL"),
    ("B", "long", 4152, "B_long_loser_setup004152", "B long: HH, HL confirmed, reaction, entry; stop from the OB"),
    ("B", "short", 3300, "B_short_winner_setup003300", "B short: LL, LH confirmed, reaction, entry, TP"),
    ("A", "long", 4315, "A_long_return_break_same_bar_setup004315",
     "Return and second break in the same M1 bar: cancellation (R8)"),
    ("B", "short", 5649, "B_short_far_from_ob_setup005649",
     "Entry about 40 USD beyond the OB after four structure changes; wide stop"),
    ("A", "long", 1035, "A_long_shared_structure_setup001035",
     "Two OBs on one structure: #1035 and #1033 (OB about 16 USD lower), separate reaction candles"),
    ("B", "long", 4541, "B_long_shared_structure_setup004541",
     "Two overlapping OBs on one structure: #4541 and #4543, entries 4 minutes apart"),
]

LEGEND = [
    ("M5 OB zone", "blue band (M5 box from the OB candle; on M1 the lines 'OB high' / 'OB low')"),
    ("M5 identifying FVG", "violet box on the M5 panel"),
    ("touch", "orange triangle: first tick that reached the OB (time in timeline)"),
    ("break", "red x 'break n': an M1 close beyond the OB far edge"),
    ("return", "blue ring 'return n': renewed touch after a break"),
    ("second break (R8)", "red x 'second break (R8)' and the red dotted line 'cancelled_second_break'"),
    ("M1 structure change", "black star 'HH' / 'LL' on the breaking bar and the dashed line 'ref high/low' = crossed "
                            "pivot level; 'origin' = lowest low / highest high of the move; faint stars = earlier, "
                            "superseded structure changes"),
    ("HL / LH", "black ring 'HL' / 'LH' on its pivot (variant B needs it; in A it only feeds the stop)"),
    ("new M1 entry FVG", "aqua box 'entry FVG' (faint aqua = earlier FVGs that lapsed or were replaced)"),
    ("reaction candle", "vertical aqua band 'reaction'; '(lost R16 to #n)' = another setup took that candle"),
    ("entry", "orange diamond 'fill <price>'; on the M5 panel a small orange diamond"),
    ("SL / TP", "red / green dashed lines with their price on the right"),
    ("exit", "black X; if the trade ran longer than the M1 window: 'exit ... on the M5 panel'"),
    ("other OB on the same structure", "violet hatched band '#n OB', violet diamond '#n fill', violet band at its "
                                       "reaction candle (shared_structure charts only)"),
    ("pivots", "small ring = pivot peak, small grey square = close of its confirmation bar (N = 3), dotted between"),
    ("timeline", "table under the panels: when each fact became known; violet numbers on the M1 panel = row"),
]


def guide_md(rows) -> str:
    lines = ["# Chart gate - 8 representative strategy charts", "",
             "Pilot runs Dec 2025 - Jul 2026, variants A (HH/LL only) and B (HH+HL / LL+LH). Times are server time. "
             "The chart title also shows the example's net result after costs; it is descriptive and not a basis "
             "for any choice.", "",
             "| # | file | variant | direction | setup | what it shows |", "|---|---|---|---|---|---|"]
    lines += [f"| {i} | {r['file']} | {r['v']} | {r['d']} | #{r['sid']} | {r['what']} |" for i, r in enumerate(rows, 1)]
    lines += ["", "## Markings", "", "| marking | how it looks |", "|---|---|"]
    lines += [f"| {a} | {b} |" for a, b in LEGEND]
    lines += ["", "Each chart: top panel M5 (OB candle to the exit or cancellation, grey = the M1 window); middle panel "
              "M1; bottom the timeline. The legend under the title repeats the markings. All 32 gate charts (16 per "
              "variant) and both setup tables are in the ZIP under all_charts/.", ""]
    return "\n".join(lines)


def gallery_html(rows, inline=False) -> str:
    """inline=True embeds the PNGs as data URIs, so the page opens on its own (one file)."""
    def src(r):
        if not inline:
            return r["file"]
        return "data:image/png;base64," + base64.b64encode((OUT / r["file"]).read_bytes()).decode()
    cards = "\n".join(
        f'<section><h2>{i}. Variant {r["v"]} {r["d"]} - setup #{r["sid"]}</h2><p>{html.escape(r["what"])}</p>'
        f'<img src="{src(r)}" alt="setup {r["sid"]}"></section>'
        for i, r in enumerate(rows, 1))
    legend = "".join(f"<tr><td>{html.escape(a)}</td><td>{html.escape(b)}</td></tr>" for a, b in LEGEND)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Chart gate</title>
<style>body{{font-family:system-ui,sans-serif;margin:16px;background:#fcfcfb;color:#1f1f1e;max-width:1600px}}
img{{width:100%;height:auto;border:1px solid #ddd}} table{{border-collapse:collapse;font-size:14px}}
td{{border:1px solid #ddd;padding:4px 8px;vertical-align:top}} section{{margin:28px 0}}</style></head><body>
<h1>Chart gate - 8 representative strategy charts</h1>
<p>Pilot Dec 2025 - Jul 2026. Net results in the titles are descriptive only.</p>
<table>{legend}</table>
{cards}
</body></html>"""


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    rows = []
    for i, (v, d, sid, stem, what) in enumerate(PICKS, 1):
        src = PILOT / f"charts_pilot_{v.lower()}" / "charts" / f"{stem}.png"
        name = f"{i}_{v}_{d}_setup{sid}.png"
        shutil.copy2(src, OUT / name)
        rows.append(dict(file=name, v=v, d=d, sid=sid, what=what))
    (OUT / "guide.md").write_text(guide_md(rows), encoding="utf-8")
    (OUT / "index.html").write_text(gallery_html(rows), encoding="utf-8")
    (OUT / "gallery_single_file.html").write_text(gallery_html(rows, inline=True), encoding="utf-8")
    with zipfile.ZipFile(OUT / "gate_charts.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for r in rows:
            z.write(OUT / r["file"], r["file"])
        z.write(OUT / "guide.md", "guide.md")
        z.write(OUT / "index.html", "index.html")
        z.write(OUT / "gallery_single_file.html", "gallery_single_file.html")
        for v in ("a", "b"):
            base = PILOT / f"charts_pilot_{v}"
            for p in sorted((base / "charts").glob("*.png")):
                z.write(p, f"all_charts/variant_{v.upper()}/{p.name}")
            z.write(base / "setups_table.md", f"all_charts/setups_table_{v.upper()}.md")
    print(OUT)


if __name__ == "__main__":
    main()
