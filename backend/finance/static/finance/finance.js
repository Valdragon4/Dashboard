/* =====================================================================
   Aides communes de l'outil finance (chargé après base/base.js).
   Aucun innerHTML : le DOM se construit par createElement / textContent.
   ===================================================================== */
(function () {
  var html = document.documentElement;
  var SVG = 'http://www.w3.org/2000/svg';

  // Icône du sprite (base/icones.js), décorative.
  function icone(nom, classe) {
    var svg = document.createElementNS(SVG, 'svg');
    svg.setAttribute('aria-hidden', 'true');
    if (classe) svg.setAttribute('class', classe);
    var use = document.createElementNS(SVG, 'use');
    use.setAttribute('href', '#i-' + nom);
    svg.appendChild(use);
    return svg;
  }

  // Élément avec classe et texte.
  function el(tag, classe, texte) {
    var e = document.createElement(tag);
    if (classe) e.className = classe;
    if (texte !== undefined && texte !== null) e.textContent = texte;
    return e;
  }

  // Notification passagère. niveau : info | ok | crit. duree en ms (0 = à retirer soi-même).
  function notifier(texte, niveau, duree) {
    niveau = niveau || 'info';
    var pile = document.querySelector('.notif-pile');
    if (!pile) { pile = el('div', 'notif-pile'); pile.setAttribute('aria-live', 'polite'); document.body.appendChild(pile); }
    var m = el('div', 'message message-' + niveau);
    if (niveau === 'crit') m.setAttribute('role', 'alert');
    m.appendChild(icone(niveau === 'crit' ? 'crit' : niveau === 'ok' ? 'ok' : 'info'));
    m.appendChild(el('p', 'flex-1', texte));
    pile.appendChild(m);
    if (duree !== 0) setTimeout(function () { m.remove(); }, duree || 4000);
    return m;
  }

  // Couleur d'un jeton CSS (« 15 118 110 ») en rgba() lisible par Chart.js et D3.
  function couleur(jeton, alpha) {
    var v = getComputedStyle(html).getPropertyValue('--' + jeton).trim().split(/\s+/);
    var a = alpha === undefined ? 1 : alpha;
    return 'rgba(' + v[0] + ', ' + v[1] + ', ' + v[2] + ', ' + a + ')';
  }

  // Bouton occupé : désactivé, aria-busy, libellé « … en cours ».
  function occuper(btn, libelle) {
    if (!btn.dataset.libelle) btn.dataset.libelle = btn.textContent.trim();
    btn.disabled = true;
    btn.setAttribute('aria-busy', 'true');
    btn.replaceChildren(icone('actualiser'), document.createTextNode(libelle));
  }
  function liberer(btn, icon) {
    btn.disabled = false;
    btn.removeAttribute('aria-busy');
    btn.replaceChildren();
    if (icon) btn.appendChild(icone(icon));
    btn.appendChild(document.createTextNode(btn.dataset.libelle || ''));
  }

  window.Finance = { icone: icone, el: el, notifier: notifier, couleur: couleur, occuper: occuper, liberer: liberer };

  // Changement de thème : prévient les graphiques (événement « themechange »).
  var sombre = html.classList.contains('dark');
  new MutationObserver(function () {
    var d = html.classList.contains('dark');
    if (d === sombre) return;
    sombre = d;
    window.dispatchEvent(new CustomEvent('themechange', { detail: { dark: d } }));
  }).observe(html, { attributes: true, attributeFilter: ['class'] });
})();
