# crapy

Outil GUI (Tkinter + Playwright) pour :

1. récupérer les URLs produits depuis une page listing (et ses pages suivantes),
2. scraper les fiches produits,
3. exporter les résultats en CSV / JSON.

## Données récupérées

Pour chaque produit, l'outil tente d'extraire :

- `url`
- `title`
- `image`
- `description`

## Usage rapide

```bash
python crapy.py
```

Puis dans l'interface :

1. renseigner l'URL de listing (ex: `.../page1.htm`),
2. indiquer le préfixe des URLs produits (ex: `https://www.king-jouet.com/jeu-jouet/`),
3. choisir `Nb pages listing` pour parcourir `page1.htm`, `page2.htm`, etc.,
4. cliquer `1) Récupérer URLs produits`,
5. sélectionner les URLs voulues,
6. cliquer `2) Scraper fiches sélectionnées`.

## Bonnes pratiques anti-blocage (responsables)

Le logiciel inclut des protections **non agressives** :

- pauses aléatoires entre requêtes,
- détection des pages de challenge/captcha,
- arrêt/skip en cas de blocage détecté.

> Important : cet outil est prévu pour un scraping respectueux (conditions d'utilisation du site, robots.txt, fréquence raisonnable). Il ne contourne pas les protections anti-bot.
