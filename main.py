import asyncio
import os
import requests

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# Реальные ID ютуберов
YOUTUBERS = {
    "FixPlay": 734375793,
    "Kaban": 5390379061,
}

last_places = {uid: None for uid in YOUTUBERS.values()}


def send_telegram_msg(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(
        url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    )


async def main():
    send_telegram_msg("🚀 Бот отслеживания Roblox запущен!")
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


if __name__ == "__main__":
    asyncio.run(main())
