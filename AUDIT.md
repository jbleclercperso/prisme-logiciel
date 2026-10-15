# Audit de Prisme — octobre 2026

Cinq relectures en parallèle, en lecture seule (gels de l'interface, architecture, travail de fond et NAS, démarrage et mémoire, filet de sécurité), puis vérification des points clés. Base : 1.1.20 + correctifs Telegram du 15/10. Aucune fonctionnalité ne doit disparaître : chaque lot ci-dessous dit comment vérifier que rien n'est cassé.

## Ce qui ressort

Le code est déjà soigné (WAL SQLite, écritures groupées, vignettes et aperçus hors du fil de l'interface, imports lourds différés). La lenteur ne vient pas d'un « code sale » en général, mais de quelques causes précises :

1. **Décodage vidéo réglé sur « carte graphique »** sur ce PC. Le code lui-même le dit : chaque vidéo ouverte crée un périphérique Direct3D sur le fil de l'interface (0,13 à 0,5 s de gel par aperçu survolé). En « Automatique », un PC à 8 cœurs décode au processeur. *Un réglage, pas du code.*
2. **Telegram sans son module de chiffrement rapide (`cryptg`)** : tout ce qui arrive de Telegram est déchiffré en Python pur, ce qui affame l'interface. Les rafales de gels du 14/10 au soir (un gel de 1 à 2 s toutes les 4 s pendant 45 min) correspondent à l'activité Telegram.
3. **Rien ne laisse la priorité à la lecture** : pendant qu'une vidéo du NAS joue, jusqu'à ~20 ffmpeg (vignettes, signatures, scènes…) se disputent le réseau, à priorité normale.
4. **Le modèle de voix (Whisper, ~1 Go) se charge 30 s après chaque lancement** et reste en mémoire toute la session, même sans jamais toucher au micro.
5. **Des lectures du NAS en continu** : présence des visiteurs toutes les 5 s (même partage coupé), demandes du téléphone toutes les 30 s, racines toutes les 20 s.
6. **Le journal des gels désigne mal les coupables** : 85 % des secondes de gel portent l'étiquette d'une action vieille de plusieurs minutes. Il faut qu'il note *ce que faisait* le programme au moment du gel.

## Bugs trouvés en passant

- **8 réglages jamais relus** (dossier et navigateur des téléchargements web, titre exact, taille des cartes et sections ouvertes de la recherche web ; exemples, grain et banc du Labo) : ils repartent à zéro à chaque lancement. *(config.py : clés absentes de `DEFAULTS`.)*
- **`tests/bench_scan.py` efface l'index réel** s'il est lancé (pas de bac à sable). `bench_startup.py`, `bench_root.py` et `make_fixture.py` lisent aussi les vraies données.
- **Vérification des mises à jour toutes les 15 min** alors que le commentaire dit six heures.
- `telegram_hub` : « Ouvrir le dossier » utilise `/select,` sur un dossier.

## Avancement (15/10/2026)

Fait et vérifié (guard, smoke, tests ciblés, vraie fenêtre en bac à sable : vignettes posées, aperçu qui bouge) :
- Lot 0 : `tests/guard.py` + référence `tests/guard_baseline.json` ; bancs d'essai en bac à sable ; journal des gels qui nomme la fonction en cours ; `--verifier` contrôle Telegram/cryptg, les petites bibliothèques et l'import de tous les modules.
- Lot 1 : cryptg ; la vidéo qui joue fait attendre les travaux de fond ; ffmpeg sous la priorité normale ; modèle vocal préchargé seulement si le micro a servi dans la semaine ; Telegram pas démarré sans groupe suivi ; 8 réglages perdus gardés ; visiteurs du NAS relus toutes les 30 s quand personne ne regarde ; survol sondé au ralenti fenêtre inactive ; battement du détecteur de gels à 250 ms.
- Lot 2 (en partie) : page de vignettes posée en une seule mise à l'écran ; doublons trouvés relus hors du fil de l'interface.

Reste : réglage « Décodage vidéo » (à passer en Automatique par l'utilisateur), préchauffage du premier aperçu, sonde NAS avant les cartes connues, lots 3 à 5. Deux échecs de `tests/test_photos.py` existaient avant ce travail (onglet « Photos », zoom à la molette) : à regarder.

## Plan, par lots — du plus sûr au plus risqué

Chaque lot : un commit, les vérifications indiquées, puis contrôle à l'écran réel pour tout ce qui touche à l'image.

### Lot 0 — Garde-fous (avant de toucher au reste)
- `tests/guard.py` (quelques secondes, bac à sable, sans fenêtre visible) :
  - import de **tous** les modules (y compris ceux chargés à la demande : Telegram, sondages, missions, réparation…) + contrôle des noms non définis ;
  - photographie de l'« API » (`dir(MainWindow)`, `BoardView`, `SinglePlayer`, `Index`) comparée avant/après ;
  - photographie des menus (libellés, cases, actions branchées) — plusieurs fonctions dépendent du texte exact des menus ;
  - ouverture de chaque fenêtre secondaire (Telegram, sondages, missions, demandes, réparation, solos, mosaïque, doublons, recherche web, licence) ;
  - sonde « l'image bouge » sur un vrai petit clip, pour chaque lecteur (fiche, survol, mur, lecteur de côté, flottant, mosaïque) ;
  - invariants d'empaquetage (fichiers plats, liste des fichiers publiés sur le NAS importables sans Qt).
- Mettre le bac à sable dans les bancs d'essai (`bench_*.py`, `make_fixture.py`).
- `--verifier` contrôle aussi Telethon/cryptg, segno, rapidfuzz, Send2Trash et l'import de chaque module.
- Pouvoir lancer une seule section de `tests/test_app.py`.
- Journal des gels : noter la pile du fil de l'interface (et les autres fils) quand un gel dépasse 0,3 s.

### Lot 1 — Gains sûrs, sans changer de comportement visible
- `cryptg` dans requirements.txt et dans le programme empaqueté.
- Voix : ne plus charger Whisper au lancement ; seulement au premier usage du micro (ou au survol du bouton), et le libérer après un temps d'inactivité.
- Les 8 réglages perdus ajoutés à `DEFAULTS`.
- ffmpeg de fond : `-threads 1` partout, priorité « inférieure à la normale », plafond global de processus simultanés.
- Un drapeau « lecture en cours » : le travail de fond cède la place pendant qu'une vidéo joue (comme il le fait déjà pour les vignettes demandées).
- Telegram : surveillance non démarrée tant qu'aucun groupe n'est suivi ; « déjà dans la bibliothèque » lu depuis l'index au lieu de parcourir tout le NAS.
- Mises à jour : vraiment toutes les 6 h ; `licence.status()` calculé une fois au lieu de deux.
- Lectures du NAS : présence des visiteurs seulement si le partage tourne ; ralentir quand Prisme est réduit ou inactif.
- Minuteries : le « cœur » du détecteur de gels à 250 ms au lieu de 50 ms (précis), survols arrêtés quand la fenêtre est inactive.

### Lot 2 — Démarrage et planche
- Premier aperçu de la session (2,4 à 3,8 s de gel aujourd'hui) : préchauffer le lecteur derrière l'écran d'accueil.
- Remplissage d'une page de cartes : mises à jour suspendues pendant le remplissage, cartes préparées pendant les temps morts.
- Liste « toutes les vidéos » et serveur de partage préparés après coup (quand Prisme est au repos), pas pendant l'affichage des premières cartes.
- Doublons trouvés : ne lire que l'en-tête au lancement.
- Index : chemins mis en commun (`sys.intern`) — moins de mémoire.
- Ne plus attendre la sonde du NAS (jusqu'à 2,5–14 s) pour montrer les cartes déjà connues.

### Lot 3 — Nettoyage du code (aucun changement visible)
- Supprimer : `rescue.py` (orphelin, 303 lignes), 21 fonctions jamais appelées, alias anciens, imports inutilisés.
  - À trancher par toi : `dupes_memory.retablir` (annuler un « pas des doublons ») et `media.clear_cache` (vider le cache des vignettes) — fonctions prêtes mais sans bouton. On les branche ou on les retire ?
  - « Avancer après une étoile » : la case a disparu du menu, le code reste. On la remet ou on la retire ?
- Regrouper les doublons de code : écriture « .tmp puis remplacement » (14 copies), formats de taille et de durée, « Afficher dans l'Explorateur », recherche d'ffmpeg, parcours des dossiers vidéo (3 copies).
- Garder les migrations d'anciennes versions jusqu'au tri de la version vendue (tout à la fin).

### Lot 4 — Découpage de la fenêtre principale (16 000 lignes, 643 méthodes)
Déplacements purs (même code, nouveaux fichiers), des parties les plus isolées aux plus liées : bandeaux → mises à jour/licence → pastilles web → solos → corbeille → partage/visiteurs → téléphone/sondages/missions → racines → doublons → renommage → ultra/similaires → filtres. En dernier, et seulement si les garde-fous tiennent : mur, sélection/actions, photos, lecteurs de côté et flottant. Contrôle à chaque étape : même `dir(MainWindow)`, smoke, sections ciblées, écran réel.

### Lot 5 — Plus lourd, à décider ensuite
- Lecteurs créés à la demande (mur, flottant, lecteur de côté) au lieu de 11 lecteurs vidéo au lancement.
- Les fonctions (signatures, scènes, titres, vignettes) qui reparcourent tout le NAS : partir de l'index.
- Sortir le CSS/JS de la page web de `web.py` (3 700 lignes de texte) — impose d'adapter la publication NAS et l'empaquetage.

## Fonctions sans aucun test aujourd'hui (les plus exposées)
Image réelle des lecteurs (aucun test ne vérifie que l'image bouge), tri ultra-rapide (Ctrl+U), couvertures de dossiers, Telegram, intégration téléphone/visiteurs côté fenêtre, démarrage/fermeture complets, une dizaine de fenêtres jamais ouvertes par les tests, changement de racine, épingles de mots, vidéos similaires, solos, volume/sortie audio, voix, mises à jour, réglages du menu ⋯. Le lot 0 en couvre l'essentiel.

## Contrôles à l'écran réel après chaque lot (image et son)
1. Lancement : dernière racine sur Dossiers, couvertures présentes (différentes du lancement précédent).
2. Survol de cartes (Dossiers, Vidéos, chaque densité de colonnes) : l'aperçu bouge, jamais noir.
3. Fiche : lecture, pause au clic, molette, Maj (neuf instants), Espace = suivante, touches de tri, Ctrl+Z.
4. Mur : chaque panneau bouge, son sur un seul, F ouvre la fiche, Échap revient.
5. Lecteur de côté, flottant (Ctrl+L), cinéma (F11), mosaïque.
6. Photos (diaporama, mur de photos), Mots-clés, Favoris.
7. Menu ⋯ : chaque sous-menu et chaque fenêtre s'ouvre sans bandeau d'erreur.
8. Ctrl+U, Ctrl+K, fermeture puis relance (pas d'index reconstruit).
9. Programme empaqueté : `Prisme-diagnostic.exe --verifier`, version portable et installée.
