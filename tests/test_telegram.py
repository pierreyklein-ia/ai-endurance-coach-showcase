"""Bot Telegram testé avec un faux client HTTP (aucun appel réseau)."""
from datetime import date

from coach.adapters.telegram_bot import CoachBot, TelegramAPI, pseudonym

ME, STRANGER = 111, 999


class FakeHTTP:
    def __init__(self):
        self.sent = []

    def __call__(self, url, params, timeout):
        if url.endswith("/sendMessage"):
            self.sent.append((params["chat_id"], params["text"]))
            return {"ok": True, "result": {}}
        return {"ok": True, "result": []}


def bot():
    http = FakeHTTP()
    return CoachBot(TelegramAPI("TOKEN", http), allowed={ME}, today=date(2026, 9, 30)), http


def msg(chat, text=None, **extra):
    m = {"chat": {"id": chat}, **extra}
    if text is not None:
        m["text"] = text
    return {"update_id": 1, "message": m}


def test_stranger_gets_chat_id_but_no_coaching():
    b, http = bot()
    b.handle_update(msg(STRANGER, "salut"))
    assert "999" in http.sent[0][1] and "non autorisé" in http.sent[0][1]
    assert b.deps.llm.usage_log == []


def test_start_shows_agenda_in_pre_block():
    b, http = bot()
    b.handle_update(msg(ME, "/start"))
    assert "<pre>" in http.sent[0][1] and "Seuil" in http.sent[0][1]


def test_coaching_message_updates_agenda_and_escapes_html():
    b, http = bot()
    b.handle_update(msg(ME, "Ma fille a été malade toute la nuit, je suis épuisé <3"))
    text = http.sent[-1][1]
    assert "footing cool" in text and "Agenda mis à jour" in text


def test_voice_note_is_politely_declined():
    b, http = bot()
    b.handle_update(msg(ME, voice={"file_id": "x", "duration": 12}))
    assert "vocales" in http.sent[0][1] and b.deps.llm.usage_log == []


def test_confirmation_flow_over_telegram():
    b, http = bot()
    b.handle_update(msg(ME, "Mon genou me fait mal, 5/10"))
    assert "oui / non" in http.sent[-1][1]
    b.handle_update(msg(ME, "ouais vas-y"))
    assert "C'est dans ton agenda" in http.sent[-1][1]


def test_chat_id_is_pseudonymised():
    assert "111" not in pseudonym(111, "s") and pseudonym(111, "s") != pseudonym(111, "t")
