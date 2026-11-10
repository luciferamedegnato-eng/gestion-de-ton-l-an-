# Finmee — gestionnaire de dépenses personnelles

Application de finances personnelles en Python : version terminal conservée et PWA installable avec un compte individuel par personne. Chaque compte possède son profil, ses opérations et ses statistiques privées.

## Démarrer en local

```powershell
python -m pip install -r requirements.txt
$env:COOKIE_SECURE = "0"
python -m uvicorn web_app:app --host 127.0.0.1 --port 8000
```

Ouvrir ensuite `http://127.0.0.1:8000`, puis créer un compte avec un nom, une adresse e-mail et un mot de passe d’au moins 10 caractères. Le serveur n’est accessible que depuis l’ordinateur local par défaut. Les comptes, sessions et opérations web sont conservés dans `data/accounts.db`; la version terminal utilise sa base distincte `data/depenses.db`.

Les mots de passe sont stockés sous forme de dérivés PBKDF2, jamais en clair. Les cookies de session sont HttpOnly et expirent après 30 jours. Il n’y a pas encore de confirmation d’adresse e-mail ni de récupération du mot de passe : chaque personne doit conserver son adresse et son mot de passe.

En local, sauvegardez régulièrement `data/accounts.db` pour garder une copie des comptes et informations.

## Données existantes

Les comptes créés auparavant sans connexion ne sont pas automatiquement associés au premier nouveau compte : cela éviterait qu’une autre personne puisse récupérer des données privées. Avant de mettre à jour une ancienne installation, faites une copie de `data/accounts.db`. Après avoir créé votre compte, importez le fichier sauvegardé depuis « Importer mes données » pour récupérer l’ancien espace local. L’import d’une base Finmee récente n’inclut que l’ancien espace local et refuse de fusionner les données des différents comptes.

Pour importer l’historique de la version terminal, ouvrir « Importer mes données » et sélectionner `data/depenses.db`. L’import conserve les dates, descriptions, catégories et montants nominaux ; les montants sont arrondis au franc entier. Les anciennes bases web, dont les montants étaient affichés en euros, sont converties au taux fixe de 1 EUR = 655,957 XOF. Une même base ne peut être importée qu’une seule fois. Le fichier est limité à 12 Mo et 100 000 opérations.

## Franc CFA

L’application utilise le franc CFA BCEAO, code `XOF`, affiché comme `FCFA`. Les nouveaux montants sont des nombres entiers, sans décimales. La base de l’ancienne version terminal ne précisait pas de devise : ses montants numériques sont conservés et arrondis au franc CFA le plus proche.

Une dépense est acceptée même si elle dépasse le solde : le solde cumulé peut devenir négatif et il est alors signalé dans le tableau de bord.

## Installer sur un téléphone

Une PWA doit être ouverte sur HTTPS ou depuis `localhost` pour être installable. Les données financières et les opérations exigent une session connectée; utilisez HTTPS pour tout déploiement accessible depuis Internet.

Le service worker met en cache l’interface, pas les réponses financières ni les opérations : une connexion au serveur reste nécessaire pour consulter ou modifier les données. Sauvegardez régulièrement la base SQLite.

Une image Docker est également fournie. Pour ne publier le port que sur l’ordinateur local :

```powershell
docker build -t finmee .
docker run --rm -p 127.0.0.1:8000:8000 -v finmee-data:/data finmee
```

## Déploiement Vercel

`pyproject.toml` indique à Vercel de charger l’application FastAPI depuis `web_app:app`. Pour que les données survivent aux redéploiements et aux démarrages à froid, l’application utilise PostgreSQL sur Vercel (`DATABASE_URL` ou `POSTGRES_URL`) et SQLite en local.

1. Créez une base PostgreSQL, par exemple avec Neon depuis le Marketplace Vercel, et liez-la au projet.
2. Dans **Project Settings → Environment Variables**, vérifiez qu’une URL PostgreSQL (`DATABASE_URL` ou `POSTGRES_URL`) est configurée pour **Production**. Si vous activez les déploiements Preview, utilisez de préférence une base séparée pour éviter de mélanger les données de test et celles des utilisateurs. Ne publiez jamais ces URL dans le dépôt ou dans un message.
3. Pour que chacun puisse ouvrir le site et créer son propre compte Finmee, désactivez **Deployment Protection / Vercel Authentication** sur le déploiement public. La connexion Finmee protège ensuite chaque espace séparément. Si la protection Vercel reste active, seuls les visiteurs autorisés par Vercel pourront atteindre l’écran de connexion.
4. Redéployez après avoir configuré PostgreSQL. Sans URL de base persistante, l’application refuse explicitement de démarrer sur Vercel.

Chaque nouveau compte commence avec son espace vide. Les données de l’ancienne base locale ne sont pas copiées automatiquement : sauvegardez-la, puis utilisez **Importer mes données** après connexion. Conservez la sauvegarde originale. L’authentification intégrée protège les API et filtre chaque lecture/écriture par l’identifiant du compte connecté.

## Tests

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest -v
```
