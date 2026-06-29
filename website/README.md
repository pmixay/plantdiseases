# PlantDiseases — marketing / showcase website

A standalone, responsive, bilingual (RU / EN) one-page site that presents the
**PlantDiseases · PlantVision** project — both the **in-app functions** and the
**ML technology** behind them.

**🌐 Live:** <https://pdis.arbuz.lol/>

## What's inside

- **`index.html`** — the full page. All copy carries `data-ru` / `data-en`
  attributes; the RU ⇄ EN toggle in the nav swaps every string live.
- **`css/styles.css`** — the design system (brand greens, Onest / Manrope /
  JetBrains Mono, dark "under the hood" bands, responsive down to mobile).
- **`js/main.js`** — sticky nav, mobile menu, language toggle (persisted to
  `localStorage`), scroll-reveal animations, and an image lightbox.
- **`js/charts.js`** — all benchmark visualisations, hand-built in native
  SVG / CSS (no image assets, no libraries): an interactive confusion-matrix
  heatmap, per-class precision/recall/F1 bars, recall dot-and-whisker with
  95% CIs, in-domain vs real-world paired bars, and a conformal-coverage
  gauge. Every element has a hover tooltip with exact values, the charts
  animate in on scroll, and they re-render live when the language is toggled.
  Data comes straight from the PlantVision 2.6.9 · gamma evaluation report.
- **`assets/screens/`** — the only raster assets: real in-app screenshots
  (Scanner, Result, Garden, Gallery, Guide, Community "Scans near you").

## Sections

Hero → Problem → Solution (3 steps) → **In-app features** (Scanner, Result,
Garden/Gallery/Guide, Community, plus AI doctor / species ID / progression /
care plans / offline / honest uncertainty) → Pipeline → Model families →
Metrics → Charts → Honest uncertainty → Roadmap → Tech stack & API → Team.

## View locally

```bash
cd website
python3 -m http.server 8080
# open http://localhost:8080
```

It's fully static — no build step.
