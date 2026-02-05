from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.keyboards import inline_webapp, main_keyboard, webapp_url

router = Router()


@router.message(Command("start"))
async def start(message: Message) -> None:
    url = webapp_url()
    await message.answer(
        "Welcome! Use the Mini App to manage topics, upload files, and generate questions.",
        reply_markup=main_keyboard(),
    )
    if url:
        await message.answer("Tap to open the Mini App:", reply_markup=inline_webapp())
    else:
        await message.answer(
            "Mini App link must be HTTPS. Update `WEB_BASE_URL` and restart the bot.",
            reply_markup=inline_webapp(),
        )


@router.message(Command("help"))
async def help_cmd(message: Message) -> None:
    await message.answer(
        "Features:\n"
        "- Topics + file uploads\n"
        "- AI-generated questions\n"
        "Privacy: files are encrypted at rest and processed only for generation."
    )


@router.message(lambda msg: msg.text and msg.text.casefold() == "my topics")
async def list_topics(message: Message) -> None:
    await message.answer("Open the Mini App to view your topics.", reply_markup=inline_webapp())


@router.message(Command("topics"))
async def topics_cmd(message: Message) -> None:
    await message.answer("Open the Mini App to view your topics.", reply_markup=inline_webapp())
