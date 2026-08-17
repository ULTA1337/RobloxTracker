import logging
import os
import threading
import time

import requests
from flask import Flask

app = Flask(__name__)


@app.get("/")
def home():
    return "Roblox tracker is running"


def run_web():
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)


BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "15"))

# Здесь находятся Roblox User ID отслеживаемых людей.
YOUTUBERS = {
    "FixPlay": 734375793,
    "Kaban": 5390379061,
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)
http = requests.Session()
NAMES_BY_ID = {user_id: name for name, user_id in YOUTUBERS.items()}

# 2 = InGame. Другие статусы (онлайн на сайте, приложение и т. п.)
# намеренно не вызывают уведомление.
last_state = {
    user_id: {"is_playing": False, "place_id": None, "game_id": None}
    for user_id in YOUTUBERS.values()
}


def send_telegram_msg(text, chat_id=None):
    if not BOT_TOKEN:
        log.warning("BOT_TOKEN не настроен")
        return False

    target_chat = chat_id or CHAT_ID
    if not target_chat:
        log.warning("CHAT_ID не настроен")
        return False

    try:
        response = http.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": target_chat, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        log.error("Ошибка отправки сообщения в Telegram: %s", exc)
        return False


def get_presences():
    response = http.post(
        "https://presence.roblox.com/v1/presence/users",
        json={"userIds": list(YOUTUBERS.values())},
        timeout=10,
    )
    response.raise_for_status()
    return response.json().get("userPresences", [])


def join_link(place_id, game_id):
    # Эта ссылка пытается открыть конкретный сервер, но Roblox может отказать,
    # если сервер заполнен или присоединение к пользователю закрыто.
    if game_id:
        return f"https://www.roblox.com/games/start?placeId={place_id}&gameInstanceId={game_id}"
    return f"https://www.roblox.com/games/{place_id}"


def tracker_loop():
    first_check = True
    while True:
        try:
            presences = get_presences()
            seen_ids = set()

            for user in presences:
                user_id = user.get("userId")
                if user_id not in last_state:
                    continue

                seen_ids.add(user_id)
                old = last_state[user_id]
                presence_type = user.get("userPresenceType")
                place_id = user.get("placeId")
                game_id = user.get("gameId")

                # Уведомление только если человек реально находится в игре.
                is_playing = presence_type == 2 and place_id is not None
                changed_server = is_playing and old["game_id"] != game_id

                if is_playing and (not old["is_playing"] or changed_server):
                    name = NAMES_BY_ID[user_id]
                    title = "ЗАШЁЛ В ИГРУ" if not old["is_playing"] else "СМЕНИЛ СЕРВЕР"
                    link = join_link(place_id, game_id)
                    send_telegram_msg(
                        f"🚨 **{name} {title}!**\n\n"
                        f"🎮 Place ID: `{place_id}`\n"
                        f"🖥 Server ID: `{game_id or 'не указан'}`\n"
                        f"🔗 [ПОПРОБОВАТЬ ЗАЙТИ К НЕМУ]({link})"
                    )

                # Статус сохраняем, но при выходе сообщение НЕ отправляем.
                last_state[user_id] = {
                    "is_playing": is_playing,
                    "place_id": place_id if is_playing else None,
                    "game_id": game_id if is_playing else None,
                }

            # Если API временно не вернул пользователя, не объявляем его вышедшим.
            # При следующем нормальном ответе состояние обновится.
            first_check = False

        except (requests.RequestException, ValueError, KeyError) as exc:
            log.error("Ошибка проверки Roblox: %s", exc)

        time.sleep(CHECK_INTERVAL)


def status_text():
    lines = ["📡 Я работаю и отслеживаю:"]
    for name, user_id in YOUTUBERS.items():
        state = last_state[user_id]
        if state["is_playing"]:
            lines.append(f"✅ {name} сейчас в игре (Place ID: {state['place_id']})")
        else:
            lines.append(f"⚪ {name} сейчас не в игре")
    return "\n".join(lines)


def telegram_commands_loop():
    """Отвечает на команды Telegram без сторонних библиотек."""
    if not BOT_TOKEN:
        log.warning("Команды Telegram отключены: нет BOT_TOKEN")
        return

    offset = None
    while True:
        try:
            params = {"timeout": 25}
            if offset is not None:
                params["offset"] = offset
            response = http.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                params=params,
                timeout=35,
            )
            response.raise_for_status()
            updates = response.json().get("result", [])

            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message", {})
                text = (message.get("text") or "").split()[0].lower()
                chat_id = message.get("chat", {}).get("id")
                if not chat_id:
                    continue

                if text in ("/start", "/status"):
                    send_telegram_msg(
                        "✅ **Да, я работаю!**\n\n"
                        + status_text()
                        + "\n\nЯ отправляю уведомления только тогда, когда FixPlay или Kaban находятся именно внутри Roblox-игры.\n\n"
                        "Команда /status показывает текущий статус.",
                        chat_id=chat_id,
                    )

        except (requests.RequestException, ValueError, KeyError) as exc:
            log.error("Ошибка Telegram-команд: %s", exc)
            time.sleep(5)


if __name__ == "__main__":
    threading.Thread(target=tracker_loop, daemon=True).start()
    threading.Thread(target=telegram_commands_loop, daemon=True).start()
    run_web()
