# Prisme TV

La bibliothèque de Prisme sur la télé : Android TV, Google TV, Fire TV
(Amazon), Nvidia Shield, box Android. On parcourt les dossiers à la
télécommande, on cherche, on regarde. Les vidéos viennent du PC par le Wi-Fi
de la maison : rien n'est copié sur la télé.

## Ce qu'il faut

- Sur le PC : Prisme ouvert, avec le **partage à distance activé**
  (⋯ › Connexion › Partage à distance). Pas besoin de tunnel ni d'adresse
  publique à la maison.
- La télé et le PC sur le **même réseau**. Le PC ne doit pas se mettre en
  veille pendant qu'on regarde (son écran, lui, peut s'éteindre).

## Installer

L'application n'est pas sur le Play Store ni sur l'Appstore d'Amazon (tous
deux refusent les applications pour adultes) : elle s'installe à la main, une
fois. Le fichier `Prisme-TV-….apk` se récupère sur GitHub : onglet
**Actions › Prisme TV**, dernière exécution réussie, rubrique *Artifacts*.

**Fire TV** (le plus simple) :
1. *Paramètres › Ma Fire TV › Options pour les développeurs* : activer
   *Applications de sources inconnues* (ou, sur les modèles récents,
   l'autoriser pour l'application Downloader). Si le menu n'existe pas :
   *Paramètres › Ma Fire TV › À propos*, appuyer 7 fois sur le nom de l'appareil.
2. Installer l'application **Downloader** (gratuite, dans l'Appstore).
3. Dans Downloader, saisir l'adresse où se trouve l'APK (par exemple un
   lien de téléchargement sur le NAS ou le site), puis *Installer*.

**Android TV / Google TV** :
1. *Paramètres › Système › À propos* : appuyer 7 fois sur *Build Android
   TV OS* pour les options de développeur, puis autoriser les sources
   inconnues pour le gestionnaire de fichiers utilisé.
2. Copier l'APK par une clé USB, ou l'envoyer avec l'application
   *Send files to TV*, puis l'ouvrir.

**Depuis le PC, sans rien installer sur la télé** (pour les habitués) : activer
le *débogage ADB* sur la télé, puis `adb connect <adresse-de-la-télé>` et
`adb install -r Prisme-TV-….apk`.

Les versions suivantes s'installent par-dessus : la télé reste appairée.

## Premier lancement

La télé cherche Prisme sur le réseau et affiche les PC trouvés. On choisit le
sien : **sur le PC, Prisme demande d'autoriser la télé**. Une fois autorisée,
elle s'ouvre ensuite directement sur la bibliothèque, sans mot de passe.

Si la recherche ne trouve rien (certaines box isolent les appareils du Wi-Fi) :
*Saisir l'adresse à la main*, avec l'adresse du PC (par exemple
`192.168.1.20`) ou le lien d'invitation affiché dans Partage à distance.

## À la télécommande

| Touche | Dans la bibliothèque | Dans le lecteur |
|---|---|---|
| Flèches | aller d'une carte à l'autre | ← → : reculer, avancer de 10 s ; ↑ : la barre |
| OK | ouvrir le dossier, lancer la vidéo | pause, reprise |
| Retour | revenir au dossier d'avant | fermer la vidéo |
| ⏯ ⏪ ⏩ | | pause ; 30 s en arrière, en avant |
| ☰ Menu | recharger, ou changer de PC | |

Pour déconnecter une télé : Partage à distance › *Nouveau lien* (toutes les
personnes entrées par lien sont alors déconnectées).

## Pour le développeur

- `app/src/main/java/app/prisme/tv/MainActivity.java` : l'appairage et la page.
  La page elle-même vient du PC (`web.py`), qui se met en mode télé quand le
  navigateur se nomme `PrismeTV`.
- Recherche et appairage : `tv/pc/tvlink.py`, UDP port 8714.
- `prisme-tv.keystore` signe toutes les versions : sans une signature constante,
  chaque mise à jour obligerait à désinstaller. Elle est gardée hors du dépôt
  (voir « Dans Prisme » plus bas).
- Construction : `.github/workflows/prisme-tv.yml`, ou en local avec Android
  Studio (ouvrir le dossier `tv/`).

## Dans Prisme (depuis le 23/10/2026)

Prisme TV vivait sur une branche de l'ancien dépôt privé « VideoSorter ». Le voici rangé dans Prisme :

- `tv/` : l'application télé (inchangée). Le dépôt de Prisme est **public** : la clé de signature
  (`tv/prisme-tv.keystore`) et ses mots de passe (`tv/signature.properties`) n'y sont **jamais** envoyés
  (`.gitignore`). Ils sont sur ce PC et en copie sur le NAS, dans `Dev/fichiers/cles/prisme-tv/`.
  **Sans cette clé, impossible d'installer une mise à jour par-dessus l'appli déjà sur la télé.**
- `tv/pc/tvlink.py` : la partie PC qui répond aux télés du réseau (« Autoriser cette télé »).
- `tv/integration/web-et-fenetre.patch` : le mode télé de la page de partage et le bouton d'accord dans la
  fenêtre, écrits pour l'ancien code. **Pas encore reportés** dans le Prisme actuel : c'est la prochaine étape
  pour que Prisme TV marche de bout en bout.
- `.github/workflows/prisme-tv.yml` : construction sur GitHub, à la main, une fois les secrets du dépôt en
  place (`PRISME_TV_KEYSTORE_B64`, `PRISME_TV_STORE_PASSWORD`, `PRISME_TV_KEY_PASSWORD`).
