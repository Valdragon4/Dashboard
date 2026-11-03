/* =====================================================================
   Base commune — charte Graphite (applications personnelles).
   À charger dans le <head>, JUSTE APRÈS vendor/tailwind.js :

     <link rel="stylesheet" href="…/base/base.css">
     <link rel="stylesheet" href="…/base/composants.css">
     <script src="…/base/vendor/tailwind.js"></script>
     <script src="…/base/base.js"></script>
     <script src="…/base/icones.js" defer></script>

   1. Applique le thème AVANT le rendu (pas de flash).
   2. Configure Tailwind sur les jetons de base.css.
   3. Câble les comportements communs : mode sombre, tiroir mobile,
      groupes segmentés.
   Aucun innerHTML ici : tout se fait par attributs et classes.
   ===================================================================== */
(function () {
  var html = document.documentElement;

  function lire(cle) { try { return localStorage.getItem(cle); } catch (_) { return null; } }
  function ecrire(cle, val) { try { localStorage.setItem(cle, val); } catch (_) {} }

  // --- 1. Thème : choix mémorisé, sinon préférence du système -----------
  var theme = lire('theme') ||
    (window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  var force = new URLSearchParams(location.search).get('theme'); // captures : ?theme=dark
  if (force === 'dark' || force === 'light') theme = force;
  html.classList.toggle('dark', theme === 'dark');

  // --- 2. Tailwind sur les jetons -----------------------------------------
  function c(v) { return 'rgb(var(--' + v + ') / <alpha-value>)'; }
  if (window.tailwind) {
    window.tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            bg: c('bg'), surface: c('surface'), sunken: c('sunken'),
            line: c('line'), 'line-strong': c('line-strong'),
            ink: c('ink'), 'ink-2': c('ink-2'), 'ink-3': c('ink-3'),
            accent: c('accent'), 'accent-hover': c('accent-hover'), 'accent-soft': c('accent-soft'),
            'on-accent': c('on-accent'), 'accent-text': c('accent-text'),
            side: c('side'), 'side-line': c('side-line'), 'side-ink': c('side-ink'), 'side-ink-3': c('side-ink-3'),
            'side-active': c('side-active'), 'side-active-ink': c('side-active-ink'), 'side-hover': c('side-hover'),
            crit: c('st-crit'), 'crit-soft': c('st-crit-soft'),
            warn: c('st-warn'), 'warn-soft': c('st-warn-soft'),
            info: c('st-info'), 'info-soft': c('st-info-soft'),
            ok: c('st-ok'), 'ok-soft': c('st-ok-soft'),
            unknown: c('st-unknown'), off: c('st-off'),
            // Transition : les anciens noms de l'outil finance pointent vers l'accent.
            primary: c('accent'), 'primary-dark': c('accent-hover'), 'primary-light': c('accent-text')
          },
          borderRadius: { card: 'var(--r-card)', ctl: 'var(--r-ctl)', badge: 'var(--r-badge)' },
          boxShadow: { card: 'var(--sh-card)', pop: 'var(--sh-pop)' },
          fontFamily: { sans: 'var(--f-sans)', mono: 'var(--f-mono)' },
          spacing: { row: 'var(--row)' }
        }
      }
    };
  }

  // --- 3. Comportements ----------------------------------------------------
  function majTheme(t) {
    html.classList.toggle('dark', t === 'dark');
    ecrire('theme', t);
    document.querySelectorAll('[data-action="theme"]').forEach(function (b) {
      b.setAttribute('aria-pressed', String(t === 'dark'));
    });
  }
  window.Base = { theme: majTheme };

  document.addEventListener('DOMContentLoaded', function () {
    // Bascule du mode sombre (bouton du pied de barre latérale)
    document.querySelectorAll('[data-action="theme"]').forEach(function (b) {
      b.setAttribute('aria-pressed', String(html.classList.contains('dark')));
      b.addEventListener('click', function () { majTheme(html.classList.contains('dark') ? 'light' : 'dark'); });
    });

    // Tiroir de navigation mobile
    var ouvrir = document.querySelector('[data-action="menu"]');
    function basculer(ouvert) {
      html.classList.toggle('menu-ouvert', ouvert);
      if (ouvrir) ouvrir.setAttribute('aria-expanded', String(ouvert));
    }
    if (ouvrir) ouvrir.addEventListener('click', function () { basculer(true); });
    document.querySelectorAll('[data-action="fermer-menu"]').forEach(function (b) {
      b.addEventListener('click', function () { basculer(false); });
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && html.classList.contains('menu-ouvert')) basculer(false);
    });

    // Groupes segmentés : un seul bouton pressé par groupe
    document.querySelectorAll('[data-segmente]').forEach(function (groupe) {
      groupe.addEventListener('click', function (e) {
        var b = e.target.closest('button');
        if (!b || !groupe.contains(b)) return;
        groupe.querySelectorAll('button').forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); });
      });
    });
  });
})();
