# Validation de la première tranche

Date : 16 octobre 2026. Environnement local : Windows, Python 3.13.5,
pytest 9.0.3, Ruff 0.15.14, mypy 2.1.0, Playwright 1.59.0 / Chromium.

## Vérifications effectuées

- 60 tests réussis : domaine, import, moteur, HTTP et navigateur.
- Installation editable vérifiée dans un venv neuf ; découverte des packages
  limitée explicitement à swing pour exclure les données locales.
- Ruff check et format : conformes.
- mypy strict : aucun problème dans les 9 modules de l'application.
- JavaScript : vérification syntaxique Node réussie.
- Parcours Chromium : réglages, import synthétique, séance, démarrage/pause,
  portefeuille, preuves du journal et export JSON. Aucun pageerror.
- Largeurs 1440 et 390 pixels : pas de débordement horizontal global.
- Captures régénérées depuis un replay synthétique, visibles dans captures/.
  Leurs nombres sont des résultats fabriqués, jamais des performances réelles.

La suite inclut la modification des données futures sans modification des décisions
ou fills antérieurs, l'indépendance du prix/quantité d'entrée des high/low/close du
jour d'exécution, les entrées ultérieures aux signaux, gaps, stop prioritaire,
frais/glissement/risque, conservation des espèces, transactions annulées en cas
d'erreur, répétitions concurrentes et reprise après réouverture SQLite.

Imports invalides/incomplets, métadonnées interdites, dates sans fuseau, NaN,
infinis, doublons, révisions, trous et publications tardives sont exercés.
L'absence de données et les erreurs ne sont pas assimilées à l'abstention.

Les tests HTTP vérifient les réponses réelles, les routes, les en-têtes,
Host/Origin/CSRF, les chemins non autorisés, le worker et l'export.
Ils ont nécessité une exécution hors sandbox pour les connexions loopback :
la première tentative limitée a échoué avant les requêtes (WinError 10013).
Le rerun autorisé a effectivement exécuté les tests, sans contacter de courtier.

## Revue indépendante

Un agent distinct a relu domaine, moteur, persistance, calendrier et protections
HTTP/UI, sans modifier les fichiers et sans accéder à aucun secret.

Deux constats ont été corrigés, puis revérifiés indépendamment :

1. Les prix importés à quatre décimales pouvaient faire diverger la valorisation
   arrondie du membre droit de l'invariant comptable. Arrondi par position partagé.
2. Une nouvelle avance au terme du calendrier pouvait demander une séance de 2029
   avant de détecter la fin du fichier. La fin des données est maintenant vérifiée
   avant l'appel au calendrier.

Le reviewer a aussi exécuté un replay de 100 séances, un replay avec prix de
clôture à quatre décimales, et une mutation des futurs prix/volumes. Aucun problème
bloquant restant n'a été identifié. Son propre test HTTP était limité par le sandbox ;
les contrôles HTTP/navigateur ont donc été complétés dans la suite principale.

## Portée

Le premier passage CI Linux a détecté le parent absent du dossier temporaire
pytest dans un clone vierge. La préparation crée maintenant explicitement data
avant les tests ; aucun test ni contrôle n'a été désactivé.

Ce rapport ne revendique ni performance de marché, ni validation d'un flux réel,
ni certification de sécurité, ni précision d'exécution au carnet d'ordres.
La publication ne déploie aucun serveur. La CI distante doit être vérifiée avant
fusion ; une impossibilité technique ou de facturation n'autorise pas son contournement.
