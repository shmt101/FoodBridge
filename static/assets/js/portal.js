/* FoodBridge portal helpers: video facades, dialogs, countdowns, auth-page interactions. */
(function () {
  'use strict';

  // ---- street-address autocomplete: suggests real addresses as you type ----
  (function () {
    var fields = document.querySelectorAll('input[name="address"], input[name="pickup_address"]');
    if (!fields.length) return;

    fields.forEach(function (input) {
      var wrap = document.createElement('div');
      wrap.className = 'addr-suggest-wrap';
      input.parentNode.insertBefore(wrap, input);
      wrap.appendChild(input);

      var list = document.createElement('div');
      list.className = 'addr-suggest-list';
      list.hidden = true;
      wrap.appendChild(list);

      var timer = null, controller = null, items = [], active = -1;

      function close() {
        list.hidden = true;
        list.innerHTML = '';
        items = [];
        active = -1;
      }

      function render(results) {
        items = results;
        active = -1;
        if (!results.length) { close(); return; }
        list.innerHTML = results.map(function (r, i) {
          return '<button type="button" class="addr-suggest-item" data-i="' + i + '">' +
                 '<i class="bi bi-geo-alt"></i><span>' + r.label.replace(/</g, '&lt;') + '</span></button>';
        }).join('');
        list.hidden = false;
      }

      function choose(i) {
        if (!items[i]) return;
        input.value = items[i].label;
        close();
        input.dispatchEvent(new Event('change', { bubbles: true }));
      }

      input.addEventListener('input', function () {
        var q = input.value.trim();
        clearTimeout(timer);
        if (q.length < 3) { close(); return; }
        timer = setTimeout(function () {
          if (controller) controller.abort();
          controller = new AbortController();
          fetch('/accounts/address-suggest/?q=' + encodeURIComponent(q), { signal: controller.signal })
            .then(function (r) { return r.ok ? r.json() : { results: [] }; })
            .then(function (data) { render(data.results || []); })
            .catch(function () {});
        }, 350);
      });

      list.addEventListener('click', function (e) {
        var btn = e.target.closest('.addr-suggest-item');
        if (btn) choose(parseInt(btn.dataset.i, 10));
      });

      input.addEventListener('keydown', function (e) {
        if (list.hidden) return;
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          active = Math.min(active + 1, items.length - 1);
          updateActive();
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          active = Math.max(active - 1, 0);
          updateActive();
        } else if (e.key === 'Enter' && active >= 0) {
          e.preventDefault();
          choose(active);
        } else if (e.key === 'Escape') {
          close();
        }
      });

      function updateActive() {
        list.querySelectorAll('.addr-suggest-item').forEach(function (el, i) {
          el.classList.toggle('active', i === active);
        });
      }

      document.addEventListener('click', function (e) {
        if (!wrap.contains(e.target)) close();
      });
    });
  })();

  // ---- dashboard tabs: [data-dash-tab] buttons show/hide matching [data-dash-panel] ----
  document.querySelectorAll('.dash-tabs').forEach(function (tabs) {
    tabs.addEventListener('click', function (e) {
      var btn = e.target.closest('[data-dash-tab]');
      if (!btn) return;
      var name = btn.getAttribute('data-dash-tab');
      tabs.querySelectorAll('[data-dash-tab]').forEach(function (b) {
        var on = b === btn;
        b.classList.toggle('active', on);
        b.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      document.querySelectorAll('[data-dash-panel]').forEach(function (p) {
        p.hidden = p.getAttribute('data-dash-panel') !== name;
      });
    });
  });

  // ---- mobile nav toggle ----
  var navToggle = document.querySelector('[data-nav-toggle]');
  var navLinks = document.getElementById('navLinks');
  if (navToggle && navLinks) {
    navToggle.addEventListener('click', function () {
      var open = navLinks.classList.toggle('open');
      navToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      navToggle.querySelector('i').className = open ? 'bi bi-x-lg' : 'bi bi-list';
    });
    navLinks.querySelectorAll('a.nav-link').forEach(function (a) {
      a.addEventListener('click', function () {
        navLinks.classList.remove('open');
        navToggle.setAttribute('aria-expanded', 'false');
        navToggle.querySelector('i').className = 'bi bi-list';
      });
    });
  }

  // ---- YouTube click-to-load facade (no third-party request until the user presses play) ----
  function loadVideo(btn) {
    var id = btn.getAttribute('data-yt');
    if (!id || btn.querySelector('iframe')) return;
    var frame = document.createElement('iframe');
    frame.src = 'https://www.youtube-nocookie.com/embed/' + encodeURIComponent(id) + '?autoplay=1&rel=0&modestbranding=1';
    frame.title = btn.getAttribute('aria-label') || 'Video';
    frame.allow = 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; fullscreen';
    frame.setAttribute('allowfullscreen', '');
    btn.innerHTML = '';
    btn.appendChild(frame);
    btn.classList.add('playing');
  }
  document.addEventListener('click', function (e) {
    var facade = e.target.closest('.yt-facade');
    if (facade && !facade.classList.contains('playing')) { e.preventDefault(); loadVideo(facade); }
  });

  // ---- dialogs ----
  function closeOnBackdrop(dlg) {
    dlg.addEventListener('click', function (e) { if (e.target === dlg) dlg.close(); });
  }
  document.querySelectorAll('dialog.fb-dialog').forEach(closeOnBackdrop);
  document.addEventListener('click', function (e) {
    var closer = e.target.closest('[data-close-dialog]');
    if (closer) { var d = closer.closest('dialog'); if (d) d.close(); }
  });

  // Video modal: <button data-video-modal="ID">
  document.addEventListener('click', function (e) {
    var trigger = e.target.closest('[data-video-modal]');
    if (!trigger) return;
    var dlg = document.getElementById('videoDialog');
    if (!dlg || typeof dlg.showModal !== 'function') { window.open('https://www.youtube.com/watch?v=' + trigger.getAttribute('data-video-modal'), '_blank', 'noopener'); return; }
    var holder = dlg.querySelector('.yt-facade');
    holder.setAttribute('data-yt', trigger.getAttribute('data-video-modal'));
    holder.classList.remove('playing');
    holder.innerHTML = '';
    var title = trigger.getAttribute('data-video-title') || 'Video';
    dlg.querySelector('.dlg-title').textContent = title;
    holder.setAttribute('aria-label', 'Play video: ' + title);
    dlg.showModal();
    loadVideo(holder);
  });
  var vd = document.getElementById('videoDialog');
  if (vd) vd.addEventListener('close', function () { var h = vd.querySelector('.yt-facade'); if (h) { h.innerHTML = ''; h.classList.remove('playing'); } });

  // Reason dialog (cancel / release / withdraw): <button data-reason-open data-donation=".." data-action="..">
  var rd = document.getElementById('reasonDialog');
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-reason-open]');
    if (!btn || !rd) return;
    rd.querySelector('[name=donation_id]').value = btn.getAttribute('data-donation');
    var act = rd.querySelector('[name=action]');
    if (act) act.value = btn.getAttribute('data-action') || '';
    rd.querySelector('.dlg-title').textContent = btn.getAttribute('data-title') || 'Are you sure?';
    rd.querySelector('.dlg-intro').textContent = btn.getAttribute('data-intro') || '';
    rd.querySelector('[type=submit]').textContent = btn.getAttribute('data-confirm') || 'Confirm';
    rd.querySelector('select').value = '';
    rd.showModal();
  });

  // ---- countdowns: <span class="countdown" data-expires="ISO"> ----
  function fmt(ms) {
    var s = Math.floor(ms / 1000);
    if (s <= 0) return null;
    var d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    if (d > 0) return d + 'd ' + h + 'h';
    if (h > 0) return h + 'h ' + m + 'm';
    if (m > 0) return m + ' min';
    return s + 's';
  }
  function tick() {
    document.querySelectorAll('.countdown[data-expires]').forEach(function (el) {
      var left = new Date(el.getAttribute('data-expires')).getTime() - Date.now();
      var txt = fmt(left);
      if (txt === null) { el.textContent = el.getAttribute('data-done') || 'expired'; el.classList.add('lapsed'); el.classList.remove('urgent'); return; }
      el.textContent = txt + ' left';
      el.classList.toggle('urgent', left < 30 * 60 * 1000);
    });
  }
  if (document.querySelector('.countdown')) { tick(); setInterval(tick, 15000); }

  // ---- password show/hide + strength ----
  document.querySelectorAll('[data-toggle-password]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var input = document.getElementById(btn.getAttribute('data-toggle-password'));
      if (!input) return;
      var show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      btn.querySelector('i').className = show ? 'bi bi-eye-slash' : 'bi bi-eye';
    });
  });
  var pw = document.querySelector('[data-strength]');
  if (pw) {
    var bar = document.querySelector('.strength > i'), label = document.querySelector('.strength-label');
    pw.addEventListener('input', function () {
      var v = pw.value, score = 0;
      if (v.length >= 8) score++; if (v.length >= 12) score++;
      if (/[A-Z]/.test(v) && /[a-z]/.test(v)) score++;
      if (/\d/.test(v)) score++; if (/[^A-Za-z0-9]/.test(v)) score++;
      var names = ['Too short', 'Weak', 'Fair', 'Good', 'Strong', 'Excellent'];
      var colors = ['#ef4444', '#ef4444', '#f59e0b', '#eab308', '#22c55e', '#16a34a'];
      if (!v) { bar.style.width = '0'; label.textContent = ''; return; }
      bar.style.width = (score / 5 * 100) + '%';
      bar.style.background = colors[score];
      label.textContent = 'Password strength: ' + names[score];
    });
  }

  // ---- role tabs on the auth aside + role cards on sign-up ----
  function activateRole(role) {
    document.querySelectorAll('[data-role-tab]').forEach(function (b) { b.classList.toggle('active', b.getAttribute('data-role-tab') === role); });
    document.querySelectorAll('[data-role-pane]').forEach(function (p) { p.classList.toggle('active', p.getAttribute('data-role-pane') === role); });
    var note = document.querySelector('[data-driver-note]');
    if (note) note.classList.toggle('show', role === 'driver');
    var orgLabel = document.querySelector('[data-org-label]');
    if (orgLabel) orgLabel.textContent = role === 'recipient' ? 'Pantry / organisation' : (role === 'donor' ? 'Business name' : 'Organisation (optional)');
  }
  document.querySelectorAll('[data-role-tab]').forEach(function (b) {
    b.addEventListener('click', function () {
      var role = b.getAttribute('data-role-tab');
      activateRole(role);
      var radio = document.querySelector('.role-card input[value="' + role.toUpperCase() + '"]');
      if (radio) { radio.checked = true; radio.dispatchEvent(new Event('change', { bubbles: true })); }
    });
  });
  var cards = document.querySelectorAll('.role-card');
  function syncCards() {
    cards.forEach(function (c) {
      var r = c.querySelector('input');
      c.classList.toggle('is-selected', r.checked);
      if (r.checked) activateRole(r.value.toLowerCase());
    });
  }
  cards.forEach(function (c) { c.querySelector('input').addEventListener('change', syncCards); });
  if (cards.length) syncCards();

  // rotate the aside tips gently on the login page until the user interacts
  var tabs = document.querySelectorAll('[data-role-tab]');
  if (tabs.length && !cards.length) {
    var i = 0, timer = setInterval(function () { i = (i + 1) % tabs.length; activateRole(tabs[i].getAttribute('data-role-tab')); }, 6000);
    tabs.forEach(function (t) { t.addEventListener('click', function () { clearInterval(timer); }); });
  }
})();
