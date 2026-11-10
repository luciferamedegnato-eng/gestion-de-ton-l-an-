# Finmee — gestionnaire local de dépenses

Application de finances personnelles en Python : version terminal conservée et PWA installable, pour un seul utilisateur sur un appareil ou un serveur privé. Aucune connexion, aucun compte, aucun mot de passe, aucun code OTP et aucun service Brevo ne sont nécessaires.

## Démarrer en local

```powershell
python -m pip install -r requirements.txt
$env:COOKIE_SECURE = "0"
python -m uvicorn web_app:app --host 127.0.0.1 --port 8000
```

Ouvrir ensuite `http://127.0.0.1:8000`. Le serveur n’est accessible que depuis l’ordinateur local par défaut. Les données web sont conservées dans `data/accounts.db`; la version terminal utilise sa base distincte `data/depenses.db`.

L’application web n’a pas d’authentification : toute personne pouvant atteindre son serveur peut consulter et modifier ses opérations. Ne l’hébergez pas sur Internet, ne transférez pas son port depuis votre routeur et ne l’exposez pas à un réseau public. Pour y accéder depuis un téléphone, utilisez uniquement un réseau privé de confiance ou un VPN privé correctement configuré.

Lors du premier démarrage, saisissez le nom à afficher. Le nom et les opérations financières sont conservés dans le fichier SQLite local `data/accounts.db`, même après fermeture du navigateur ou redémarrage du serveur. Sélectionnez votre nom en bas du menu pour le modifier. Sauvegardez ce fichier pour garder une copie de vos informations.

## Données existantes

Au premier démarrage, les opérations des comptes web existants sont regroupées dans l’espace local unique afin de conserver l’historique. Les sessions, codes OTP et tables de limitation obsolètes sont supprimés; les anciennes données de transaction sont conservées. Faites une copie de `data/accounts.db` avant cette migration si vous souhaitez garder une sauvegarde indépendante.

Pour importer l’historique de la version terminal, ouvrir « Importer mes données » et sélectionner `data/depenses.db`. L’import conserve les dates, descriptions, catégories et montants nominaux ; les montants sont arrondis au franc entier. Les anciennes bases web, dont les montants étaient affichés en euros, sont converties au taux fixe de 1 EUR = 655,957 XOF. Une même base ne peut être importée qu’une seule fois. Le fichier est limité à 12 Mo et 100 000 opérations.

## Franc CFA

L’application utilise le franc CFA BCEAO, code `XOF`, affiché comme `FCFA`. Les nouveaux montants sont des nombres entiers, sans décimales. La base de l’ancienne version terminal ne précisait pas de devise : ses montants numériques sont conservés et arrondis au franc CFA le plus proche.

## Installer sur un téléphone

Une PWA doit être ouverte sur HTTPS ou depuis `localhost` pour être installable. Pour garder les données privées, n’exposez pas l’application sans authentification sur Internet. Un accès depuis le téléphone est réservé à un réseau privé de confiance ou à un VPN privé; toute personne ayant accès à ce réseau peut consulter et modifier les données.

Le service worker met en cache l’interface, pas les réponses financières ni les opérations : une connexion au serveur reste nécessaire pour consulter ou modifier les données. Sauvegardez régulièrement la base SQLite.

Une image Docker est également fournie. Pour ne publier le port que sur l’ordinateur local :

```powershell
docker build -t finmee .
docker run --rm -p 127.0.0.1:8000:8000 -v finmee-data:/data finmee
```

## Déploiement Vercel

`pyproject.toml` indique à Vercel de charger l’application FastAPI depuis `web_app:app`. Pour que les données survivent aux redéploiements et aux démarrages à froid, l’application utilise PostgreSQL sur Vercel (`DATABASE_URL` ou `POSTGRES_URL`) et SQLite en local.

1. Créez une base PostgreSQL, par exemple avec Neon depuis le Marketplace Vercel, et liez-la au projet.
2. Dans **Project Settings → Environment Variables**, vérifiez qu’une URL de connexion PostgreSQL (`DATABASE_URL` ou `POSTGRES_URL`) est configurée pour **Production**. Ajoutez-la aussi aux environnements Preview si vous les utilisez. Ne publiez jamais cette URL dans le dépôt ou dans un message.
3. Dans **Project Settings → Deployment Protection**, activez **Vercel Authentication** et choisissez une portée qui protège **tous les déploiements**, y compris le domaine Production, avant de rendre l’application accessible. Seules les personnes autorisées dans Vercel pourront alors ouvrir le site; l’application elle-même n’a pas de comptes utilisateurs.
4. Redéployez après avoir configuré la base et la protection. Sans URL PostgreSQL, l’application refuse explicitement de démarrer sur Vercel.

La base PostgreSQL commence vide. Les opérations de l’ancienne base locale `data/accounts.db` ne sont pas transférées automatiquement : après avoir ouvert le déploiement protégé, utilisez **Importer mes données** pour importer l’ancienne base. Conservez une copie de sauvegarde. La protection Vercel est indispensable, car l’application n’a pas d’authentification intégrée.

## Tests

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest -v
```
