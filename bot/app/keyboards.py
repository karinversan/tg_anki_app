from __future__ import annotations

from urllib.parse import urlparse

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo

from app.config import settings

_OPEN_TEXT = "Open Mini App"


def webapp_url() -> str | None:
    url = settings.web_base_url.strip()
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return None
    return url


def main_keyboard() -> ReplyKeyboardMarkup:
    url = webapp_url()
    webapp_button = (
        KeyboardButton(text=_OPEN_TEXT, web_app=WebAppInfo(url=url))
        if url
        else KeyboardButton(text=_OPEN_TEXT)
    )
    return ReplyKeyboardMarkup(
        keyboard=[[webapp_button]],
        resize_keyboard=True,
    )


def inline_webapp() -> InlineKeyboardMarkup:
    url = webapp_url()
    if not url:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=_OPEN_TEXT, url=settings.web_base_url)]]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=_OPEN_TEXT, web_app=WebAppInfo(url=url))]]
    )
