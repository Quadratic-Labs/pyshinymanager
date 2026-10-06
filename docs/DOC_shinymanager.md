# 📘 Documentation — shinymanager (Python)

> **Contexte :** brique d'authentification pour applications Shiny for Python.
> **Public cible :** integrateur / responsable d'application souhaitant comprendre ce que le
> paquet fait, sans lire le code.
> **Derniere mise a jour :** 2026-07-16

---

## 1. Sources de donnees

Le paquet ne produit pas d'analyse : il gere les **comptes** et les **connexions**. Il s'appuie
sur une base de credentials, sous l'un de ces formats au choix de l'integrateur.

| Source | Type | Description | Remarques |
|---|---|---|---|
| liste de comptes | en memoire | comptes fournis directement dans le code | prototypage / tests uniquement |
| `credentials.sqlite` | fichier SQLite | base de comptes locale | production simple, un fichier a proteger |
| base SQL externe | Postgres / MySQL / MSSQL / Databricks | base d'entreprise, decrite par un fichier de configuration | secrets par variables d'environnement |

Chaque base contient trois tables : **credentials** (les comptes : identifiant, mot de passe
hache, droits admin, dates de debut et de fin de validite), **pwd_mngt** (l'etat des mots de
passe : changement a forcer, nombre d'echecs, date du dernier changement) et **logs** (l'historique
des connexions : qui, quand, depuis quelle application, deconnexion).

---

## 2. Fonctionnement

### 2.1 Protection de l'application
L'integrateur enveloppe son application. Tant que l'utilisateur n'est pas authentifie, il ne voit
qu'une page de login. Une fois connecte, il accede a l'application ; l'information de connexion est
conservee dans un cookie securise, inaccessible au code de la page (jamais dans l'adresse).

### 2.2 Gestion des mots de passe
Un mot de passe doit respecter une politique de complexite (au moins un chiffre, une minuscule,
une majuscule, six caracteres). Un compte peut etre force a changer son mot de passe a la
prochaine connexion. Un mot de passe peut expirer apres un delai. Apres trop d'echecs, le compte
se verrouille. Les mots de passe ne sont jamais stockes en clair : ils sont haches.

En option, un lien "Mot de passe oublie ?" sur la page de login permet a un utilisateur de
recevoir par mail un mot de passe temporaire (en donnant son nom et son email, ou son nom seul
selon le reglage). Il doit le changer a sa prochaine connexion, et ce mot de passe peut avoir une
duree de validite limitee. La page affiche toujours le meme message, pour ne pas reveler quels
comptes existent ; chaque demande est tracee dans les logs de l'administration. L'envoi du mail
est confie a une fonction fournie par l'application (un envoi SMTP pret a l'emploi est inclus).

### 2.3 Expiration de session
Une session inactive au-dela d'un delai configurable (15 minutes par defaut) expire
automatiquement : l'utilisateur est renvoye a la page de login. L'activite reelle (souris,
clavier, defilement) reinitialise le compteur.

### 2.4 Administration
Les comptes marques administrateurs accedent a un panneau dedie : liste des utilisateurs, ajout,
edition, suppression, reinitialisation de mot de passe, et un onglet de statistiques de connexion.

---

## 3. Ce que vous obtenez

| Output | Format | Description |
|---|---|---|
| Page de login | ecran | panneau centre, 11 langues, message d'erreur precis (identifiants, compte verrouille, expire, non autorise) |
| Ecran de changement de mot de passe | ecran | impose a la premiere connexion ou apres reinitialisation |
| Panneau admin — Utilisateurs | tableau interactif | ajout / edition / suppression / reinitialisation, avec garde-fous |
| Panneau admin — Statistiques | 2 graphiques | connexions par utilisateur (barres) et par jour (aire) |
| Exports | fichiers CSV / SQLite | table des utilisateurs, base SQLite, journal des connexions |
| Infos de l'utilisateur connecte | donnees | disponibles cote application (identifiant, droits, applications autorisees) |

---

## 4. Choix methodologiques

> Cette section explique *pourquoi* certaines decisions ont ete prises. Elles ecartent
> deliberement le comportement du package R d'origine ; le detail est dans `migration.md`.

- **Cookie plutot qu'adresse (URL)** : le jeton de session ne transite plus dans l'adresse de la
  page mais dans un cookie securise. C'est plus sur (jeton non exposable, non copiable par un lien).
- **Refus par defaut en cas de doute** : si le systeme ne peut pas verifier l'etat d'un compte
  (erreur de lecture), il refuse l'acces plutot que de laisser passer. Le package R faisait
  l'inverse.
- **Pas de chiffrement de la base, mais mots de passe haches** : la base n'est pas chiffree ; la
  protection repose sur le hachage des mots de passe et les permissions du fichier. Alignee sur le
  mode SQL du package d'origine.
- **Configuration declarative** : la connexion a une base SQL externe se decrit dans un fichier de
  configuration simple (pas de code executable dans la configuration, pour des raisons de securite).

---

## 5. Limites et points d'attention

- **Bases SQL d'entreprise** : le fonctionnement est valide sur SQLite ; Postgres, MySQL, MSSQL et
  Databricks reposent sur la meme mecanique mais n'ont pas ete eprouves sur une infrastructure
  reelle.
- **Compatibilite avec une base R existante** : aucune. Le stockage a ete repense ; une base creee
  par le package R d'origine n'est pas relue telle quelle.
- **Fichier de base a proteger** : la base SQLite n'etant pas chiffree, son acces disque doit etre
  restreint par les permissions du systeme.
- **Ecarts cosmetiques mineurs assumes** : quelques details visuels du package d'origine ne sont
  pas reproduits a l'identique (indicateur d'attente lors d'une ecriture, retraduction instantanee
  de toute la page au changement de langue). Sans effet sur la securite ni le fonctionnement.
- **Mot de passe oublie** : le mail part avant la modification du mot de passe ; si l'envoi
  echoue, rien ne change. Comme dans le package d'origine, une demande pour un compte existant
  repond un peu plus lentement (temps d'envoi du mail), ce qui peut trahir son existence a un
  observateur attentif. Sur une base SQL, la duree de validite du mot de passe temporaire
  necessite d'ajouter une colonne a la table des mots de passe.
- **Dependances** : le paquet installe plotly et ses dependances (pour les graphiques de
  l'onglet statistiques) — a prendre en compte dans un environnement contraint.
