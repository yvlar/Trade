# Atelier Swing

Une application française locale pour parcourir des séances D1, expliquer trois
stratégies déterministes et suivre leur exécution **paper historique**.
Actions et FNB américains, USD, positions LONG entières, sans levier.

**Ce prototype est un laboratoire de replay. Aucun ordre courtier, campagne réelle,
collecte réseau, IA ou cloud. Les 300 bougies d'exemple sont fabriquées. Leurs
résultats ne représentent aucune performance de marché.**

## Démarrer sous Windows / PowerShell

Prérequis : Python 3.11 ou ultérieur. Depuis cette copie du dépôt :

~~~powershell
cd C:\Users\ivess\Documents\Codex\2026-10-16\task-2\Trade
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m swing --port 8765 --db data\swing.sqlite3
~~~

Ouvrir **http://127.0.0.1:8765**. Le worker est arrêté au démarrage.
L'installation initiale de tzdata nécessite un accès au registre Python ; après
installation, l'application fonctionne hors ligne. Aucune clé ni fichier .env.
Arrêt complet : Ctrl+C dans le terminal.

Si 8765 est occupé, choisir par exemple --port 8766. Le port 8000 est refusé.
Le serveur écoute exclusivement 127.0.0.1, sans changement de pare-feu ni service
Windows. L'interface est aussi accessible par localhost sur le même port.

Sur macOS/Linux, créer le venv avec python3 -m venv .venv, puis utiliser
.venv/bin/python pour les mêmes commandes ; ouvrir la même adresse locale.

## Parcours

1. Régler les hypothèses avant la première séance : capital fictif 10 000 USD,
   risque 0,5 % par entrée, exposition 20 % par entrée, risque cumulé 2 %,
   quatre positions au maximum, frais 1 USD par transaction et glissement adverse
   de 5 points de base. **Ce sont des hypothèses pédagogiques, pas des conseils.**
2. Charger l'exemple synthétique ou importer un fichier JSON. Le lien « Format
   d'exemple » fournit un fichier complet, explicitement marqué synthétique.
3. Traiter une séance ou démarrer le replay (une séance toutes les 0,5 s).
   Le démarrage ne crée aucun service persistant. Une pause laisse terminer la
   transaction courante. Aucun replay ne redémarre automatiquement.
4. Examiner les occasions expliquées, positions, entrées en attente, sorties et
   statistiques. Les 24 premières séances constituent l'historique.
5. Filtrer le journal et ouvrir les preuves. Exporter tout en JSON pour conserver
   état, bougies, provenance, lots d'import et événements. L'écran montre les
   250 derniers événements ; l'export contient le journal complet.

Les réglages et l'univers sont figés pour une expérience. Pour comparer d'autres
hypothèses, arrêter l'application puis la lancer avec --db data\experience-2.sqlite3.
Ne pas lancer plusieurs serveurs sur la même base : les transactions empêchent
les doubles exécutions d'une séance, mais deux workers peuvent accélérer le replay.
La pause d'une fenêtre n'arrêterait pas l'autre processus.

## Entrées de données

Un import est un objet contenant exactement :

~~~json
{
  "source": "SYNTHÉTIQUE — exemple de format, prix inventés",
  "synthetic": true,
  "price_basis": "raw",
  "corporate_actions": "none_attested",
  "assets": [
    {"symbol":"SPY","kind":"ETF","market":"US","currency":"USD","leveraged":false}
  ],
  "bars": [
    {
      "symbol":"SPY","date":"2026-01-02",
      "open":"100.00","high":"101.00","low":"99.00","close":"100.50",
      "volume":1000000,"complete":true,
      "published_at":"2026-01-02T21:05:00+00:00"
    }
  ]
}
~~~

Les nombres de cet extrait illustrent le format ; ils ne sont pas des cours.
Ne pas présenter cet extrait comme un jeu de marché réel. synthetic:false est
réservé à des données effectivement obtenues d'une source autorisée.

- 1 à 20 instruments EQUITY ou ETF, en USD, marché US, sans levier déclaré.
  Les identités, droits d'usage et métadonnées ne sont pas certifiés par l'application.
- 1 à 20 000 bougies par import, fichier ≤ 10 Mo ; prix positifs ≤ 1 000 000,
  quatre décimales maximum, OHLC cohérent, volume entier positif.
- Séances régulières US uniquement, calendrier vérifié 2026–2028. Bougies
  complètes et clôturées ; publication horodatée avec fuseau, après clôture et
  avant l'instant réel de réception.
- **received_at** est créé par l'application à l'import ; le fichier ne peut pas
  le fournir. Il conserve la vraie date d'import, jamais une date historique fictive.
  **published_at** est déclaré par la source. Une empreinte SHA-256 identifie le lot.
- Un même lot est idempotent. Un doublon interne au fichier, une révision d'une
  bougie existante ou un mélange de provenances est refusé intégralement.
- Toutes les séances de l'univers doivent être présentes entre le début du jeu
  et le curseur. Un trou bloque le moteur avec une **erreur**, sans avance du
  curseur. Un import complémentaire peut réparer un trou encore non traité.
- Les imports peuvent ajouter des séances futures par rapport au curseur mais
  déjà clôturées au moment réel de l'import. Aucune réécriture du passé traité.
- Prix bruts uniquement. none_attested signifie que le fournisseur atteste
  l'absence de splits, dividendes, fusions et autres événements sur la période.
  Sinon le jeu n'est pas utilisable dans cette version.

## Règles exactes

Calculs en Decimal. Aucune stratégie n'accède à une bougie après la séance
traitée. Les EMA partent de la première clôture de l'historique ; échauffement
minimal de 25 séances. L'ATR est la moyenne des 14 derniers true ranges.

| Stratégie | Conditions à la clôture | Stop et objectif |
|---|---|---|
| Repli de tendance | EMA10 > EMA20 ; EMA20 en hausse ; bas du jour ≤ EMA10 ; clôture > EMA10 et haut précédent | Stop = clôture − 1,5 ATR ; objectif = clôture + 2× distance au stop |
| Cassure de consolidation | Largeur des 10 séances précédentes ≤ 6 % du plus bas ; clôture au-dessus du plus haut ; volume ≥ 1,5× moyenne de ces 10 séances | Même stop et objectif |
| Retour à la moyenne | Clôture précédente < sa moyenne précédente de 20 séances − 1,5 ATR ; clôture actuelle > haut précédent et < moyenne des 20 clôtures précédentes | Même stop ; objectif = moyenne précédente de 20 séances |

Les niveaux sont arrondis au cent inférieur. ATR nul = erreur de calcul,
historique < 25 = historique insuffisant, conditions non réunies = abstention.
Si plusieurs stratégies signalent le même titre, priorité fixe : repli, cassure,
retour à la moyenne. Pas d'optimisation ni sélection par performance.

La décision est horodatée à **clôture + 30 minutes**. Une publication plus tardive
est une erreur bloquante pour cette convention de replay. received_at ne sert
pas à prétendre une observation prospective : ces données importées aujourd'hui
rejouent une disponibilité historique déclarée, sans preuve qu'elle existait.

L'entrée attend la prochaine séance US, à l'ouverture, après glissement adverse.
Elle est refusée si hors stop/objectif, gap absolu > 3 % par défaut, même titre
déjà détenu, limite de positions ou budget insuffisant. Taille = minimum des
contraintes d'espèces, exposition, risque par entrée et risque cumulé. Le risque
inclut les frais des deux transactions et le glissement de sortie au stop.
Les espèces réservent les frais de sortie de toutes les positions ouvertes.
Les limites s'appliquent aux nouvelles entrées ; les mouvements ultérieurs ne
déclenchent pas de rééquilibrage. Les titres sont traités dans l'ordre alphabétique.

À l'ouverture : sorties sur gap ou objectif puis expiration des positions.
Ces seules sorties peuvent financer d'autres entrées à cette ouverture.
Dans la bougie : stop avant objectif si les deux sont touchés.
Un gap sous le stop sort au prix d'ouverture adverse ; un gap au-dessus de
l'objectif utilise l'objectif, sans amélioration. Après dix séances détenues
par défaut, sortie à l'ouverture suivante. Aucune liquidation forcée à la fin
du fichier : une position peut rester ouverte.

Le journal conserve les motifs, prix, frais et empreintes des historiques utilisés.
À chaque séance : **cash + valeur des positions = capital initial + réalisé + latent**.
Les valorisations sont arrondies au cent par position. Le latent inclut les frais
d'entrée déjà payés, mais pas les futurs frais de sortie. La variation concerne
la valeur totale ; le drawdown utilise seulement les clôtures, sans annualisation.

## Architecture et sûreté locale

| Fichier | Responsabilité |
|---|---|
| swing/domain.py | Fonctions financières pures : indicateurs, règles, taille, sorties |
| swing/calendar.py | Séances US, fuseau IANA, jours fériés et clôtures anticipées |
| swing/store.py | Validation, provenance, SQLite et import atomique |
| swing/engine.py | Orchestration d'une séance et statistiques |
| swing/server.py | HTTP loopback, contrôles de requêtes et worker pilotable |
| swing/static/ | Interface française sans framework, CDN, police distante ou télémétrie |
| swing/broker.py | Contrat Alpaca inactif : chaque tentative lève une erreur |
| swing/fixtures.py | Génération déterministe de données fabriquées |

SQLite WAL, synchronisation FULL et transaction BEGIN IMMEDIATE : bougies/lot
ou séance/positions/cash/journal sont écrits ensemble. expected_cursor empêche
un double clic ou une requête répétée d'avancer deux fois. Une interruption avant
commit annule la séance ; après commit, le curseur reprend à la suivante.
Les erreurs du worker l'arrêtent et apparaissent au journal. Les bases et logs
sont ignorés par Git. Sauvegarder le dossier de données **application arrêtée**,
ou utiliser l'API de sauvegarde SQLite ; l'export JSON est une preuve, pas un
format de restauration automatique.

Toutes les requêtes vérifient un Host local exact avec port. Les mutations exigent
Origin égal à l'origine locale, JSON et l'en-tête X-Swing-Local: 1 ; le navigateur
ne peut pas l'envoyer depuis un autre site sans prévol CORS, qui est refusé.
Pas de token par section ni de login. CSP et protection contre l'intégration en
iframe. Cela protège le navigateur contre les requêtes intersites ; un logiciel
local capable de forger HTTP peut toujours agir. Ne pas exposer ce serveur via proxy.

## Vérifier

~~~powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m ruff check swing tests
.\.venv\Scripts\python.exe -m ruff format --check swing tests
.\.venv\Scripts\python.exe -m mypy swing
node --check swing/static/app.js
.\.venv\Scripts\python.exe -c "from pathlib import Path; Path('data').mkdir(exist_ok=True)"
.\.venv\Scripts\python.exe -m pytest -q --basetemp=data\verification-tests
~~~

Node n'est nécessaire que pour la vérification syntaxique facultative de JavaScript.
Pytest utilise exclusivement des bases temporaires et ports loopback éphémères.
Le chemin data\verification-tests est réservé aux tests, que pytest peut nettoyer.
Les tests navigateur génèrent les [captures](docs/captures/).
La CI exécute les mêmes contrôles sur Python 3.11 et 3.13.

Voir le [rapport de validation et revue](docs/VALIDATION.md).

## Limites explicites

Ce travail ne fournit pas de flux de données agréé, screening d'univers complet,
campagne prospective, intégration courtier, qualification d'une stratégie,
gestion fiscale ou promesse de rendement. La liquidité et les exécutions réelles
ne sont pas modélisées : frais et glissement constants sont hypothétiques.
Les gaps peuvent dépasser le risque prévu. Les métadonnées sont déclaratives.
Les événements corporatifs, suspensions et fermetures exceptionnelles imprévues
nécessitent une évolution du modèle ou du calendrier.

Calendrier borné d'après les [horaires officiels NYSE](https://www.nyse.com/trade/hours-calendars),
consultés le 16 octobre 2026. Le prototype refuse les années hors périmètre.
Aucun ancien service Trading-Autonome n'est modifié ou réutilisé.
