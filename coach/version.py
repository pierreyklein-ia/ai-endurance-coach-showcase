"""Version de l'application : lue dans le fichier VERSION à la racine du paquet (une seule source de vérité)."""
from pathlib import Path


def app_version() -> str:
    try:
        return (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip() or "inconnue"
    except OSError:
        return "inconnue"
