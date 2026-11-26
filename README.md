# Jarvis — assistant vocal personnel pour Windows

Vous dites « Hey Jarvis », vous parlez, il agit sur le PC et répond à voix haute.
Le cerveau est Claude, **sur votre forfait** (Pro / Max) : aucune clé API, aucune facturation à l'usage.
Un réglage suffit pour passer sur une IA locale (Ollama).

## Installation (5 minutes)

1. Installer **Python 3.11 ou 3.12** depuis https://www.python.org/downloads/ en cochant **« Add python.exe to PATH »**.
   (Évitez 3.13+ pour l'instant : certaines bibliothèques audio n'ont pas encore de version précompilée.)
2. Décompresser ce dossier, par exemple dans `C:\Jarvis`.
3. Double-cliquer sur **`install.bat`**. Il crée l'environnement, installe tout, puis ouvre la page de
   connexion Claude dans le navigateur : connectez-vous avec le compte de votre forfait (une seule fois).
   Si Claude Code est déjà connecté sur ce PC, cette étape est sautée automatiquement.
4. Double-cliquer sur **`Jarvis.bat`** (voix) ou **`Jarvis-texte.bat`** (clavier, sans micro).

Au premier lancement vocal, Jarvis télécharge le modèle de reconnaissance vocale (~500 Mo) : c'est normal.

## Utilisation

| Pour… | Faites |
|---|---|
| Réveiller Jarvis | Dites **« Hey Jarvis »** (bip aigu), ou **Ctrl+Alt+J** |
| Enchaîner | Après sa réponse, il écoute encore 6 s sans mot de réveil (bip court) |
| Lui couper la parole | **Ctrl+Alt+J** pendant qu'il parle |
| Finir l'échange | « Merci Jarvis » |
| Repartir de zéro | « Nouvelle conversation » |
| Le quitter | « Au revoir Jarvis » |

Exemples de demandes :

- « Ouvre Chrome et Spotify », « Ferme le bloc-notes », « Mets le volume à 30 », « Chanson suivante »
- « Cherche sur YouTube un tuto Excel », « Quel temps fait-il à Lyon demain ? », « Qui a gagné le match hier ? »
- « Trouve ma dernière facture PDF et copie-la sur le bureau », « Range mes téléchargements par type de fichier »
- « Lis-moi le fichier notes.txt du bureau et résume-le », « Écris un compte rendu dans Documents »
- « Envoie un mail à Paul pour dire que j'aurai dix minutes de retard », « Lis mes mails non lus »
- « Regarde mon écran et dis-moi ce que signifie cette erreur »
- « Tape : bonjour à tous », « Fais Ctrl+S », « Réduis toutes les fenêtres »
- « Minuteur de dix minutes pour les pâtes », « Rappelle-moi à 17 h 30 d'appeler le garage »
- « Retiens que l'adresse de Paul est paul@exemple.fr », « État du PC ? », « Verrouille la session »

## Ce que Jarvis sait faire (53 outils)

- **Applications** : lancer (raccourcis du menu Démarrer, applis du Store, alias de `config.yaml`), fermer, gérer les fenêtres
- **Fichiers** : lister, lire (txt, pdf, docx…), écrire, chercher, copier, déplacer, supprimer (corbeille), ouvrir
- **Web** : ouvrir un site, rechercher (Google, YouTube, Maps…), chercher une réponse sur Internet, lire une page, météo
- **E-mail** : envoyer avec pièces jointes, lire la boîte de réception
- **PC** : volume, lecture multimédia, capture d'écran analysée par l'IA, saisie de texte, raccourcis clavier,
  presse-papiers, état du système, verrouillage / veille / redémarrage / extinction, commandes PowerShell
- **N'importe quel logiciel** : il regarde l'écran (capture), puis clique, fait défiler, tape et vérifie,
  comme vous le feriez à la souris (« clique sur Envoyer », « coche la case en bas »)
- **Réglages Windows** : luminosité, mode sombre, fond d'écran, Wi-Fi, corbeille, pages des Paramètres
- **Logiciels** : chercher, installer, désinstaller, mettre à jour (winget)
- **Divers** : état du réseau, téléchargement d'un fichier, zip / dézip, impression, notifications, liste de tâches
- **Agenda, contacts, tâches** : « ajoute dentiste jeudi à 14 h 30 », « qu'est-ce que j'ai demain ? » ; il
  prévient 15 minutes avant chaque rendez-vous
- **Mémoire des échanges** : la conversation reprend après un redémarrage, et il retrouve dans son historique
  ce qui a été dit ou fait les jours précédents
- **Routines** : « quand je dis mode travail, ouvre Outlook, Chrome et mets le volume à 20 » : il retient et rejoue
- **Mémoire** : retient durablement ce que vous lui dites (stocké dans `data/memoire.json`)
- **Minuteurs et rappels** vocaux, conservés même si Jarvis est relancé

## L'écosystème autour de la voix

**Fenêtre flottante.** En bas à droite : pastille d'état (veille, écoute, réflexion, parole), fil des échanges
et des actions, champ pour taper une demande, boutons Oui / Non pour les confirmations.
Désactivable avec `interface: false`.

**Depuis le téléphone (Telegram).** Écrivez ou dictez un message vocal à votre bot privé : Jarvis agit sur le
PC et répond. « Envoie-moi le PDF du bureau », « fais une capture d'écran et envoie-la-moi », « éteins le PC ».
Un fichier envoyé au bot arrive dans Téléchargements. Seul votre identifiant est accepté. Réglage en deux
lignes dans `.env` (explications dans le fichier).

**Navigateur piloté.** « Va sur le site de la SNCF et cherche un Paris-Lyon vendredi matin », « remplis ce
formulaire avec mes coordonnées ». Jarvis ouvre Edge (ou Chrome), lit la page, clique et saisit. Il utilise
un profil à part : connectez-vous une fois à vos sites dans cette fenêtre, les sessions sont gardées.

**Missions de fond.** « Lance une mission : compare les cinq meilleurs PC portables à 32 Go et fais-moi un
rapport ». Un second Claude travaille seul dans `Documents/Jarvis/missions/<nom>` (recherche web, lecture et
écriture de fichiers), Jarvis reste disponible et annonce la fin. `missions.terminal: true` l'autorise en
plus à lancer des commandes.

**Planning.** « Tous les matins de semaine à 8 h, donne-moi la météo, mes mails non lus et mes tâches. »
« À 18 h, ferme Outlook et lance Spotify. » Jarvis exécute l'instruction à l'heure dite.

**Surveillance.** Il prévient si la batterie passe sous 15 % ou si un disque est presque plein.

**Extensions.** « Crée-toi un outil pour … » : Jarvis écrit lui-même un module Python dans `extensions/`,
le charge à chaud et s'en sert dès la demande suivante. Vous pouvez aussi y déposer vos propres modules
(modèle dans `extensions/_exemple.py.txt`).

## Sécurité

Jarvis demande **« oui ou non ? »** avant : supprimer, envoyer un e-mail, fermer un logiciel, exécuter une
commande PowerShell, éteindre / redémarrer. Les suppressions vont à la corbeille. Une liste noire refuse les
commandes destructrices (formatage, effacement de disque…). Pour tout désactiver : `confirmations: false`
dans `config.yaml` (déconseillé).

## Réglages (`config.yaml`)

- `cerveau.modele_forfait` : `sonnet` (défaut), `haiku` (plus rapide, consomme très peu de forfait),
  `opus` (le plus fort, consomme davantage).
- `cerveau.fournisseur` : `forfait` (défaut), `ollama` (IA locale), `api` (clé API payante, facultatif).
- `voix.tts_voix` : `fr-FR-HenriNeural`, `fr-FR-DeniseNeural`, `fr-FR-RemyMultilingualNeural`…
- `voix.whisper_modele` : `small` par défaut ; `medium` ou `large-v3` avec un bon PC ; `whisper_device: cuda` avec une carte NVIDIA.
- `voix.seuil_reveil` : baissez à 0.3 si « Hey Jarvis » n'est pas détecté, montez à 0.7 s'il se déclenche seul.
- `applications` : ajoutez vos propres noms parlés → programme, dossier ou site.

### E-mail (optionnel)

Dans `.env`, renseignez `JARVIS_EMAIL_ADDRESS` et `JARVIS_EMAIL_PASSWORD`. Pour Gmail, il faut un
**mot de passe d'application** (https://myaccount.google.com/apppasswords), pas le mot de passe habituel.
Sans cela, Jarvis ouvre un brouillon pré-rempli dans votre messagerie et vous cliquez sur Envoyer.

### Passer sur une IA locale (futur PC 32 Go)

1. Installer Ollama (https://ollama.com), puis `ollama pull qwen3:14b` (ou un autre modèle sachant appeler des outils).
2. Dans `config.yaml` : `fournisseur: ollama`.

Tout le reste (voix, outils, mémoire) est identique. La reconnaissance vocale tourne déjà en local ;
avec `tts: sapi`, la synthèse aussi : Jarvis fonctionne alors entièrement hors-ligne. Limites en local :
pas d'analyse des captures d'écran ni de recherche Internet intégrée, et des enchaînements d'actions moins fiables.

### Lancement, démarrage automatique, mise à jour

- `install.bat` crée un raccourci **Jarvis** sur le Bureau (lancement sans fenêtre noire).
- `demarrage-auto.bat` : Jarvis démarre avec Windows.
- `Mise-a-jour.bat` : récupère la dernière version, si le dossier a été installé avec git :
  `git clone -b ccr-4ebd9c8f-428bf3 https://github.com/jbleclercperso/prisme-logiciel Jarvis`

## Dépannage

- « Je ne suis pas connecté à votre compte Claude » : lancez `Connexion-Claude.bat`.
- Jarvis puise dans le même quota que vos autres usages de Claude : forfait épuisé, il le dit et reprend
  quand le quota se recharge (ou passez sur `haiku`, ou sur Ollama).

- **`Autotest.bat`** : teste une à une toutes les fonctions sur votre PC (Claude, fichiers, applications,
  clavier, capture, voix, micro, haut-parleur) et écrit le rapport `data/autotest.txt`. À lancer en premier
  si quelque chose cloche ; le rapport dit exactement quoi.
- `data/journal.log` garde la trace de ce que Jarvis a entendu, fait et répondu.
- `python -m jarvis --diagnostic` : état de l'installation. `python -m jarvis --micros` : liste des micros
  (reportez le numéro dans `voix.micro`).
- Le raccourci Ctrl+Alt+J ne répond pas dans une fenêtre lancée en administrateur : lancez Jarvis en administrateur aussi.
- Pas de son : passez `tts: sapi`.

## Architecture

```
jarvis/
  __main__.py     démarrage, boucle vocale, mode texte
  core.py         coeur partagé : un seul cerveau pour la voix, la fenêtre, Telegram et le planning
  ui.py           fenêtre flottante
  telegram.py     pilotage depuis le téléphone
  autotest.py     autotest complet du PC
  brain.py        cerveau : forfait Claude (Claude Agent SDK), Ollama, ou API ; mêmes outils
  voice.py        micro → mot de réveil (openWakeWord) → faster-whisper → edge-tts / SAPI
  tools/          les outils (fichiers, système, PC, missions, planning, extensions...)
extensions/       vos outils supplémentaires, chargés au démarrage
tests/test_core.py   tests hors-ligne : python tests/test_core.py
```
