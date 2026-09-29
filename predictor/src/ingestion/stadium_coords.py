"""Coordonnées GPS des stades par équipe (normalisées, pour requête météo).

Clés = fragments du nom d'équipe en minuscules sans accents.
La fonction get_coords() essaie chaque fragment du nom d'équipe.
"""

from __future__ import annotations

import unicodedata

# (latitude, longitude) du stade domicile
_COORDS: dict[str, tuple[float, float]] = {
    # Ligue 1
    "paris":        (48.8414, 2.2530),   # Parc des Princes
    "psg":          (48.8414, 2.2530),
    "marseille":    (43.2699, 5.3947),   # Vélodrome
    "lyon":         (45.7654, 4.9822),   # Groupama Stadium
    "monaco":       (43.7272, 7.4148),   # Stade Louis-II
    "lille":        (50.6119, 3.1301),   # Pierre-Mauroy
    "nice":         (43.7052, 7.1924),   # Allianz Riviera
    "rennes":       (48.1075, -1.7074),  # Roazhon Park
    "lens":         (50.4327, 2.8155),   # Bollaert-Delelis
    "montpellier":  (43.6182, 3.8110),   # La Mosson
    "strasbourg":   (48.5553, 7.7501),   # La Meinau
    "nantes":       (47.2556, -1.5225),  # Beaujoire
    "toulouse":     (43.5824, 1.4334),   # Stadium de Toulouse
    "reims":        (49.2416, 4.0299),   # Auguste-Delaune
    "brest":        (48.4012, -4.5008),  # Francis-Le Blé
    "havre":        (49.4893, -0.1044),  # Stade Océane
    "etienne":      (45.4607, 4.3905),   # Geoffroy-Guichard
    "auxerre":      (47.8064, 3.5714),   # Abbé-Deschamps
    "angers":       (47.4733, -0.5543),  # Raymond-Kopa
    "metz":         (49.1092, 6.1760),   # Saint-Symphorien
    "lorient":      (47.7481, -3.3693),  # Le Moustoir
    "troyes":       (48.2973, 4.0747),   # Stade de l'Aube
    "clermont":     (45.7858, 3.1325),   # Gabriel-Montpied
    "bordeaux":     (44.8282, -0.5562),  # Matmut Atlantique
    "caen":         (49.1654, -0.3706),  # Stade Michel d'Ornano
    "guingamp":     (48.5684, -3.1460),  # Roudourou
    "amiens":       (49.8881, 2.5073),   # Stade de la Licorne
    "nimes":        (43.8397, 4.3572),   # Costières
    "dijon":        (47.3260, 5.0699),   # Gaston-Gérard
    # France NT
    "france":       (48.9244, 2.3601),   # Stade de France (Saint-Denis)
    # Quelques stades NT adversaires fréquents
    "germany":      (51.4927, 7.4519),   # Signal Iduna Park (fréquent)
    "spain":        (40.4531, -3.6883),  # Santiago Bernabéu
    "england":      (51.5560, -0.1400),  # Wembley
    "portugal":     (38.7522, -9.1848),  # Estádio da Luz
    "italy":        (45.4787, 9.1237),   # San Siro
    "netherlands":  (52.3143, 4.9419),   # Johan Cruyff ArenA
    "belgium":      (50.7921, 4.3611),   # Roi Baudouin
    "croatia":      (45.7999, 15.9795),  # Maksimir
}


def _normalize(s: str) -> str:
    s = s.lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s


def get_coords(team_name: str) -> tuple[float, float] | None:
    """Retourne (lat, lon) pour le stade domicile de l'équipe, ou None si inconnu."""
    norm = _normalize(team_name)
    # Essai exact
    if norm in _COORDS:
        return _COORDS[norm]
    # Essai sur chaque fragment du nom
    for fragment in norm.split():
        if len(fragment) >= 4 and fragment in _COORDS:
            return _COORDS[fragment]
    # Essai partiel (le nom contient une clé connue)
    for key, coords in _COORDS.items():
        if key in norm:
            return coords
    return None
