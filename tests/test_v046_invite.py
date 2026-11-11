"""v0.46 : espace invité (2e personne sur le même serveur, données séparées)."""
from pathlib import Path

D = Path(__file__).resolve().parent.parent / "deploy"


def test_invite_files_are_consistent():
    comp = (D / "docker-compose.invite.yml").read_text()
    assert "env_file: invite.env" in comp and "invite_data:/data" in comp
    assert "./Caddyfile.invite:/etc/caddy/Caddyfile:ro" in comp            # même cible : remplace le Caddyfile
    caddy = (D / "Caddyfile.invite").read_text()
    assert "{$DOMAIN} {" in caddy and "reverse_proxy app:8000" in caddy     # ton application, inchangée
    assert "invite.{$DOMAIN} {" in caddy and "reverse_proxy invite:8000" in caddy
    main = (D / "Caddyfile").read_text()
    assert "invite" not in main                                             # sans invité : rien ne change
    dep = (D / "deploy.sh").read_text()
    assert '[ -f invite.env ] && FILES+=(-f docker-compose.invite.yml)' in dep and '"${FILES[@]}" up -d --build' in dep
    add = (D / "add-invite.sh").read_text()
    assert "COACH_BACKUP_URL" not in add                                    # jamais tes sauvegardes distantes
    assert "COACH_WEB_SECRET" not in add
    gi = (D.parent / ".gitignore").read_text()
    assert "deploy/invite.env" in gi
