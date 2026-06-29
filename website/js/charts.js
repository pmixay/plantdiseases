/* ============================================================
   PlantDiseases — native, interactive metric charts
   Hand-built SVG / CSS visualisations (no image assets, no libs).
   Data: PlantVision 2.6.8 gamma (dataset v5) · evaluation on val_realworld.
   ============================================================ */
(function () {
  'use strict';

  /* ---------- shared bilingual class labels ---------- */
  var L = {
    notplant:   { ru: 'Не растение',            en: 'Not a plant' },
    powdery:    { ru: 'Мучнистая роса',         en: 'Powdery mildew' },
    pests:      { ru: 'Вредители',              en: 'Pests' },
    healthy:    { ru: 'Здоровое растение',      en: 'Healthy plant' },
    viral:      { ru: 'Вирусное заболевание',   en: 'Viral disease' },
    graymold:   { ru: 'Серая гниль (ботритис)', en: 'Gray mold (botrytis)' },
    leafspot:   { ru: 'Пятнистость листьев',    en: 'Leaf spot' },
    nutrient:   { ru: 'Дефицит питания',        en: 'Nutrient deficiency' },
    scorch:     { ru: 'Краевой ожог (недолив)', en: 'Leaf scorch (under-water)' }
  };
  function lang() { return document.documentElement.getAttribute('lang') === 'en' ? 'en' : 'ru'; }
  function lbl(k) { return L[k][lang()]; }
  function t(ru, en) { return lang() === 'en' ? en : ru; }

  /* short, language-neutral codes for the compact confusion matrix */
  var SHORT = {
    notplant: 'NP', powdery: 'PM', pests: 'PS', healthy: 'HL', viral: 'VI',
    graymold: 'GM', leafspot: 'LS', nutrient: 'NU', scorch: 'SC'
  };
  function isNarrow() { return window.matchMedia('(max-width:680px)').matches; }

  /* ---------- data (from the evaluation report) ---------- */
  // precision / recall / f1 / n  ·  per-class on real-world photos
  var PERCLASS = [
    { k: 'notplant', p: 98.4, r: 97.8, f: 98.1, n: 508 },
    { k: 'pests',    p: 91.0, r: 95.8, f: 93.4, n: 264 },
    { k: 'powdery',  p: 91.2, r: 95.4, f: 93.3, n: 87 },
    { k: 'healthy',  p: 93.7, r: 91.2, f: 92.4, n: 634 },
    { k: 'viral',    p: 86.9, r: 89.6, f: 88.3, n: 193 },
    { k: 'leafspot', p: 91.4, r: 83.0, f: 87.0, n: 218 },
    { k: 'graymold', p: 82.1, r: 88.6, f: 85.2, n: 88 },
    { k: 'scorch',   p: 75.2, r: 78.8, f: 77.0, n: 104 },
    { k: 'nutrient', p: 74.7, r: 78.2, f: 76.4, n: 87 }
  ];

  // recall + 95% bootstrap CI (low/high) · sorted desc by recall
  var RECALL_CI = [
    { k: 'notplant', r: 97.8, lo: 97, hi: 99,  n: 508 },
    { k: 'pests',    r: 95.8, lo: 93, hi: 98,  n: 264 },
    { k: 'powdery',  r: 95.4, lo: 90, hi: 99,  n: 87 },
    { k: 'healthy',  r: 91.2, lo: 89, hi: 93,  n: 634 },
    { k: 'viral',    r: 89.6, lo: 85, hi: 94,  n: 193 },
    { k: 'graymold', r: 88.6, lo: 82, hi: 95,  n: 88 },
    { k: 'leafspot', r: 83.0, lo: 78, hi: 88,  n: 218 },
    { k: 'scorch',   r: 78.8, lo: 71, hi: 87,  n: 104 },
    { k: 'nutrient', r: 78.2, lo: 68, hi: 87,  n: 87 }
  ];

  // in-domain recall vs real-world recall (both from the val_indomain /
  // val_realworld eval slices of the same run)
  var DOMAIN = [
    { k: 'notplant', ind: 97.5, real: 97.8 },
    { k: 'powdery',  ind: 93.2, real: 95.4 },
    { k: 'pests',    ind: 94.7, real: 95.8 },
    { k: 'healthy',  ind: 89.2, real: 91.2 },
    { k: 'viral',    ind: 95.9, real: 89.6 },
    { k: 'graymold', ind: 92.9, real: 88.6 },
    { k: 'leafspot', ind: 88.1, real: 83.0 },
    { k: 'nutrient', ind: 87.8, real: 78.2 },
    { k: 'scorch',   ind: 78.0, real: 78.8 }
  ];

  // confusion matrix · row-normalised % · order = rows == cols
  // diagonals equal the per-class recalls exactly.
  var CM_ORDER = ['graymold','healthy','scorch','leafspot','notplant','nutrient','pests','powdery','viral'];
  var CM = {
    graymold: { graymold:89, healthy:1, scorch:0, leafspot:0, notplant:1, nutrient:0, pests:7, powdery:2, viral:0 },
    healthy:  { graymold:0, healthy:91, scorch:2, leafspot:1, notplant:0, nutrient:2, pests:1, powdery:0, viral:1 },
    scorch:   { graymold:3, healthy:8, scorch:79, leafspot:5, notplant:1, nutrient:2, pests:2, powdery:1, viral:0 },
    leafspot: { graymold:3, healthy:2, scorch:1, leafspot:83, notplant:0, nutrient:0, pests:1, powdery:1, viral:8 },
    notplant: { graymold:0, healthy:1, scorch:1, leafspot:0, notplant:98, nutrient:0, pests:0, powdery:0, viral:0 },
    nutrient: { graymold:1, healthy:9, scorch:6, leafspot:2, notplant:0, nutrient:78, pests:1, powdery:0, viral:2 },
    pests:    { graymold:0, healthy:2, scorch:0, leafspot:0, notplant:1, nutrient:0, pests:96, powdery:0, viral:0 },
    powdery:  { graymold:1, healthy:1, scorch:0, leafspot:0, notplant:0, nutrient:0, pests:2, powdery:95, viral:0 },
    viral:    { graymold:0, healthy:5, scorch:0, leafspot:2, notplant:0, nutrient:3, pests:0, powdery:1, viral:90 }
  };

  /* ---------- tooltip ---------- */
  var tip = document.createElement('div');
  tip.className = 'chart-tip';
  document.body.appendChild(tip);
  function showTip(html, x, y) {
    tip.innerHTML = html;
    tip.classList.add('on');
    var pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
    var left = x + pad, top = y - h - pad;
    if (left + w > window.innerWidth - 8) left = x - w - pad;
    if (top < 8) top = y + pad;
    tip.style.left = left + 'px';
    tip.style.top = top + 'px';
  }
  function hideTip() { tip.classList.remove('on'); }

  /* ---------- helpers ---------- */
  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  // green-scale for heatmap intensity (0..100) — a fresh, light ramp (mint ->
  // bright brand green) so even the strong-diagonal cells stay airy on the dark
  // band instead of going deep-forest dark.
  function heat(v) {
    var x = Math.max(0, Math.min(100, v)) / 100;
    // ease so most off-diagonal (small) values read as pale mint, not grey
    var e = Math.pow(x, 0.85);
    var c0 = [236, 250, 241], c1 = [47, 191, 94];
    var r = Math.round(c0[0] + (c1[0] - c0[0]) * e);
    var g = Math.round(c0[1] + (c1[1] - c0[1]) * e);
    var b = Math.round(c0[2] + (c1[2] - c0[2]) * e);
    return 'rgb(' + r + ',' + g + ',' + b + ')';
  }
  function nColor(n) { return n >= 100 ? 'var(--acc-green)' : n >= 50 ? 'var(--acc-amber)' : 'var(--acc-red)'; }

  /* ============================================================
     1 · PER-CLASS GROUPED BARS (precision / recall / F1)
     ============================================================ */
  function renderPerClass(root) {
    root.innerHTML = '';
    var legend = el('div', 'chart-legend',
      '<span><i style="background:var(--acc-blue)"></i>' + t('Точность', 'Precision') + '</span>' +
      '<span><i style="background:var(--acc-green)"></i>' + t('Полнота', 'Recall') + '</span>' +
      '<span><i style="background:var(--acc-purple)"></i>F1</span>');
    root.appendChild(legend);
    var grid = el('div', 'pc-grid');
    PERCLASS.forEach(function (d) {
      var row = el('div', 'pc-row');
      row.appendChild(el('div', 'pc-name', lbl(d.k) + ' <em>n=' + d.n + '</em>'));
      var bars = el('div', 'pc-bars');
      [['p', d.p, 'var(--acc-blue)', t('Точность','Precision')],
       ['r', d.r, 'var(--acc-green)', t('Полнота','Recall')],
       ['f', d.f, 'var(--acc-purple)', 'F1']].forEach(function (b) {
        var track = el('div', 'pc-track');
        var fill = el('div', 'pc-fill');
        fill.style.background = b[2];
        fill.setAttribute('data-w', b[1]);
        fill.appendChild(el('span', 'pc-val', b[1].toFixed(0) + '%'));
        track.appendChild(fill);
        track.addEventListener('mousemove', function (e) {
          showTip('<b>' + lbl(d.k) + '</b><br>' + b[3] + ': <b>' + b[1].toFixed(1) + '%</b><br><span class="muted">n = ' + d.n + '</span>', e.clientX, e.clientY);
        });
        track.addEventListener('mouseleave', hideTip);
        bars.appendChild(track);
      });
      row.appendChild(bars);
      grid.appendChild(row);
    });
    root.appendChild(grid);
  }

  /* ============================================================
     2 · RECALL + 95% CI (dot & whisker)
     ============================================================ */
  function renderRecallCI(root) {
    root.innerHTML = '';
    var MIN = 20, MAX = 100, span = MAX - MIN;
    function pct(v) { return ((v - MIN) / span * 100); }
    var grid = el('div', 'ci-grid');
    RECALL_CI.forEach(function (d) {
      var row = el('div', 'ci-row');
      row.appendChild(el('div', 'ci-name', lbl(d.k)));
      var plot = el('div', 'ci-plot');   // 70% threshold drawn via CSS gradient
      var whisk = el('div', 'ci-whisker');
      whisk.style.left = pct(d.lo) + '%';
      whisk.style.width = (pct(d.hi) - pct(d.lo)) + '%';
      whisk.style.background = nColor(d.n);
      var dot = el('div', 'ci-dot');
      dot.style.left = pct(d.r) + '%';
      dot.style.background = nColor(d.n);
      plot.appendChild(whisk); plot.appendChild(dot);
      plot.addEventListener('mousemove', function (e) {
        showTip('<b>' + lbl(d.k) + '</b><br>' + t('Полнота','Recall') + ': <b>' + d.r.toFixed(1) + '%</b><br>95% CI: ' + d.lo + '–' + d.hi + '%<br><span class="muted">n = ' + d.n + '</span>', e.clientX, e.clientY);
      });
      plot.addEventListener('mouseleave', hideTip);
      var num = el('div', 'ci-num', d.r.toFixed(0) + '%');
      num.style.color = nColor(d.n);
      row.appendChild(plot);
      row.appendChild(num);
      grid.appendChild(row);
    });
    root.appendChild(grid);
    // axis + threshold note
    var axis = el('div', 'ci-axis', '<span>20%</span><span>40%</span><span>60%</span><span>80%</span><span>100%</span>');
    root.appendChild(axis);
    var lg = el('div', 'chart-legend',
      '<span><i style="background:var(--acc-green)"></i>n ≥ 100</span>' +
      '<span><i style="background:var(--acc-amber)"></i>50–99</span>' +
      '<span><i style="background:var(--acc-red)"></i>n &lt; 50 ' + t('(широкий ДИ)','(wide CI)') + '</span>');
    root.appendChild(lg);
  }

  /* ============================================================
     3 · IN-DOMAIN vs REAL-WORLD (paired bars)
     ============================================================ */
  function renderDomain(root) {
    root.innerHTML = '';
    var legend = el('div', 'chart-legend',
      '<span><i style="background:#1B7E3E"></i>in-domain</span>' +
      '<span><i style="background:var(--acc-green)"></i>' + t('реальные фото','real-world') + '</span>');
    root.appendChild(legend);
    var grid = el('div', 'dm-grid');
    DOMAIN.forEach(function (d) {
      var col = el('div', 'dm-col');
      var bars = el('div', 'dm-bars');
      var gap = (d.ind - d.real);
      [['ind', d.ind, '#1B7E3E', 'in-domain'], ['real', d.real, 'var(--acc-green)', t('реальные','real')]].forEach(function (b) {
        var bar = el('div', 'dm-bar');
        bar.style.background = b[2];
        bar.setAttribute('data-h', b[1]);
        bars.appendChild(bar);
      });
      col.appendChild(bars);
      col.appendChild(el('div', 'dm-name', isNarrow() ? SHORT[d.k] : lbl(d.k)));
      col.addEventListener('mousemove', function (e) {
        showTip('<b>' + lbl(d.k) + '</b><br>in-domain: <b>' + d.ind.toFixed(0) + '%</b><br>' + t('реальные','real-world') + ': <b>' + d.real.toFixed(1) + '%</b><br><span class="muted">' + t('разрыв','gap') + ' ' + (gap >= 0 ? '−' : '+') + Math.abs(gap).toFixed(1) + ' ' + t('п.п.','pp') + '</span>', e.clientX, e.clientY);
      });
      col.addEventListener('mouseleave', hideTip);
      grid.appendChild(col);
    });
    root.appendChild(grid);
  }

  /* ============================================================
     4 · CONFUSION MATRIX (heatmap)
     ============================================================ */
  function renderConfusion(root) {
    root.innerHTML = '';
    var narrow = isNarrow();
    var hd = function (k) { return narrow ? SHORT[k] : lbl(k); };
    var wrap = el('div', 'cm-wrap' + (narrow ? ' cm-sm' : ''));
    var grid = el('div', 'cm-grid' + (narrow ? ' cm-sm' : ''));
    grid.style.gridTemplateColumns = 'var(--cm-lbl) repeat(' + CM_ORDER.length + ', minmax(0,1fr))';
    // corner
    grid.appendChild(el('div', 'cm-corner', '<span>' + (narrow ? t('и╲п','t╲p') : t('истина ╲ предск.','true ╲ pred')) + '</span>'));
    // column headers
    CM_ORDER.forEach(function (c) {
      grid.appendChild(el('div', 'cm-colh', '<span>' + hd(c) + '</span>'));
    });
    CM_ORDER.forEach(function (rk) {
      grid.appendChild(el('div', 'cm-rowh', hd(rk)));
      CM_ORDER.forEach(function (ck) {
        var v = CM[rk][ck] || 0;
        var cell = el('div', 'cm-cell' + (rk === ck ? ' diag' : ''), v > 0 ? v : '');
        cell.style.background = heat(v);
        // Dark ink on every cell now that the ramp stays light — keeps the
        // matrix airy instead of dark-green cells with white text.
        cell.setAttribute('data-v', v);
        cell.addEventListener('mousemove', function (e) {
          showTip('<b>' + lbl(rk) + '</b> → ' + lbl(ck) + '<br>' + t('доля строки','row share') + ': <b>' + v + '%</b>' + (rk === ck ? '<br><span class="muted">' + t('верный диагноз','correct') + '</span>' : ''), e.clientX, e.clientY);
        });
        cell.addEventListener('mouseleave', hideTip);
        grid.appendChild(cell);
      });
    });
    wrap.appendChild(grid);
    root.appendChild(wrap);
    // scale legend
    var scale = el('div', 'cm-scale');
    scale.innerHTML = '<span>0%</span><div class="cm-bar"></div><span>100%</span>';
    root.appendChild(scale);
    // on narrow screens spell out the short codes
    if (narrow) {
      var key = el('div', 'cm-key');
      key.innerHTML = CM_ORDER.map(function (k) {
        return '<span><b>' + SHORT[k] + '</b>' + lbl(k) + '</span>';
      }).join('');
      root.appendChild(key);
    }
  }

  /* ============================================================
     5 · CONFORMAL COVERAGE (gauge + stats)
     ============================================================ */
  function renderConformal(root) {
    root.innerHTML = '';
    var box = el('div', 'cf-box');
    // radial gauge — 98.8% coverage
    var pct = 98.8;
    var gauge = el('div', 'cf-gauge');
    gauge.innerHTML =
      '<svg viewBox="0 0 120 120">' +
        '<circle class="cf-bg" cx="60" cy="60" r="52"></circle>' +
        '<circle class="cf-fill" cx="60" cy="60" r="52" data-pct="' + pct + '"></circle>' +
      '</svg>' +
      '<div class="cf-center"><div class="cf-pct">98.8%</div><div class="cf-cap">' + t('покрытие','coverage') + '</div></div>';
    box.appendChild(gauge);
    var stats = el('div', 'cf-stats');
    [[t('Цель','Target'), '≥ 90%', 'ok'],
     [t('Средний размер','Avg. set size'), '1.87 ' + t('класса','classes'), ''],
     [t('Метод','Method'), 'RAPS · α=0.2', ''],
     [t('Калибровка','Calibration'), '999 ' + t('фото','photos'), ''],
     [t('Температура','Temperature'), 'T = 0.849', '']].forEach(function (s) {
      var r = el('div', 'cf-row');
      r.innerHTML = '<span class="cf-k">' + s[0] + '</span><span class="cf-v ' + s[2] + '">' + s[1] + '</span>';
      stats.appendChild(r);
    });
    box.appendChild(stats);
    root.appendChild(box);
  }

  /* ============================================================
     animation on first view + re-render on language change
     ============================================================ */
  var charts = [
    { sel: '#chart-perclass', fn: renderPerClass },
    { sel: '#chart-recall',   fn: renderRecallCI },
    { sel: '#chart-domain',   fn: renderDomain },
    { sel: '#chart-confusion',fn: renderConfusion },
    { sel: '#chart-conformal',fn: renderConformal }
  ];

  function animate(root) {
    // bars grow
    root.querySelectorAll('.pc-fill').forEach(function (f) {
      requestAnimationFrame(function () { f.style.width = f.getAttribute('data-w') + '%'; });
    });
    root.querySelectorAll('.dm-bar').forEach(function (b) {
      requestAnimationFrame(function () { b.style.height = b.getAttribute('data-h') + '%'; });
    });
    var fill = root.querySelector('.cf-fill');
    if (fill) {
      var p = parseFloat(fill.getAttribute('data-pct'));
      var c = 2 * Math.PI * 52;
      fill.style.strokeDasharray = c;
      fill.style.strokeDashoffset = c;
      requestAnimationFrame(function () { fill.style.strokeDashoffset = c * (1 - p / 100); });
    }
  }

  function build(root, fn) { fn(root); }

  function init() {
    charts.forEach(function (c) {
      var root = document.querySelector(c.sel);
      if (root) build(root, c.fn);
    });
    if ('IntersectionObserver' in window) {
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) { animate(en.target); io.unobserve(en.target); }
        });
      }, { threshold: 0.25 });
      charts.forEach(function (c) { var r = document.querySelector(c.sel); if (r) io.observe(r); });
    } else {
      charts.forEach(function (c) { var r = document.querySelector(c.sel); if (r) animate(r); });
    }
  }

  // re-render charts when the language toggle fires
  document.addEventListener('pd:lang', function () {
    charts.forEach(function (c) {
      var root = document.querySelector(c.sel);
      if (root) { c.fn(root); animate(root); }
    });
  });

  // re-render the confusion matrix when crossing the compact-layout breakpoint
  var lastNarrow = isNarrow();
  var rzTimer;
  window.addEventListener('resize', function () {
    clearTimeout(rzTimer);
    rzTimer = setTimeout(function () {
      var n = isNarrow();
      if (n === lastNarrow) return;
      lastNarrow = n;
      [['#chart-confusion', renderConfusion], ['#chart-domain', renderDomain]].forEach(function (c) {
        var root = document.querySelector(c[0]);
        if (root) { c[1](root); animate(root); }
      });
    }, 180);
  }, { passive: true });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
