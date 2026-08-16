import asyncio
import os
import threading
from flask import Flask
import requests

# 1. Фейковый веб-сервер для обмана Render
app = Flask("")


@app.route("/")
def home():
    return "Bot is running!"


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# 2. Логика бота Telegram
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

YOUTUBERS = {
    "FixPlay": 734375793,
    "Kaban": 5390379061,
}

last_places = {uid: None for uid in YOUTUBERS.values()}


def send_telegram_msg(text):
    if not BOT_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(
        url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    )


async def track_roblox():
    send_telegram_msg("🚀 Бот отслеживания Roblox успешно запущен!")
    while True:
        try:
            payload = {"userIds": list(YOUTUBERS.values())}
            res = requests.post(
                "https://presence.roblox.com/v1/presence/users", json=payload
            ).json()

            for user in res.get("userPresences", []):
                u_id = user["userId"]
                p_type = user["userPresenceType"]  # 2 = В игре
                place_id = user.get("placeId")

                if p_type == 2 and last_places[u_id] != place_id:
                    last_places[u_id] = place_id
                    name = [k for k, v in YOUTUBERS.items() if v == u_id][0]

                    link = f"https://www.roblox.com/games/{place_id}"
                    msg = (
                        f"🚨 **{name} ЗАШЁЛ В ИГРУ!**\n\n"
                        f"🎮 Place ID: `{place_id}`\n"
                        f"🔗 [ЖМИ СЮДА ЧТОБЫ ЗАЙТИ]({link})"
                    )
                    send_telegram_msg(msg)

                elif p_type != 2:
                    last_places[u_id] = None

        except Exception as e:
            print(f"Ошибка: {e}")

        await asyncio.sleep(15)


def start_bot_loop():
    asyncio.run(track_roblox())


if __name__ == "__main__":
    # Запускаем бота в отдельном потоке
    threading.Thread(target=start_bot_loop, daemon=True).start()
    # Запускаем веб-сервер для Render
    run_web()
