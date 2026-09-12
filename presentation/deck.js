/* APEX FLOW deck engine — keyboard/click fragments, scaling, overview, notes.
   No external dependencies so the file works offline from disk or a static host. */
(function () {
  /* ── FILL THESE THREE IN, THEN PRESENT ───────────────────────────────────
     Everything else in the deck is generated from the repository; these are
     the only human details. Anything left empty stays marked as a placeholder
     on the title slide so you cannot accidentally present with <your name> on
     the projector. You can also skip this block and edit the three spans in
     index.html directly (search for data-fill).                                              */
  const META = {
    name:     '',                       // e.g. 'Nandan Sharma'
    course:   '',                       // e.g. 'B.Tech CSE · Final-year project · Thapar University'
    reviewer: ''                        // e.g. 'Prof. <name>'
  };

  function applyMeta() {
    let unfilled = 0;
    document.querySelectorAll('[data-fill]').forEach(el => {
      const v = META[el.getAttribute('data-fill')];
      if (v && String(v).trim()) el.textContent = String(v).trim();
      if (/</.test(el.textContent)) { el.classList.add('pending'); unfilled++; }
    });
    const title = document.querySelector('.slide[data-title="Title"]');
    if (unfilled && title) {
      const warn = document.createElement('div');
      warn.className = 'metahint';
      warn.textContent = '⚠ ' + unfilled + ' placeholder' + (unfilled > 1 ? 's' : '') +
        ' still on this slide — set META in deck.js (line 10) before you present.';
      title.appendChild(warn);
    }
  }
  applyMeta();

  const stage = document.getElementById('stage');
  const wrap  = document.getElementById('stage-wrap');
  const slides = Array.from(stage.querySelectorAll('.slide'));
  const bar = document.querySelector('#bar i');
  const pageno = document.getElementById('pageno');
  const notes = document.getElementById('notes');
  let i = 0, f = 0;

  /* ── fit 1280×720 stage to any viewport (projector-safe) ───────────── */
  function fit() {
    const s = Math.min(wrap.clientWidth / 1280, wrap.clientHeight / 720);
    stage.style.transform = 'scale(' + s.toFixed(4) + ')';
  }
  addEventListener('resize', fit); fit();

  const frags = (el) => Array.from(el.querySelectorAll('.frag'));

  /* ── auto-fit: guarantee no slide ever clips at 1280×720 ─────────────────
     Children are wrapped in a .fit flex column; absolutely positioned
     decorations (.band rules) stay outside. If the content is taller than the
     stage we binary-search a scale factor, reflowing at a wider box so text
     rewraps instead of merely shrinking. */
  function autoFit() {
    const pad = (slide) => {
      const c = getComputedStyle(slide);
      return slide.clientHeight - parseFloat(c.paddingTop) - parseFloat(c.paddingBottom);
    };
    slides.forEach((slide) => {
      let box = slide.querySelector(':scope > .fit');
      if (!box) {
        const kids = Array.from(slide.children);
        const outside = kids.filter((k) => getComputedStyle(k).position === 'absolute');
        box = document.createElement('div');
        box.className = 'fit';
        kids.forEach((k) => { if (outside.indexOf(k) === -1) box.appendChild(k); });
        slide.appendChild(box);
      }
      const avail = pad(slide);
      box.style.width = '100%';
      box.style.transform = 'none';
      slide.style.setProperty('--pz', '1');                   // print stylesheet reads this
      if (box.scrollHeight <= avail + 1) return;              // already fits
      let best = 1;
      for (let it = 0; it < 8; it++) {
        box.style.width = (100 / best).toFixed(3) + '%';     // reflow wider, then scale
        const need = box.scrollHeight * best;                 // transform is excluded from scrollHeight
        if (need <= avail + 0.5) break;
        best = Math.max(0.72, best * (avail / need) - 0.004);
      }
      box.style.transform = 'scale(' + best + ')';
      box.style.transformOrigin = 'top left';
      box.dataset.scale = best.toFixed(3);
      slide.style.setProperty('--pz', best.toFixed(4));      // same factor on screen and on paper
    });
  }
  autoFit();
  addEventListener('load', autoFit);   // re-measure once fonts settle

  function render() {
    slides.forEach((el, n) => {
      const fr = frags(el);
      if (n < i)  { el.classList.add('active','done'); fr.forEach(x => x.classList.add('on')); el.classList.remove('prev'); }
      else if (n === i) {
        el.classList.add('active'); el.classList.remove('done','prev');
        fr.forEach((x, k) => x.classList.toggle('on', k < f));
      } else {
        el.classList.remove('active','done');
        fr.forEach(x => x.classList.remove('on'));
      }
    });
    const total = slides.length;
    bar.style.width = (100 * (i + (f / Math.max(1, frags(slides[i]).length || 1))) / total).toFixed(2) + '%';
    pageno.textContent = String(i + 1).padStart(2, '0') + ' / ' + String(total).padStart(2, '0');
    const note = slides[i].getAttribute('data-notes');
    notes.innerHTML = note ? '<h5>Speaker notes — slide ' + (i + 1) + '</h5>' + note : '<h5>Speaker notes</h5><span style="color:#7f9cbb">none for this slide</span>';
    pageno.dataset.hits = String(frags(slides[i]).length);
    document.title = slides[i].dataset.title + ' — APEX FLOW';
  }

  function next() { if (f < frags(slides[i]).length) { f++; render(); } else if (i < slides.length - 1) { i++; f = 0; render(); } }
  function prev() { if (f > 0) { f--; render(); } else if (i > 0) { i--; f = frags(slides[i]).length; render(); } }
  function go(n, fr) { i = Math.max(0, Math.min(slides.length - 1, n)); f = fr || 0; render(); }

  /* ── keyboard ────────────────────────────────────────────────────────── */
  addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { overlay(false); return; }
    if (e.key === 'o' || e.key === 'O') { overlay(); return; }
    if (e.key === 'n' || e.key === 'N') { notes.classList.toggle('on'); return; }
    if (e.key === 'a' || e.key === 'A') { slides[i].classList.toggle('no-anim'); return; }
    if (e.key === 'f' || e.key === 'F') { toggleFs(); return; }
    if (e.key === '?' ) { document.body.classList.toggle('showhint'); return; }
    const jump = parseInt(e.key, 10);
    if (!isNaN(jump) && jump > 0 && e.altKey) { go(jump - 1); return; }
    if (['ArrowRight',' ','PageDown','Enter','j'].includes(e.key)) { e.preventDefault(); next(); }
    else if (['ArrowLeft','PageUp','Backspace','k'].includes(e.key)) { e.preventDefault(); prev(); }
    else if (e.key === 'Home') go(0);
    else if (e.key === 'End') go(slides.length - 1);
  });

  /* ── click / tap zones: right 2/3 forward, left 1/3 back ────────────── */
  stage.addEventListener('click', (e) => {
    if (e.target.closest('a,button,input,select,textarea')) return;
    (e.clientX < wrap.clientWidth * 0.28) ? prev() : next();
  });
  let tx = null;
  addEventListener('touchstart', (e) => { tx = e.changedTouches[0].clientX; }, { passive: true });
  addEventListener('touchend', (e) => {
    if (tx === null) return;
    const dx = e.changedTouches[0].clientX - tx; tx = null;
    if (Math.abs(dx) > 42) (dx < 0 ? next() : prev());
  }, { passive: true });

  /* ── wheel (debounced) ───────────────────────────────────────────────── */
  let lock = 0;
  addEventListener('wheel', (e) => {
    const now = Date.now(); if (now - lock < 560) return;
    if (Math.abs(e.deltaY) < 14) return;
    lock = now; (e.deltaY > 0 ? next() : prev());
  }, { passive: true });

  /* ── overview ────────────────────────────────────────────────────────── */
  const ov = document.getElementById('overlay');
  function overlay(force) {
    const on = force === undefined ? !ov.classList.contains('on') : force;
    ov.classList.toggle('on', on);
    if (on) {
      const grid = document.getElementById('ovgrid');
      grid.innerHTML = slides.map((s, n) =>
        '<button data-n="' + n + '"><span class="n">SLIDE ' + String(n + 1).padStart(2, '0') + '</span><span class="t2">' +
        (s.dataset.title || s.querySelector('h2,h1')?.textContent.trim().slice(0, 60) || '') + '</span></button>').join('');
      grid.querySelectorAll('button').forEach(b => b.onclick = () => { go(+b.dataset.n); overlay(false); });
    }
  }

  function toggleFs() {
    if (!document.fullscreenElement) document.documentElement.requestFullscreen?.();
    else document.exitFullscreen?.();
  }

  /* deep link: #7 starts on slide 7 (fragments revealed) */
  const h = parseInt((location.hash || '').replace('#', ''), 10);
  if (!isNaN(h) && h >= 1 && h <= slides.length) { i = h - 1; f = frags(slides[i]).length; }

  addEventListener('hashchange', () => {
    const n = parseInt((location.hash || '').replace('#', ''), 10);
    if (!isNaN(n)) go(n - 1, frags(slides[n - 1]).length);
  });

  window.addEventListener('beforeprint', () => slides.forEach(s => s.classList.add('no-anim')));
  render();
  setTimeout(() => document.body.classList.add('showhint'), 700);
  setTimeout(() => document.body.classList.remove('showhint'), 5200);
})();
