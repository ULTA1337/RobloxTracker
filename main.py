import logging
import os
import threading
import time

import requests
from flask import Flask

app = Flask(__name__)

# Render проверяет, что веб-сервис отвечает
@app.get("/")
def home():
    return "Roblox tracker is running"


def run_web_server():
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)


BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "15"))

# Roblox User ID отслеживаемых людей
YOUTUBERS = {
    "FixPlay": 734375793,
    "Kaban": 5390379061,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger(__name__)
session = requests.Session()

NAMES_BY_ID = {
    user_id: name
    for name, user_id in YOUTUBERS.items()
}

# Состояние игроков.
# userPresenceType == 2 означает, что человек находится именно в игре.
last_state = {
    user_id: {
        "playing": False,
        "place_id": None,
        "game_id": None,
    }
    for user_id in YOUTUBERS.values()
}


def send_telegram_message(text, chat_id=None):
    if not BOT_TOKEN:
        logger.error("Не задан BOT_TOKEN")
        return False

    target_chat_id = chat_id or CHAT_ID

    if not target_chat_id:
        logger.error("Не задан CHAT_ID")
        return False

    try:
        response = session.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": target_chat_id,
                "text": text,
            },
            timeout=10,
        )

        response.raise_for_status()
        return True

    except requests.RequestException as error:
        logger.error("Ошибка отправки сообщения Telegram: %s", error)
        return False


def get_roblox_presences():
    response = session.post(
        "https://presence.roblox.com/v1/presence/users",
        json={
            "userIds": list(YOUTUBERS.values()),
        },
        timeout=10,
    )

    response.raise_for_status()

    data = response.json()
    return data.get("userPresences", [])


def make_join_link(place_id, game_id):
    """
    Ссылка пытается открыть конкретный сервер.
    Roblox может не пустить, если сервер заполнен
    или присоединение к игроку закрыто.
    """

    if game_id:
        return (
            "https://www.roblox.com/games/start"
            f"?placeId={place_id}&gameInstanceId={game_id}"
        )

    return f"https://www.roblox.com/games/{place_id}"


def get_status_text():
    lines = ["📡 Сейчас я отслеживаю:"]

    for name, user_id in YOUTUBERS.items():
        state = last_state[user_id]

        if state["playing"]:
            lines.append(
                f"✅ {name} сейчас в игре "
                f"(Place ID: {state['place_id']})"
            )
        else:
            lines.append(f"⚪ {name} сейчас не в игре")

    return "\n".join(lines)


def roblox_tracker_loop():
    while True:
        try:
            presences = get_roblox_presences()

            for presence in presences:
                user_id = presence.get("userId")

                if user_id not in last_state:
                    continue

                # 2 = InGame.
                # Статусы 0, 1 и другие игнорируются.
                presence_type = presence.get("userPresenceType")
                is_playing = (
                    presence_type == 2
                    and presence.get("placeId") is not None
                )

                place_id = presence.get("placeId")
                game_id = presence.get("gameId")

                old_state = last_state[user_id]
                was_playing = old_state["playing"]

                # Уведомляем только о входе в игру
                # или о переходе на другой сервер.
                entered_game = is_playing and not was_playing
                changed_server = (
                    is_playing
                    and was_playing
                    and old_state["game_id"] != game_id
                )

                if entered_game or changed_server:
                    name = NAMES_BY_ID[user_id]
                    link = make_join_link(place_id, game_id)

                    if entered_game:
                        title = f"🚨 {name} ЗАШЁЛ В ИГРУ!"
                    else:
                        title = f"🔄 {name} СМЕНИЛ СЕРВЕР!"

                    message = (
                        f"{title}\n\n"
                        f"🎮 Place ID: {place_id}\n"
                        f"🖥 Server ID: {game_id or 'не указан'}\n"
                        f"🔗 Попробовать зайти:\n{link}"
                    )

                    send_telegram_message(message)

                # Сохраняем новое состояние.
                # При выходе сообщение НЕ отправляется.
                last_state[user_id] = {
                    "playing": is_playing,
                    "place_id": place_id if is_playing else None,
                    "game_id": game_id if is_playing else None,
                }

        except (requests.RequestException, ValueError, KeyError) as error:
            logger.error("Ошибка проверки Roblox: %s", error)

        time.sleep(CHECK_INTERVAL)


def telegram_commands_loop():
    """
    Обрабатывает команды Telegram:
    /start
    /status
    """

    if not BOT_TOKEN:
        logger.error("Команды Telegram отключены: отсутствует BOT_TOKEN")
        return

    update_offset = None

    while True:
        try:
            params = {
                "timeout": 25,
            }

            if update_offset is not None:
                params["offset"] = update_offset

            response = session.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                params=params,
                timeout=35,
            )

            response.raise_for_status()

            updates = response.json().get("result", [])

            for update in updates:
                update_offset = update["update_id"] + 1

                message = update.get("message", {})
                chat = message.get("chat", {})
                chat_id = chat.get("id")
                text = message.get("text", "").strip().lower()

                if not chat_id:
                    continue

                if text.startswith("/start") or text.startswith("/status"):
                    answer = (
                        "✅ Да, бот работает!\n\n"
                        + get_status_text()
                        + "\n\n"
                        "Я отправляю сообщения только тогда, "
                        "когда FixPlay или Kaban находятся именно "
                        "внутри Roblox-игры.\n\n"
                        "Когда человек просто онлайн в Roblox, "
                        "сообщение не отправляется."
                    )

                    send_telegram_message(
                        answer,
                        chat_id=chat_id,
                    )

        except (requests.RequestException, ValueError, KeyError) as error:
            logger.error("Ошибка Telegram-команд: %s", error)
            time.sleep(5)


if __name__ == "__main__":
    threading.Thread(
        target=roblox_tracker_loop,
        daemon=True,
    ).start()

    threading.Thread(
        target=telegram_commands_loop,
        daemon=True,
    ).start()

    run_web_server()
