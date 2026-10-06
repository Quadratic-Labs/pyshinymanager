// Portage de inst/assets/shiny-utils.js du package R source.
//
// Conserve : togglewidget (activer / desactiver un bouton depuis le serveur).
// Non porte : unbindDT (pas de DataTables ici), rmInputSM (lie a input_checkbox_ui du source,
// remplace par la selection collectee ci-dessous).

// Disable / enable a button
Shiny.addCustomMessageHandler('togglewidget', function(data) {
  var el = document.getElementById(data.inputId);
  if (!el) return;
  if (data.type == 'disable') {
    el.setAttribute('disabled', true);
    el.classList.add('disabled');
  }
  if (data.type == 'enable') {
    el.removeAttribute('disabled');
    el.classList.remove('disabled');
  }
});

// Selection par ligne. Le source cree un input par ligne (input_checkbox_ui) puis les agrege
// dans un module (input_checkbox) ; py-shiny ne permet pas d'enumerer les inputs, donc la
// collecte se fait cote client et pousse la liste des utilisateurs coches dans UN SEUL input
// (celui du groupe). Meme valeur observable qu'en R : le vecteur des users selectionnes.
function smPushSelection(group) {
  var boxes = document.querySelectorAll('input.sm-row-check[data-group="' + group + '"]');
  var selected = [];
  boxes.forEach(function(b) {
    if (b.checked) selected.push(b.getAttribute('data-user'));
  });
  Shiny.setInputValue(group, selected, {priority: 'event'});
}

document.addEventListener('change', function(e) {
  if (e.target.classList && e.target.classList.contains('sm-row-check')) {
    smPushSelection(e.target.getAttribute('data-group'));
  }
});

// Tout cocher / tout decocher un groupe (boutons select_all_users / change_selected_allusers).
Shiny.addCustomMessageHandler('smCheckAll', function(data) {
  var boxes = document.querySelectorAll('input.sm-row-check[data-group="' + data.group + '"]');
  boxes.forEach(function(b) { b.checked = data.value; });
  smPushSelection(data.group);
});

// Une table fraichement rendue n'a aucune case cochee : reinitialise la selection du groupe
// (equivalent du nettoyage rmInputSM du source apres redessin de la table).
Shiny.addCustomMessageHandler('smResetSelection', function(data) {
  Shiny.setInputValue(data.group, [], {priority: 'event'});
});

// Initialisation des infobulles des boutons de ligne. Le source emet un
// `$('[data-toggle="tooltip"]').tooltip()` avec chaque bouton (shiny-utils.R:117) ; sans
// equivalent, les boutons-icones n'ont NI texte NI infobulle. py-shiny embarque Bootstrap 5,
// dont l'API et les attributs different de ceux du source (data-bs-*) : on convertit.
function smInitTooltips(root) {
  if (typeof bootstrap === 'undefined' || !bootstrap.Tooltip) return;
  (root || document).querySelectorAll('[data-toggle="tooltip"]').forEach(function(el) {
    if (el._smTooltip) return;
    el.setAttribute('data-bs-toggle', 'tooltip');
    el.setAttribute('data-bs-title', el.getAttribute('data-title') || '');
    el.setAttribute('data-bs-container', el.getAttribute('data-container') || 'body');
    el._smTooltip = new bootstrap.Tooltip(el);
  });
}

// Les tables sont rendues par un output : reinitialiser a chaque (re)dessin.
$(document).on('shiny:value', function(e) { setTimeout(function() { smInitTooltips(); }, 0); });
$(function() { smInitTooltips(); });
