from app.services.dedupe import dedupe_questions


def test_dedupe_questions():
    items = [
        {"question": "What is AI?", "answer": "..."},
        {"question": "What is AI", "answer": "..."},
        {"question": "Define machine learning", "answer": "..."},
    ]
    deduped = dedupe_questions(items)
    assert len(deduped) == 2
