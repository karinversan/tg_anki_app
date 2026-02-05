from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.types import MenuButtonWebApp, WebAppInfo

from app.config import settings
from app.handlers import router
from app.keyboards import webapp_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

bot = Bot(token=settings.bot_token)


def _build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(router)
    return dp


async def _startup() -> None:
    await bot.delete_webhook(drop_pending_updates=True)
    url = webapp_url()
    if url:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Open Mini App",
                web_app=WebAppInfo(url=url),
            )
        )


def run() -> None:
    dp = _build_dispatcher()
    dp.run_polling(bot, on_startup=_startup)


if __name__ == "__main__":
    run()
