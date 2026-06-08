/* ============================================================
   PlantDiseases site — interactions
   ============================================================ */
(function () {
  'use strict';

  /* ---------- sticky nav ---------- */
  var nav = document.getElementById('nav');
  function onScroll() {
    if (window.scrollY > 40) nav.classList.add('scrolled');
    else nav.classList.remove('scrolled');
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  /* ---------- mobile menu ---------- */
  var burger = document.getElementById('burger');
  var navLinks = document.getElementById('navLinks');
  burger.addEventListener('click', function () {
    navLinks.classList.toggle('open');
  });
  navLinks.querySelectorAll('a').forEach(function (a) {
    a.addEventListener('click', function () { navLinks.classList.remove('open'); });
  });

  /* ---------- language toggle (RU / EN) ---------- */
  var nodes = document.querySelectorAll('[data-ru]');
  var buttons = document.querySelectorAll('.lang-toggle button');

  function applyLang(lang) {
    nodes.forEach(function (el) {
      var val = el.getAttribute('data-' + lang);
      if (val == null) return;
      // these strings are author-controlled (this file + index.html), safe to inject as markup
      el.innerHTML = val;
    });
    document.documentElement.setAttribute('lang', lang);
    buttons.forEach(function (b) {
      b.classList.toggle('active', b.getAttribute('data-lang') === lang);
    });
    try { localStorage.setItem('pd-lang', lang); } catch (e) {}
    document.dispatchEvent(new CustomEvent('pd:lang', { detail: { lang: lang } }));
  }

  buttons.forEach(function (b) {
    b.addEventListener('click', function () { applyLang(b.getAttribute('data-lang')); });
  });

  var saved = 'ru';
  try { saved = localStorage.getItem('pd-lang') || 'ru'; } catch (e) {}
  if (saved === 'en') applyLang('en');

  /* ---------- reveal on scroll ---------- */
  var reveals = document.querySelectorAll('.reveal');
  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) {
          en.target.classList.add('in');
          io.unobserve(en.target);
        }
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -8% 0px' });
    reveals.forEach(function (el) { io.observe(el); });
  } else {
    reveals.forEach(function (el) { el.classList.add('in'); });
  }

  /* ---------- lightbox for charts ---------- */
  var lb = document.getElementById('lightbox');
  var lbImg = document.getElementById('lbImg');
  var lbClose = document.getElementById('lbClose');

  document.querySelectorAll('[data-zoom] img').forEach(function (img) {
    img.style.cursor = 'zoom-in';
    img.addEventListener('click', function () {
      lbImg.src = img.src;
      lbImg.alt = img.alt;
      lb.classList.add('open');
    });
  });
  function closeLb() { lb.classList.remove('open'); lbImg.src = ''; }
  lb.addEventListener('click', closeLb);
  lbClose.addEventListener('click', closeLb);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeLb();
  });
})();

/* ============================================================
   Ambient interactions: scroll progress · count-up · parallax
   ============================================================ */
(function () {
  'use strict';
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---- scroll progress bar ---- */
  var bar = document.getElementById('progress');
  if (bar) {
    var ticking = false;
    function upd() {
      var h = document.documentElement;
      var max = h.scrollHeight - h.clientHeight;
      bar.style.width = (max > 0 ? (h.scrollTop / max) * 100 : 0) + '%';
      ticking = false;
    }
    window.addEventListener('scroll', function () {
      if (!ticking) { requestAnimationFrame(upd); ticking = true; }
    }, { passive: true });
    upd();
  }

  /* ---- count-up numbers (keeps prefix/suffix, restores rich markup) ---- */
  function countUp(el) {
    if (el.dataset.counted) return;
    el.dataset.counted = '1';
    var html = el.innerHTML;
    var text = el.textContent.trim();
    var m = text.match(/^(\D*)(\d[\d.,]*)(.*)$/);
    if (!m) return;                              // nothing numeric — leave as-is
    if (reduce) return;
    var prefix = m[1], numStr = m[2].replace(',', '.'), suffix = m[3];
    var target = parseFloat(numStr);
    var decimals = (numStr.split('.')[1] || '').length;
    var dur = 1100, start;
    function tick(now) {
      if (!start) start = now;
      var p = Math.min(1, (now - start) / dur);
      var e = 1 - Math.pow(1 - p, 3);            // easeOutCubic
      el.textContent = prefix + (target * e).toFixed(decimals) + suffix;
      if (p < 1) requestAnimationFrame(tick);
      else el.innerHTML = html;                  // restore original (units, spans)
    }
    requestAnimationFrame(tick);
  }

  var counters = document.querySelectorAll('.hero-stats .v, .kpi .kv');
  if ('IntersectionObserver' in window) {
    var cio = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { countUp(en.target); cio.unobserve(en.target); }
      });
    }, { threshold: 0.6 });
    counters.forEach(function (c) { cio.observe(c); });
  }

  /* ---- gentle cursor parallax on the hero phones ---- */
  var hv = document.querySelector('.hero-visual');
  if (hv && !reduce && window.matchMedia('(pointer:fine)').matches) {
    var hero = document.querySelector('.hero');
    hero.addEventListener('mousemove', function (e) {
      var r = hero.getBoundingClientRect();
      var dx = (e.clientX - r.left) / r.width - 0.5;
      var dy = (e.clientY - r.top) / r.height - 0.5;
      hv.style.transform = 'translate(' + (dx * 18).toFixed(1) + 'px,' + (dy * 14).toFixed(1) + 'px)';
    });
    hero.addEventListener('mouseleave', function () { hv.style.transform = ''; });
  }
})();
