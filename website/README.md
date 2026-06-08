# PlantDiseases — marketing / showcase website

A standalone, responsive, bilingual (RU / EN) one-page site that presents the
**PlantDiseases · PlantVision** project — both the **in-app functions** and the
**ML technology** behind them.

## What's inside

- **`index.html`** — the full page. All copy carries `data-ru` / `data-en`
  attributes; the RU ⇄ EN toggle in the nav swaps every string live.
- **`css/styles.css`** — the design system (brand greens, Onest / Manrope /
  JetBrains Mono, dark "under the hood" bands, responsive down to mobile).
- **`js/main.js`** — sticky nav, mobile menu, language toggle (persisted to
  `localStorage`), scroll-reveal animations, and a chart lightbox.
- **`assets/screens/`** — real in-app screenshots (Scanner, Result, Garden,
  Gallery, Guide, Community "Scans near you").
- **`assets/charts/`** — benchmark figures from the evaluation report
  (confusion matrix, per-class metrics, recall CIs, in-domain vs real,
  conformal coverage, quality summary, model families).

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

It's fully static — no build step. To publish, point GitHub Pages (or any
static host / CDN) at this folder.
