"""Image de l'avatar (v0.25) : générée par le modèle d'image de Gemini (« Nano Banana »), en option.

- Activé seulement si GEMINI_API_KEY est dans deploy/app.env (clé Google AI Studio). Sans clé : avatar dessiné (SVG).
- Envoyé à Google : uniquement la description de l'avatar (couleurs, coiffure, équipement, décor) et l'image
  précédente pour garder le même personnage. Aucune donnée d'entraînement, aucun nom, aucune photo.
- Coût : ~0,04 $ par image (modèle gemini-2.5-flash-image). Une image par changement (niveau, équipement,
  personnalisation), au plus COACH_IMAGE_PER_DAY par jour (défaut 4), comptée dans le budget du jour.
- Réglages : COACH_IMAGE_MODEL (défaut gemini-2.5-flash-image), COACH_IMAGE_PER_DAY, COACH_IMAGE_EVERY_H (v0.30,
  défaut 48 : une nouvelle image au plus toutes les 48 h).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path

log = logging.getLogger("coach.avatar")

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
IMAGE_EUR = 0.036                                    # ~0,039 $ par image

SKIN = ["very fair", "fair", "light tan", "tan", "brown", "dark brown"]
HAIR = {"court": "short hair", "long": "a long ponytail", "chignon": "hair tied in a bun", "boucles": "curly hair",
        "rase": "a shaved head"}
HAIR_COLOR = ["black", "dark brown", "chestnut", "blond", "grey", "red"]
SHIRT = ["forest green", "blue", "red", "orange", "purple", "navy", "pink", "teal"]
SHORTS = ["black", "slate grey", "navy", "dark green", "burgundy", "light grey"]
SHOES = ["orange", "green", "red", "blue", "yellow", "white"]
GEAR_EN = {"bandeau": "a red headband", "montre": "a GPS sports watch", "casquette": "a white trail running cap",
           "gilet": "a trail running vest with two soft flasks", "batons": "folding trail poles",
           "frontale": "a headlamp", "dossard": "a race bib with the number {level}",
           "medaille": "a finisher medal around the neck", "laurier": "a laurel wreath on the head",
           "chaussures_or": "golden running shoes", "aura": "a subtle glowing golden aura",
           "lunettes": "sporty wraparound sunglasses", "buff": "a colorful neck tube scarf (buff) around the neck",
           "manchons": "black calf compression sleeves on both lower legs", "coupe_vent": "a light windbreaker jacket",
           "gants": "thin running gloves", "guetres": "short trail gaiters over the shoes",
           "poignets": "terry wristbands on both wrists", "chapeau_sorciere": "a black pointy witch hat",
           "bonnet_noel": "a red and white Santa hat", "chapeau_fete": "a colorful cone birthday party hat",
           "cotillons": "colorful confetti and party streamers floating around",
           "camelbak": "a large ultra-trail hydration backpack (camelbak style) with a drinking tube over the shoulder"}
# v0.47 : le lieu (paysage) et l'ambiance (saison, neige, nuit) se croisent : « on a snowy beach », « at night »…
PLACE_EN = {"route": "a running path through a city park", "collines": "a trail over green rolling hills",
            "montagne": "an alpine mountain trail with rocky peaks", "sommet": "a mountain summit above a sea of clouds",
            "foret": "a forest trail between tall trees", "ville": "a city street lined with buildings",
            "bord_de_mer": "a coastal path above a sandy beach and the sea",
            "volcan": "a volcanic landscape with a crater and black lava rocks, tropical island in the distance"}
AMBIANCE_EN = {"aucune": "on a bright sunny day",
               "printemps": "in spring, with blossoming trees, flowers and fresh green grass",
               "ete": "in summer, under a warm sun and a deep blue sky",
               "automne": "in autumn, with orange and red foliage and falling leaves",
               "hiver": "in winter, cold bluish light, frost on the ground and a light dusting of snow on the heights",
               "neige": "entirely covered in fresh white snow (snow on the ground, the rocks, the trees and even the sand), "
                        "snowflakes falling",
               "nuit": "at night under a starry sky with a bright full moon and moonlight reflections"}
SNOWY = {"bord_de_mer": "a snowy beach above the sea", "volcan": "a snow-capped volcano with a crater and black lava "
         "rocks", "montagne": "a snowy mountain trail", "foret": "a snowy forest trail between snow-covered trees"}
AFTER_DARK = {"ville": "a city street lined with buildings with lit windows", "volcan": "a volcano with glowing red lava "
              "and black rocks", "bord_de_mer": "a coastal path above a sandy beach, the moon reflecting on the sea"}
SCENE_EN = {**PLACE_EN, **AMBIANCE_EN}               # avant v0.47 : un décor unique (gardé pour compatibilité)


def background(place: str | None, ambiance: str | None = None) -> str:
    """Décor décrit pour Gemini : le lieu, puis l'ambiance (« a snowy beach above the sea, entirely covered… »)."""
    if place in AMBIANCE_EN and place != "aucune":        # ancien décor unique qui était une ambiance (« hiver »)
        place, ambiance = "route", ambiance if ambiance not in (None, "aucune") else place
    place = place if place in PLACE_EN else "route"
    ambiance = ambiance if ambiance in AMBIANCE_EN else "aucune"
    where = (SNOWY.get(place) if ambiance == "neige" else AFTER_DARK.get(place) if ambiance == "nuit" else None) \
        or PLACE_EN[place]
    if place == "sommet" and ambiance == "aucune":
        return "a mountain summit at golden sunrise above a sea of clouds"
    return f"{where}, {AMBIANCE_EN[ambiance]}"


def enabled() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


def model() -> str:
    return os.environ.get("COACH_IMAGE_MODEL", "gemini-2.5-flash-image")


def per_day() -> int:
    try:
        return max(0, int(os.environ.get("COACH_IMAGE_PER_DAY", "4")))
    except ValueError:
        return 4


def every_hours() -> int:
    """Une nouvelle image au plus toutes les N heures (v0.30, défaut 48), pour maîtriser les frais."""
    try:
        return max(0, int(os.environ.get("COACH_IMAGE_EVERY_H", "48")))
    except ValueError:
        return 48


def prompt(cfg: dict, level: int, worn: list[str], scene: str | None, discipline: str | None,
           ambiance: str | None = None) -> str:
    """`scene` : le lieu (ou un ancien décor unique) ; `ambiance` : saison, neige, nuit ou « aucune » (v0.47)."""
    def pick(lst, i):
        return lst[i] if isinstance(i, int) and 0 <= i < len(lst) else lst[0]
    hair = "a shaved head" if cfg.get("hair") == "rase" else f"{pick(HAIR_COLOR, cfg.get('hair_color'))} " \
                                                              f"{HAIR.get(cfg.get('hair'), 'short hair')}"
    gear = [GEAR_EN[g].format(level=level) for g in worn if g in GEAR_EN]
    stars = max(0, level - 20)
    where = background(scene, ambiance)
    if discipline == "route" and scene in ("collines", "montagne"):
        amb = ambiance if ambiance in AMBIANCE_EN else "aucune"
        where = f"a scenic road along the coast, {AMBIANCE_EN[amb]}"
    return (
        "Create an original, friendly character illustration for a running app avatar: one adult runner, "
        "full body, in a dynamic running pose seen from the side, facing right, centered. "
        f"Skin tone: {pick(SKIN, cfg.get('skin'))}. Hair: {hair}." + (" Short beard." if cfg.get("beard") else "")
        + f" Wearing a {pick(SHIRT, cfg.get('shirt'))} technical running t-shirt, {pick(SHORTS, cfg.get('shorts'))} "
          f"running shorts and {pick(SHOES, cfg.get('shoes'))} running shoes."
        + (f" The runner MUST clearly wear ALL of these accessories, each one well visible: {'; '.join(gear)}."
           if gear else " No accessories.")
        + f" Background: {where}."
        + (f" The runner looks experienced and confident (level {level}"
           + (f", {stars} small golden stars floating above" if stars else "") + ")." if level >= 5 else "")
        + " Style: polished stylized 3D cartoon, soft lighting, vibrant but natural colors, clean background, "
          "portrait 4:5. No text, no letters except the bib number, no brand logos, no real person, no watermark.")


FACE_MIME = {b"\xff\xd8\xff": "image/jpeg", b"\x89PNG": "image/png", b"RIFF": "image/webp"}
FACE_MAX = 5 * 1024 * 1024


def face_mime(data: bytes) -> str | None:
    """Type d'une photo du visage (JPEG, PNG, WebP, HEIC d'iPhone) d'après ses premiers octets ; None si refusé."""
    for magic, mime in FACE_MIME.items():
        if data.startswith(magic) and (mime != "image/webp" or data[8:12] == b"WEBP"):
            return mime
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftypheif"):
        return "image/heic"
    return None


def key_for(cfg: dict, level: int, worn: list[str], scene: str | None, discipline: str | None, face: str | None = None,
            ambiance: str | None = None) -> str:
    """Clé de cache de l'image : tout ce qui change le dessin, dont le lieu ET l'ambiance (v0.47)."""
    raw = json.dumps([cfg, level, sorted(worn), scene, discipline, model(), face] + ([ambiance] if ambiance else []),
                     sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class GeminiError(RuntimeError):
    """Erreur de l'API Gemini avec le message renvoyé par Google (lisible dans l'app)."""


def api_message(r) -> str:
    """Message d'erreur de Google (« API key not valid… », « User location is not supported… »), sinon le code HTTP."""
    try:
        e = (r.json() or {}).get("error") or {}
        msg = (e.get("message") or "").strip()
        st = e.get("status") or ""
        if msg:
            return f"HTTP {r.status_code} {st} : {msg}"[:260]
    except ValueError:
        pass
    return f"HTTP {r.status_code} : {(r.text or '')[:160]}"


def check_key(client=None) -> tuple[bool, str]:
    """Teste la clé et le nom du modèle SANS générer d'image (appel gratuit : description du modèle)."""
    import httpx
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return False, "aucune clé GEMINI_API_KEY dans deploy/app.env"
    http = client or httpx.Client(timeout=20.0)
    try:
        r = http.get(f"https://generativelanguage.googleapis.com/v1beta/models/{model()}",
                     headers={"x-goog-api-key": api_key})
    except Exception as e:                                       # noqa: BLE001  réseau
        return False, f"Google injoignable : {type(e).__name__}"
    if r.status_code == 200:
        return True, f"clé valide, modèle {model()} trouvé"
    return False, api_message(r)


def generate(text: str, reference: bytes | None = None, client=None,
             face: tuple[bytes, str] | None = None) -> bytes:
    """Appel REST de Gemini ; renvoie le PNG. Lève GeminiError (message de Google) en cas d'échec."""
    import httpx
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise GeminiError("GEMINI_API_KEY absente")
    parts: list[dict] = [{"text": text}]
    if reference:
        parts[0]["text"] = ("Keep exactly the same character (face, body, hair, style) as in the reference image. "
                            "Redraw the outfit, accessories and background exactly as described below: add every listed "
                            "accessory even if it is missing from the reference, and remove any accessory that is not "
                            "listed. " + text)
        parts.append({"inline_data": {"mime_type": "image/png", "data": base64.b64encode(reference).decode()}})
    if face:                                                  # v0.34 : photo de l'athlète, pour la ressemblance
        parts[0]["text"] += (" The LAST image is a photo of the real runner: make the character clearly resemble this "
                             "person (face shape, skin tone, hair, beard, glasses), but keep the same stylized 3D cartoon "
                             "style, not a photo. Ignore the photo's background and clothes.")
        parts.append({"inline_data": {"mime_type": face[1], "data": base64.b64encode(face[0]).decode()}})
    http = client or httpx.Client(timeout=90.0)
    url = ENDPOINT.format(model=model())
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    # Variantes essayées dans l'ordre quand Google répond 400 : selon le modèle, imageConfig ou le mode
    # « image seule » peuvent être refusés. Une clé invalide arrête tout de suite (inutile de réessayer).
    variants = [(["IMAGE"], {"aspectRatio": "4:5"}), (["IMAGE"], None), (["TEXT", "IMAGE"], None)]
    last = ""
    for modalities, image_config in variants:
        cfg: dict = {"responseModalities": modalities}
        if image_config:
            cfg["imageConfig"] = image_config
        r = http.post(url, headers=headers, json={"contents": [{"parts": parts}], "generationConfig": cfg})
        if r.status_code == 200:
            return extract_image(r.json())
        last = api_message(r)
        low = last.lower()
        if r.status_code != 400 or "api key" in low or "api_key" in low or "location" in low:
            break
    raise GeminiError(last)


def extract_image(js: dict) -> bytes:
    for cand in js.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            data = part.get("inlineData") or part.get("inline_data")
            if data and data.get("data"):
                return base64.b64decode(data["data"])
    reason = (js.get("promptFeedback") or {}).get("blockReason") or \
        ((js.get("candidates") or [{}])[0].get("finishReason"))
    raise RuntimeError(f"pas d'image dans la réponse ({reason or 'inconnu'})")


def save(home: Path, key: str, png: bytes) -> Path:
    d = home / "avatars"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"avatar-{key}.png"
    p.write_bytes(png)
    old = sorted(d.glob("avatar-*.png"), key=lambda x: x.stat().st_mtime)[:-6]   # on garde les 6 dernières
    for x in old:
        x.unlink(missing_ok=True)
    return p
