/* ============================================================
   PlantDiseases — native, interactive metric charts
   Hand-built SVG / CSS visualisations (no image assets, no libs).
   Data: PlantVision 2.4.0 gamma · evaluation on val_realworld.
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

  /* ---------- data (from the evaluation report) ---------- */
  // precision / recall / f1 / n  ·  per-class on real-world photos
  var PERCLASS = [
    { k: 'notplant', p: 98.4, r: 97.5, f: 97.9, n: 552 },
    { k: 'powdery',  p: 100,  r: 95.5, f: 97.7, n: 67 },
    { k: 'pests',    p: 93.0, r: 95.1, f: 94.0, n: 265 },
    { k: 'healthy',  p: 96.4, r: 87.2, f: 91.5, n: 576 },
    { k: 'leafspot', p: 91.5, r: 84.7, f: 88.0, n: 216 },
    { k: 'viral',    p: 82.8, r: 87.0, f: 84.8, n: 138 },
    { k: 'graymold', p: 66.2, r: 84.9, f: 74.4, n: 53 },
    { k: 'nutrient', p: 43.2, r: 76.0, f: 55.1, n: 25 },
    { k: 'scorch',   p: 37.7, r: 64.4, f: 47.5, n: 45 }
  ];

  // recall + 95% bootstrap CI (low/high) · sorted desc by recall
  var RECALL_CI = [
    { k: 'notplant', r: 97.5, lo: 96, hi: 99,  n: 552 },
    { k: 'powdery',  r: 95.5, lo: 90, hi: 100, n: 67 },
    { k: 'pests',    r: 95.1, lo: 92, hi: 98,  n: 265 },
    { k: 'viral',    r: 87.0, lo: 81, hi: 93,  n: 138 },
    { k: 'healthy',  r: 87.2, lo: 84, hi: 90,  n: 576 },
    { k: 'graymold', r: 84.9, lo: 75, hi: 94,  n: 53 },
    { k: 'leafspot', r: 84.7, lo: 80, hi: 89,  n: 216 },
    { k: 'nutrient', r: 76.0, lo: 58, hi: 92,  n: 25 },
    { k: 'scorch',   r: 64.4, lo: 50, hi: 78,  n: 45 }
  ];

  // in-domain recall vs real-world recall (in-domain read from the report figure)
  var DOMAIN = [
    { k: 'notplant', ind: 98.0, real: 97.5 },
    { k: 'powdery',  ind: 97.5, real: 95.5 },
    { k: 'pests',    ind: 98.0, real: 95.1 },
    { k: 'healthy',  ind: 92.0, real: 87.2 },
    { k: 'viral',    ind: 96.5, real: 87.0 },
    { k: 'graymold', ind: 91.5, real: 84.9 },
    { k: 'leafspot', ind: 81.5, real: 84.7 },
    { k: 'nutrient', ind: 79.0, real: 76.0 },
    { k: 'scorch',   ind: 88.0, real: 64.4 }
  ];

  // confusion matrix · row-normalised % · order = rows == cols
  // diagonals equal the per-class recalls exactly.
  var CM_ORDER = ['graymold','healthy','scorch','leafspot','notplant','nutrient','pests','powdery','viral'];
  var CM = {
    graymold: { graymold:85, healthy:2, scorch:2, leafspot:2, notplant:5, nutrient:0, pests:0, powdery:4, viral:0 },
    healthy:  { graymold:2, healthy:87, scorch:4, leafspot:1, notplant:0, nutrient:1, pests:1, powdery:1, viral:3 },
    scorch:   { graymold:1, healthy:7, scorch:64, leafspot:2, notplant:4, nutrient:18, pests:2, powdery:0, viral:2 },
    leafspot: { graymold:2, healthy:1, scorch:2, leafspot:85, notplant:1, nutrient:1, pests:2, powdery:0, viral:4 },
    notplant: { graymold:0, healthy:0, scorch:0, leafspot:1, notplant:97, nutrient:0, pests:1, powdery:1, viral:0 },
    nutrient: { graymold:0, healthy:16, scorch:6, leafspot:0, notplant:0, nutrient:76, pests:0, powdery:0, viral:2 },
    pests:    { graymold:1, healthy:1, scorch:0, leafspot:1, notplant:1, nutrient:0, pests:95, powdery:1, viral:0 },
    powdery:  { graymold:1, healthy:1, scorch:0, leafspot:0, notplant:0, nutrient:0, pests:1, powdery:96, viral:0 },
    viral:    { graymold:1, healthy:2, scorch:0, leafspot:4, notplant:4, nutrient:5, pests:0, powdery:0, viral:87 }
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
  // green-scale for heatmap intensity (0..100)
  function heat(v) {
    var x = Math.max(0, Math.min(100, v)) / 100;
    // light mint -> deep brand green
    var c0 = [233, 246, 236], c1 = [14, 110, 56];
    var r = Math.round(c0[0] + (c1[0] - c0[0]) * x);
    var g = Math.round(c0[1] + (c1[1] - c0[1]) * x);
    var b = Math.round(c0[2] + (c1[2] - c0[2]) * x);
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
      col.appendChild(el('div', 'dm-name', lbl(d.k)));
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
    var wrap = el('div', 'cm-wrap');
    var grid = el('div', 'cm-grid');
    grid.style.gridTemplateColumns = 'var(--cm-lbl) repeat(' + CM_ORDER.length + ', 1fr)';
    // corner
    grid.appendChild(el('div', 'cm-corner', '<span>' + t('истина ╲ предск.','true ╲ pred') + '</span>'));
    // column headers
    CM_ORDER.forEach(function (c) {
      grid.appendChild(el('div', 'cm-colh', '<span>' + lbl(c) + '</span>'));
    });
    CM_ORDER.forEach(function (rk) {
      grid.appendChild(el('div', 'cm-rowh', lbl(rk)));
      CM_ORDER.forEach(function (ck) {
        var v = CM[rk][ck] || 0;
        var cell = el('div', 'cm-cell' + (rk === ck ? ' diag' : ''), v > 0 ? v : '');
        cell.style.background = heat(v);
        if (v >= 45) cell.style.color = '#fff';
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
  }

  /* ============================================================
     5 · CONFORMAL COVERAGE (gauge + stats)
     ============================================================ */
  function renderConformal(root) {
    root.innerHTML = '';
    var box = el('div', 'cf-box');
    // radial gauge — 96.6% coverage
    var pct = 96.6;
    var gauge = el('div', 'cf-gauge');
    gauge.innerHTML =
      '<svg viewBox="0 0 120 120">' +
        '<circle class="cf-bg" cx="60" cy="60" r="52"></circle>' +
        '<circle class="cf-fill" cx="60" cy="60" r="52" data-pct="' + pct + '"></circle>' +
      '</svg>' +
      '<div class="cf-center"><div class="cf-pct">96.6%</div><div class="cf-cap">' + t('покрытие','coverage') + '</div></div>';
    box.appendChild(gauge);
    var stats = el('div', 'cf-stats');
    [[t('Цель','Target'), '≥ 90%', 'ok'],
     [t('Средний размер','Avg. set size'), '1.86 ' + t('класса','classes'), ''],
     [t('Метод','Method'), 'RAPS · α=0.2', ''],
     [t('Калибровка','Calibration'), '940 ' + t('фото','photos'), ''],
     [t('Температура','Temperature'), 'T = 0.791', '']].forEach(function (s) {
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

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
