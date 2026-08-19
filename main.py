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


# Настройки берутся из переменного окружения Render
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "15"))

TRACKED_USERS = {
    "FixPlay": 734375793,
    "Kaban": 5390379061,
}

# Открытый инвентарь отслеживаем только у FixPlay
OPEN_INVENTORY_USERS = {
    734375793: "FixPlay"
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger(__name__)
http = requests.Session()
http.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
})

NAMES_BY_ID = {
    user_id: name
    for name, user_id in TRACKED_USERS.items()
}

last_state = {
    user_id: {
        "is_playing": False,
        "place_id": None,
        "game_id": None,
    }
    for user_id in TRACKED_USERS.values()
}

# Хранение ID последнего известного геймпаса
last_known_pass = {
    user_id: None
    for user_id in OPEN_INVENTORY_USERS
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


def get_latest_gamepass_id(user_id):
    try:
        # Strict limit=10 for Roblox V2 Inventory API
        url = f"https://inventory.roblox.com/v2/users/{user_id}/inventory/34?limit=10&sortOrder=Desc"
        response = http.get(url, timeout=10)
        response.raise_for_status()
        data = response.json().get("data", [])

        if data:
            return data[0].get("assetId")

    except requests.RequestException as error:
        logger.error("Ошибка запроса инвентаря %s: %s", user_id, error)

    return None


def get_gamepass_info(pass_id):
    try:
        url = f"https://apis.roblox.com/game-passes/v1/game-passes/{pass_id}/product-info"
        response = http.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        return {
            "name": data.get("Name", "Неизвестный пас"),
            "place_id": data.get("TargetId"),
        }

    except requests.RequestException as error:
        logger.error("Ошибка получения информации о пасе %s: %s", pass_id, error)

    return None


def check_new_purchases(user_id):
    current_pass_id = get_latest_gamepass_id(user_id)

    if not current_pass_id:
        return None

    if last_known_pass[user_id] is None:
        last_known_pass[user_id] = current_pass_id
        return None

    if current_pass_id != last_known_pass[user_id]:
        last_known_pass[user_id] = current_pass_id
        info = get_gamepass_info(current_pass_id)

        if info:
            return {
                "pass_id": current_pass_id,
                "pass_name": info["name"],
                "place_id": info["place_id"],
            }

    return None


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

    # Обязательный лог раз в 15 секунд для отслеживания
    logger.info("Ответ Roblox Presence API: %s", presences)

    return presences


def make_join_link(place_id, game_id=None):
    if game_id and place_id:
        return (
            "https://www.roblox.com/games/start"
            f"?placeId={place_id}&gameInstanceId={game_id}"
        )

    if place_id:
        return f"https://www.roblox.com/games/{place_id}"

    return "Ссылка недоступна (Place ID не вычислен)"


def update_user_state(presence):
    user_id = presence.get("userId")

    if user_id not in last_state:
        return None

    presence_type = presence.get("userPresenceType")
    place_id = presence.get("placeId")
    game_id = presence.get("gameId")

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

    # Защищенная логика: сохраняем вычисленный place_id, если Roblox отдаёт None
    current_place_id = place_id if place_id is not None else old_state["place_id"]

    last_state[user_id] = {
        "is_playing": is_playing,
        "place_id": current_place_id if is_playing else None,
        "game_id": game_id if is_playing else None,
    }

    return {
        "user_id": user_id,
        "name": NAMES_BY_ID[user_id],
        "entered_game": entered_game,
        "changed_server": changed_server,
        "is_playing": is_playing,
    }


def tracker_loop():
    logger.info("Отслеживание FixPlay и Kaban запущено")

    try:
        presences = get_roblox_presences()
        for presence in presences:
            uid = presence.get("userId")
            if uid in last_state:
                last_state[uid]["is_playing"] = presence.get("userPresenceType") == 2

        for uid in OPEN_INVENTORY_USERS:
            last_known_pass[uid] = get_latest_gamepass_id(uid)

    except Exception as error:
        logger.error("Ошибка при стартовой инициализации: %s", error)

    while True:
        try:
            presences = get_roblox_presences()

            for presence in presences:
                user_id = presence.get("userId")
                if user_id not in last_state:
                    continue

                result = update_user_state(presence)

                # 1. Уведомление о входе / смене сервера
                if result and (result["entered_game"] or result["changed_server"]):
                    name = result["name"]
                    title = "🚨 ЗАШЁЛ В ИГРУ!" if result["entered_game"] else "🔄 СМЕНИЛ СЕРВЕР!"
                    p_id = last_state[user_id]["place_id"]
                    g_id = last_state[user_id]["game_id"]

                    msg = (
                        f"{title} ({name})\n\n"
                        f"🎮 Place ID: {p_id or 'Скрыто (Friends Only)'}\n"
                        f"🖥 Server ID: {g_id or 'Скрыто'}\n\n"
                        f"🔗 Ссылка: {make_join_link(p_id, g_id)}"
                    )
                    send_telegram_message(msg)

                # 2. Проверка покупки нового геймпаса у FixPlay
                if user_id in OPEN_INVENTORY_USERS and last_state[user_id]["is_playing"]:
                    new_purchase = check_new_purchases(user_id)

                    if new_purchase:
                        p_id = new_purchase["place_id"]
                        last_state[user_id]["place_id"] = p_id
                        pass_name = new_purchase["pass_name"]
                        name = NAMES_BY_ID[user_id]

                        msg = (
                            f"🛒 {name} КУПИЛ ГЕЙМПАС!\n\n"
                            f"📦 Название: {pass_name}\n"
                            f"🎮 Вычисленный Place ID: {p_id}\n\n"
                            f"🔗 Ссылка на плейс:\n{make_join_link(p_id)}"
                        )
                        send_telegram_message(msg)

        except (requests.RequestException, ValueError, KeyError) as error:
            logger.error("Ошибка в основном цикле: %s", error)

        time.sleep(CHECK_INTERVAL)


def telegram_commands_loop():
    if not BOT_TOKEN:
        return

    update_offset = None

    while True:
        try:
            params = {"timeout": 25}
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
                chat_id = message.get("chat", {}).get("id")
                text = (message.get("text") or "").strip().lower()

                if chat_id and (text.startswith("/start") or text.startswith("/status")):
                    lines = ["📡 Статус ютуберов:"]
                    for uid, name in NAMES_BY_ID.items():
                        st = last_state[uid]
                        if st["is_playing"]:
                            lines.append(
                                f"✅ {name} в игре\n🎮 Place ID: {st['place_id'] or 'скрыто'}"
                            )
                        else:
                            lines.append(f"⚪ {name} не в игре")

                    send_telegram_message("\n\n".join(lines), chat_id=chat_id)

        except requests.RequestException as error:
            # При дублировании контейнеров во время деплоя ждем 5 секунд
            logger.error("Ошибка Telegram команд: %s", error)
            time.sleep(5)
        except Exception as error:
            logger.error("Непредвиденная ошибка Telegram: %s", error)
            time.sleep(5)


if __name__ == "__main__":
    threading.Thread(target=tracker_loop, daemon=True).start()
    threading.Thread(target=telegram_commands_loop, daemon=True).start()
    run_web_server()
