"""Connexion OAuth Google (appli « de bureau ») pour le pilote.

Prérequis (une fois, voir GUIDE_PILOTE.md) : un projet Google Cloud avec l'API
Google Calendar activée, un écran de consentement « Externe » avec ton adresse
en utilisateur test, et un identifiant OAuth de type « Application de bureau »
téléchargé dans ~/.ai_coach/google_client.json.

⚠ Tant que l'appli est en mode « Test » chez Google, le jeton expire au bout
de 7 jours : relancer `python -m coach.pilot google` (10 s).
"""
from __future__ import annotations

import os
from pathlib import Path

from .calendar import SCOPES_PRIMARY


def scopes() -> list[str]:
    env = os.getenv("COACH_GOOGLE_SCOPES")
    return env.split(",") if env else SCOPES_PRIMARY


def credentials(home: Path, interactive: bool = True):
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token_file, client_file = home / "google_token.json", home / "google_client.json"
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), scopes())
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            creds = None                               # jeton de test expiré (7 j) : on refait l'accord
    if not creds or not creds.valid:
        if not interactive:
            raise RuntimeError("accès Google expiré : relance `python -m coach.pilot google` sur ton Mac")
        if not client_file.exists():
            raise FileNotFoundError(f"{client_file} introuvable : télécharge l'identifiant OAuth "
                                    "« Application de bureau » depuis Google Cloud (voir GUIDE_PILOTE.md)")
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(str(client_file), scopes())
        creds = flow.run_local_server(port=0, prompt="consent",
                                      success_message="Google Agenda connecté ✅ Tu peux fermer cet onglet.")
    token_file.write_text(creds.to_json())
    token_file.chmod(0o600)
    return creds


def calendar_service(home: Path, interactive: bool = True):
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=credentials(home, interactive), cache_discovery=False)
