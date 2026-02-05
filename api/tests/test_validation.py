import pytest

from app.services.validation import validate_file


def test_validate_text_file():
    result = validate_file("notes.txt", b"hello world")
    assert result.mime_type in {"text/plain", "text/markdown"}


def test_validate_invalid_extension():
    with pytest.raises(ValueError):
        validate_file("notes.exe", b"test")
