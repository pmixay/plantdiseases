# site/

One-page project site for the Gazprombank.Tech accelerator application. A single
self-contained `index.html` (inline CSS and JS, Google Fonts only), so it can be served by
any static host: copy the folder to the web root of the VDS (nginx `root`), or enable GitHub
Pages for it.

**Every number on the page is copied by hand** from `docs/MODEL_HISTORY.md` (ledger row 16
and the 2.7.5 notes), `docs/ROADMAP.md` and `server/models/MODEL_CARD.md`. Nothing is
recomputed or rounded differently. When a new model ships, update the figures in the hero,
the key-metrics table, the `classes` and `runs` arrays at the bottom of the script, the
comparison table and the Limitations list, and bump the date in the footer.

Not on the page on purpose: download or user counts (none are recorded in the repo), a
GitHub link (the repository is private) and a contact address.
