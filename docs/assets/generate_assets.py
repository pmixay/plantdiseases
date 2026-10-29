"""Regenerates docs/assets/{hero,pipeline,per-class}.svg:  python3 docs/assets/generate_assets.py docs/assets
per-class.svg numbers are copied by hand from docs/MODEL_HISTORY.md (the deployed model's rows) —
update them when a new model ships. Design rule: no text under 14px, no light-gray text."""
import sys
from pathlib import Path

OUT = Path(sys.argv[1])
FONT = "font-family=\"-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif\""
MONO = "font-family=\"ui-monospace,SFMono-Regular,Menlo,Consolas,monospace\""
INK = "#13201A"
INK2 = "#2B3A31"  # secondary text: still dark, never gray

LEAF = (
    '<path d="M0 44C0 18 16 2 46 0c0 30-16 46-42 46z" fill="#fff"/>'
    '<path d="M2 44L30 16" stroke="#2A6B46" stroke-width="3.2" fill="none" stroke-linecap="round"/>'
)


def hero():
    chips = ["9 classes", "2-stage pipeline", "offline on-device tier", "EN / RU", "honest uncertainty"]
    x = 96
    chip_svg = []
    for c in chips:
        w = 16 + len(c) * 9.4 + 16
        chip_svg.append(
            f'<rect x="{x}" y="232" width="{w:.0f}" height="36" rx="18" fill="#fff" fill-opacity=".14" '
            f'stroke="#fff" stroke-opacity=".35"/>'
            f'<text x="{x + w / 2:.0f}" y="256" text-anchor="middle" font-size="16" font-weight="500" fill="#fff" {FONT}>{c}</text>'
        )
        x += w + 12
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 300" width="1200" height="300" role="img" aria-label="PlantDiseases">
<defs>
<linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#1B4D33"/><stop offset="1" stop-color="#2F7A52"/></linearGradient>
<radialGradient id="glow" cx=".85" cy=".1" r=".7"><stop offset="0" stop-color="#9BE0B0" stop-opacity=".35"/><stop offset="1" stop-color="#9BE0B0" stop-opacity="0"/></radialGradient>
</defs>
<rect width="1200" height="300" rx="22" fill="url(#g)"/>
<rect width="1200" height="300" rx="22" fill="url(#glow)"/>
<g fill="none" stroke="#fff" stroke-opacity=".07" stroke-width="2">
<path d="M760 330C800 200 920 120 1120 70"/><path d="M820 330C860 220 960 160 1160 130"/>
<path d="M900 330C930 250 1010 210 1190 200"/><path d="M700 330C720 180 820 80 1000 20"/>
</g>
<g transform="translate(96 54) scale(1.55)">{LEAF}</g>
<text x="190" y="110" font-size="58" font-weight="700" fill="#fff" {FONT}>PlantDiseases</text>
<text x="96" y="173" font-size="27" font-weight="500" fill="#fff" {FONT}>Snap a leaf. Get a diagnosis you can trust — and a plan to fix it.</text>
<text x="96" y="209" font-size="19" fill="#fff" {FONT}>Android app + FastAPI inference service on the PlantVision two-stage pipeline.</text>
{''.join(chip_svg)}
</svg>
'''


def box(x, y, w, h, title, sub, fill, stroke, tcol=INK, scol=INK2):
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>',
           f'<text x="{x + w / 2}" y="{y + 30}" text-anchor="middle" font-size="17" font-weight="700" fill="{tcol}" {FONT}>{title}</text>']
    for i, ln in enumerate(sub.split("\n")):
        out.append(f'<text x="{x + w / 2}" y="{y + 54 + i * 20}" text-anchor="middle" font-size="14" fill="{scol}" {FONT}>{ln}</text>')
    return "".join(out)


def arrow(x1, y1, x2, y2, col="#2A6B46", dash=False):
    d = ' stroke-dasharray="5 4"' if dash else ""
    return (f'<path d="M{x1} {y1}L{x2} {y2}" stroke="{col}" stroke-width="2" fill="none"{d} '
            f'marker-end="url(#ah{"a" if col == "#C47A12" else ""})"/>')


def pipeline():
    W, H = 1200, 500
    g = [f'<rect width="{W}" height="{H}" rx="18" fill="#F6F9F5" stroke="#B8C6BB"/>',
         f'<text x="32" y="42" font-size="22" font-weight="700" fill="{INK}" {FONT}>How a photo becomes a verdict</text>',
         f'<text x="32" y="68" font-size="16" fill="{INK2}" {FONT}>Two gates can refuse to answer — an honest "I can\'t tell" beats a confident wrong diagnosis.</text>',
         f'<text x="32" y="108" font-size="14" font-weight="700" letter-spacing="1.5" fill="#1B4D33" {FONT}>SERVER TIERS  ·  gamma (default)  ·  frontier (opt-in)</text>']
    y = 122
    bw, bh, gap = 172, 100, 18
    x0 = 39
    xs = [x0 + i * (bw + gap) for i in range(6)]
    steps = [
        ("Phone photo", "camera or gallery\nEXIF-normalised", "#fff", "#B8C6BB"),
        ("Blur gate", "Laplacian variance\nfloor 50 @ 320 px", "#FFF6E3", "#C47A12"),
        ("Stage 1 · detector", "YOLOv8n, one class\n→ tight leaf crops", "#E4F1E8", "#2A6B46"),
        ("Stage 2 · classifier", "DINOv2 ViT-S/14 + LoRA\n+ MLP head, 9 classes", "#E4F1E8", "#2A6B46"),
        ("Calibration", "temperature scaling\n+ RAPS conformal set", "#E4F1E8", "#2A6B46"),
        ("OOD AND-gate", "detector ∧ k-NN dist.\n∧ no not_a_plant veto", "#FFF6E3", "#C47A12"),
    ]
    for (t, s, f, st), x in zip(steps, xs):
        g.append(box(x, y, bw, bh, t, s, f, st))
    for i in range(5):
        g.append(arrow(xs[i] + bw + 1, y + bh / 2, xs[i + 1] - 3, y + bh / 2))
    g.append(f'<text x="{xs[3] + bw / 2}" y="{y + bh + 22}" text-anchor="middle" font-size="14" fill="{INK2}" {FONT}>mean vote: top crops + whole frame</text>')
    ry = 282
    rx = xs[3]
    rw = xs[5] + bw - xs[3]
    g.append(box(rx, ry, rw, 76, "Bilingual diagnosis (EN / RU)",
                 "class · calibrated confidence · top-3 · prediction set · care plan", "#2A6B46", "#1B4D33", "#fff", "#fff"))
    g.append(arrow(xs[4] + bw / 2, y + bh + 4, xs[4] + bw / 2, ry - 3))
    g.append(arrow(xs[5] + bw / 2, y + bh + 4, xs[5] + bw / 2, ry - 3))
    nx = xs[5] + bw / 2 - 10
    g.append(f'<text x="{nx}" y="{y + bh + 32}" text-anchor="end" font-size="14" fill="#7A4A06" {FONT}>OOD hit → abstain,</text>'
             f'<text x="{nx}" y="{y + bh + 50}" text-anchor="end" font-size="14" fill="#7A4A06" {FONT}>probabilities untouched</text>')
    g.append(box(xs[1] - 4, ry, bw + 8, 76, "Abstain", "image_quality — retake", "#FFF6E3", "#C47A12", "#7A4A06", "#7A4A06"))
    g.append(arrow(xs[1] + bw / 2, y + bh + 4, xs[1] + bw / 2, ry - 3, "#C47A12", True))
    # aqua lane
    ay = 398
    g.append(f'<line x1="32" y1="{ay - 20}" x2="{W - 32}" y2="{ay - 20}" stroke="#B8C6BB" stroke-dasharray="4 4"/>')
    g.append(f'<text x="32" y="{ay + 2}" font-size="14" font-weight="700" letter-spacing="1.5" fill="#1B4D33" {FONT}>ON-DEVICE TIER  ·  aqua (no network)</text>')
    aw, ah, ag = 210, 60, 30
    ax = [32 + i * (aw + ag) for i in range(4)]
    asteps = [("Phone photo", "#fff", "#B8C6BB"), ("Blur gate (same floor)", "#FFF6E3", "#C47A12"),
              ("MobileNetV3-Small · TFLite", "#E4F1E8", "#2A6B46"), ("Offline diagnosis", "#2A6B46", "#1B4D33")]
    for (t, f, st), x in zip(asteps, ax):
        tc = "#fff" if f == "#2A6B46" else INK
        g.append(f'<rect x="{x}" y="{ay + 14}" width="{aw}" height="{ah}" rx="12" fill="{f}" stroke="{st}" stroke-width="1.6"/>'
                 f'<text x="{x + aw / 2}" y="{ay + 14 + 36}" text-anchor="middle" font-size="15" font-weight="700" fill="{tc}" {FONT}>{t}</text>')
    for i in range(3):
        g.append(arrow(ax[i] + aw + 1, ay + 14 + ah / 2, ax[i + 1] - 3, ay + 14 + ah / 2))
    g.append(f'<text x="{ax[3] + aw + 16}" y="{ay + 14 + 26}" font-size="14" fill="{INK2}" {FONT}>distilled from gamma;</text>'
             f'<text x="{ax[3] + aw + 16}" y="{ay + 14 + 46}" font-size="14" fill="{INK2}" {FONT}>downloaded once, cached</text>')
    defs = ('<defs>'
            '<marker id="ah" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#2A6B46"/></marker>'
            '<marker id="aha" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#C47A12"/></marker>'
            '</defs>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
            f'aria-label="Inference pipeline: blur gate, leaf detector, classifier, calibration, OOD gate, diagnosis; plus the on-device Aqua tier">'
            f'{defs}{"".join(g)}</svg>\n')


def per_class():
    classes = ["gray mold", "healthy", "scorch", "leaf spot", "not a plant", "nutrient def.", "pests", "powdery mildew", "viral"]
    recall = [0.92, 0.93, 0.72, 0.82, 0.97, 0.86, 0.93, 0.93, 0.91]
    prec = [0.84, 0.90, 0.73, 0.89, 0.98, 0.77, 0.95, 0.93, 0.91]
    support = [88, 581, 90, 233, 489, 65, 265, 91, 196]
    W, H = 1100, 540
    L, R, T, B = 64, 24, 104, 130
    pw, ph = W - L - R, H - T - B
    ymin, ymax = 0.5, 1.0

    def Y(v):
        return T + ph * (1 - (v - ymin) / (ymax - ymin))

    g = [f'<rect width="{W}" height="{H}" rx="18" fill="#F6F9F5" stroke="#B8C6BB"/>',
         f'<text x="28" y="42" font-size="22" font-weight="700" fill="{INK}" {FONT}>Where the deployed model is strong — and where it is not</text>',
         f'<text x="28" y="70" font-size="16" fill="{INK2}" {FONT}>Model 2.7.5 · per-class precision and recall on val_realworld (classifier-only argmax, n = 2,098). Weak class: scorch.</text>']
    for v in (0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
        g.append(f'<line x1="{L}" y1="{Y(v):.1f}" x2="{W - R}" y2="{Y(v):.1f}" stroke="#C9D3C7" stroke-width="1"/>'
                 f'<text x="{L - 10}" y="{Y(v) + 5:.1f}" text-anchor="end" font-size="14" fill="{INK2}" {MONO}>{v:.1f}</text>')
    n = len(classes)
    slot = pw / n
    bw = 32
    for i, c in enumerate(classes):
        cx = L + slot * (i + 0.5)
        weak = c == "scorch"
        if weak:
            g.append(f'<rect x="{cx - slot / 2 + 4:.1f}" y="{T - 8}" width="{slot - 8:.1f}" height="{ph + 16}" rx="8" fill="#EB6834" fill-opacity=".10"/>')
        for j, (v, col) in enumerate(((prec[i], "#2A78D6"), (recall[i], "#EB6834"))):
            x = cx - bw - 2 + j * (bw + 4)
            y = Y(v)
            g.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw}" height="{T + ph - y:.1f}" rx="3" fill="{col}"/>'
                     f'<text x="{x + bw / 2:.1f}" y="{y - 6:.1f}" text-anchor="middle" font-size="14" font-weight="600" fill="{INK}" {MONO}>{v:.2f}</text>')
        g.append(f'<text x="{cx:.1f}" y="{T + ph + 26}" text-anchor="middle" font-size="15" font-weight="{700 if weak else 500}" fill="{INK}" {FONT}>{c}</text>'
                 f'<text x="{cx:.1f}" y="{T + ph + 46}" text-anchor="middle" font-size="14" fill="{INK2}" {MONO}>n={support[i]}</text>')
    g.append(f'<line x1="{L}" y1="{T + ph}" x2="{W - R}" y2="{T + ph}" stroke="{INK2}"/>')
    ly = H - 48
    g.append(f'<rect x="{L}" y="{ly - 12}" width="14" height="14" rx="2" fill="#2A78D6"/><text x="{L + 22}" y="{ly}" font-size="15" fill="{INK}" {FONT}>precision</text>'
             f'<rect x="{L + 120}" y="{ly - 12}" width="14" height="14" rx="2" fill="#EB6834"/><text x="{L + 142}" y="{ly}" font-size="15" fill="{INK}" {FONT}>recall</text>'
             f'<text x="{L}" y="{H - 18}" font-size="14" fill="{INK2}" {FONT}>Y axis starts at 0.5. The eval slice is re-drawn every run — compare releases in docs/MODEL_HISTORY.md, not here.</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
            f'aria-label="Per-class precision and recall of model 2.7.5">{"".join(g)}</svg>\n')


OUT.mkdir(parents=True, exist_ok=True)
(OUT / "hero.svg").write_text(hero())
(OUT / "pipeline.svg").write_text(pipeline())
(OUT / "per-class.svg").write_text(per_class())
