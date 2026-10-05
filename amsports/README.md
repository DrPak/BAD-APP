# Am'Sports – Paiement (Python + HelloAsso)

## Lancer en local (test)
```
pip install -r requirements.txt
export HELLOASSO_API_BASE=https://api.helloasso-sandbox.com
export HELLOASSO_ORG_SLUG=...  HELLOASSO_CLIENT_ID=...  HELLOASSO_CLIENT_SECRET=...
export BASE_URL=http://localhost:5000
python -m flask --app app run
```
(HelloAsso exige du https pour les URLs de retour : en local, teste plutôt directement sur Render.)

## Liste des personnes
Colonnes : `ref,nom,montant,paye` (paye optionnel : oui / vide).
- Test : fichier `people.csv`.
- Prod : Google Sheet > Fichier > Partager > Publier sur le web > format CSV, puis variable `SHEET_CSV_URL`.

## Déployer sur Render
1. Mets ce dossier sur un repo GitHub, Render > New > Blueprint (render.yaml).
2. Renseigne les variables : BASE_URL (ex. https://amsports-paiement.onrender.com), HELLOASSO_ORG_SLUG, HELLOASSO_CLIENT_ID, HELLOASSO_CLIENT_SECRET, SHEET_CSV_URL.
3. Teste en sandbox (HELLOASSO_API_BASE=https://api.helloasso-sandbox.com), puis passe à https://api.helloasso.com avec les identifiants de production.
4. Dans HelloAsso (espace asso > API / notifications), mets l'URL de notification : BASE_URL/webhook/helloasso

## Sécurité
- Le montant vient de la liste côté serveur, jamais du navigateur.
- "Payé" n'est enregistré qu'après vérification auprès de l'API HelloAsso (retour client et webhook).
- Identifiants HelloAsso uniquement en variables d'environnement.
- Le disque persistant (/data) garde les statuts payés ; sans disque, ils sont perdus à chaque redéploiement.
