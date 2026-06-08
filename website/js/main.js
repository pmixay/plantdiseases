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
