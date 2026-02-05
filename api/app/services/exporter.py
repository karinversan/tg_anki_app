from __future__ import annotations

import random
from pathlib import Path

import genanki


def export_apkg(path: Path, title: str, questions: list[dict]) -> None:
    random.seed(title)
    model_id = random.randint(1_000_000, 9_999_999_999)
    deck_id = random.randint(1_000_000, 9_999_999_999)
    model = genanki.Model(
        model_id,
        "TG Anki Model",
        fields=[{"name": "Question"}, {"name": "Answer"}],
        templates=[
            {
                "name": "Card 1",
                "qfmt": "{{Question}}",
                "afmt": "{{FrontSide}}<hr id='answer'>{{Answer}}",
            }
        ],
    )
    deck = genanki.Deck(deck_id, title)
    for item in questions:
        tags = item.get("tags", [])
        if isinstance(tags, str):
            tags = [t for t in tags.split() if t]
        elif not isinstance(tags, list):
            tags = []
        safe_tags: list[str] = []
        for tag in tags:
            if not isinstance(tag, str):
                continue
            cleaned = "_".join(tag.strip().split())
            if cleaned:
                safe_tags.append(cleaned)
        question_text = _render_question(item)
        answer_text = _render_answer(item)
        note = genanki.Note(
            model=model,
            fields=[question_text, answer_text],
            tags=safe_tags,
        )
        deck.add_note(note)
    genanki.Package(deck).write_to_file(str(path))


def _render_question(item: dict) -> str:
    stem = str(item.get("question", "")).strip()
    options = item.get("options")
    if item.get("type") == "mcq" and isinstance(options, list) and options:
        letters = ["A", "B", "C", "D"]
        opts = [str(o).strip() for o in options][:4]
        options_text = "\n".join(f"{letters[i]}) {opt}" for i, opt in enumerate(opts))
        return f"{stem}\n{options_text}" if stem else options_text
    return stem


def _render_answer(item: dict) -> str:
    if item.get("type") == "mcq":
        options = item.get("options") or []
        correct_index = item.get("correct_index")
        if (
            isinstance(options, list)
            and isinstance(correct_index, int)
            and 0 <= correct_index < len(options)
        ):
            letter = ["A", "B", "C", "D"][correct_index]
            return f"{letter}) {options[correct_index]}"
    return str(item.get("answer", "")).strip()
