import logging
import os
import threading
import time

import requests
from flask import Flask

app = Flask(__name__)


@app.get("/")
def home():
    return "Roblox Tracker is running"


def run_web_server():
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)


# Настройки берутся из Render Environment
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "15"))

# Roblox User ID отслеживаемых аккаунтов
TRACKED_USERS = {
    "FixPlay": 734375793,
    "Kaban": 5390379061,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger(__name__)
http = requests.Session()

NAMES_BY_ID = {
    user_id: name
    for name, user_id in TRACKED_USERS.items()
}

# Состояние каждого аккаунта хранится отдельно.
last_state = {
    user_id: {
        "is_playing": False,
        "place_id": None,
        "game_id": None,
    }
    for user_id in TRACKED_USERS.values()
}


def send_telegram_message(text, chat_id=None):
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не задан")
        return False

    target_chat_id = chat_id or CHAT_ID

    if not target_chat_id:
        logger.error("CHAT_ID не задан")
        return False

    try:
        response = http.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": target_chat_id,
                "text": text,
            },
            timeout=10,
        )

        response.raise_for_status()
        logger.info("Сообщение Telegram отправлено")
        return True

    except requests.RequestException as error:
        logger.error("Ошибка отправки сообщения Telegram: %s", error)
        return False


def get_roblox_presences():
    response = http.post(
        "https://presence.roblox.com/v1/presence/users",
        json={
            "userIds": list(TRACKED_USERS.values()),
        },
        timeout=10,
    )

    response.raise_for_status()

    data = response.json()
    presences = data.get("userPresences", [])

    logger.info("Ответ Roblox Presence API: %s", presences)

    return presences


def make_join_link(place_id, game_id):
    if game_id and place_id:
        return (
            "https://www.roblox.com/games/start"
            f"?placeId={place_id}&gameInstanceId={game_id}"
        )

    if place_id:
        return f"https://www.roblox.com/games/{place_id}"

    return "Roblox не передал ссылку на игру"


def update_user_state(presence):
    user_id = presence.get("userId")

    if user_id not in last_state:
        return None

    presence_type = presence.get("userPresenceType")
    place_id = presence.get("placeId")
    game_id = presence.get("gameId")

    # Значение 2 означает, что аккаунт находится именно в игре.
    # placeId может быть None, поэтому он не используется
    # как обязательное условие.
    is_playing = presence_type == 2

    old_state = last_state[user_id]

    was_playing = old_state["is_playing"]
    old_game_id = old_state["game_id"]

    entered_game = is_playing and not was_playing

    changed_server = (
        is_playing
        and was_playing
        and game_id is not None
        and old_game_id != game_id
    )

    last_state[user_id] = {
        "is_playing": is_playing,
        "place_id": place_id if is_playing else None,
        "game_id": game_id if is_playing else None,
    }

    return {
        "user_id": user_id,
        "name": NAMES_BY_ID[user_id],
        "entered_game": entered_game,
        "changed_server": changed_server,
        "is_playing": is_playing,
    }


def send_game_notification(result):
    if not result:
        return

    if not result["entered_game"] and not result["changed_server"]:
        return

    user_id = result["user_id"]
    name = result["name"]
    state = last_state[user_id]

    if result["entered_game"]:
        title = f"🚨 {name} ЗАШЁЛ В ИГРУ!"
    else:
        title = f"🔄 {name} СМЕНИЛ СЕРВЕР!"

    place_id = state["place_id"]
    game_id = state["game_id"]
    link = make_join_link(place_id, game_id)

    message = (
        f"{title}\n\n"
        f"🎮 Place ID: {place_id or 'Roblox не передал'}\n"
        f"🖥 Server ID: {game_id or 'Roblox не передал'}\n\n"
        f"🔗 Попробовать зайти:\n{link}"
    )

    send_telegram_message(message)


def tracker_loop():
    logger.info("Отслеживание FixPlay и Kaban запущено")

    # Первичная проверка без уведомлений.
    try:
        presences = get_roblox_presences()

        for presence in presences:
            user_id = presence.get("userId")

            if user_id in last_state:
                presence_type = presence.get("userPresenceType")
                place_id = presence.get("placeId")
                game_id = presence.get("gameId")

                last_state[user_id] = {
                    "is_playing": presence_type == 2,
                    "place_id": place_id if presence_type == 2 else None,
                    "game_id": game_id if presence_type == 2 else None,
                }

    except (requests.RequestException, ValueError, KeyError) as error:
        logger.error("Ошибка первой проверки Roblox: %s", error)

    while True:
        try:
            presences = get_roblox_presences()
            received_ids = set()

            for presence in presences:
                user_id = presence.get("userId")

                if user_id not in last_state:
                    continue

                received_ids.add(user_id)

                result = update_user_state(presence)
                send_game_notification(result)

            # Если Roblox временно не вернул пользователя,
            # не меняем его состояние и не отправляем ложное уведомление.
            missing_ids = set(last_state.keys()) - received_ids

            if missing_ids:
                logger.warning(
                    "Roblox не вернул пользователей: %s",
                    [
                        NAMES_BY_ID[user_id]
                        for user_id in missing_ids
                    ],
                )

        except (requests.RequestException, ValueError, KeyError) as error:
            logger.error("Ошибка проверки Roblox: %s", error)

        time.sleep(CHECK_INTERVAL)


def get_status_text():
    lines = ["📡 Текущий статус:"]

    for name, user_id in TRACKED_USERS.items():
        state = last_state[user_id]

        if state["is_playing"]:
            lines.append(
                f"✅ {name} сейчас в игре\n"
                f"🎮 Place ID: {state['place_id'] or 'не передан'}"
            )
        else:
            lines.append(
                f"⚪ {name} сейчас не в игре"
            )

    return "\n\n".join(lines)


def refresh_all_states():
    try:
        presences = get_roblox_presences()

        for presence in presences:
            user_id = presence.get("userId")

            if user_id in last_state:
                presence_type = presence.get("userPresenceType")
                place_id = presence.get("placeId")
                game_id = presence.get("gameId")

                last_state[user_id] = {
                    "is_playing": presence_type == 2,
                    "place_id": place_id if presence_type == 2 else None,
                    "game_id": game_id if presence_type == 2 else None,
                }

    except (requests.RequestException, ValueError, KeyError) as error:
        logger.error("Ошибка обновления статуса: %s", error)


def telegram_commands_loop():
    logger.info("Команды Telegram запущены")

    if not BOT_TOKEN:
        logger.error("Команды отключены: BOT_TOKEN не задан")
        return

    update_offset = None

    while True:
        try:
            params = {
                "timeout": 25,
            }

            if update_offset is not None:
                params["offset"] = update_offset

            response = http.get(
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
                text = (message.get("text") or "").strip().lower()

                if not chat_id:
                    continue

                if text.startswith("/start") or text.startswith("/status"):
                    # Для команды делаем свежий запрос Roblox.
                    refresh_all_states()

                    answer = (
                        "✅ Да, бот работает!\n\n"
                        f"{get_status_text()}\n\n"
                        "Уведомления отправляются отдельно для каждого аккаунта.\n"
                        "Сообщение приходит только при нахождении аккаунта "
                        "именно внутри Roblox-игры."
                    )

                    send_telegram_message(
                        answer,
                        chat_id=chat_id,
                    )

        except requests.RequestException as error:
            logger.error("Ошибка получения команд Telegram: %s", error)
            time.sleep(5)

        except (ValueError, KeyError) as error:
            logger.error("Ошибка обработки Telegram: %s", error)
            time.sleep(5)


if __name__ == "__main__":
    threading.Thread(
        target=tracker_loop,
        daemon=True,
    ).start()

    threading.Thread(
        target=telegram_commands_loop,
        daemon=True,
    ).start()

    run_web_server()
