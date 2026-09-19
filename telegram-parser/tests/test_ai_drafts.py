from types import SimpleNamespace

from src.ai_drafts import _response_text


def test_response_text_uses_output_text_when_available():
    response = SimpleNamespace(output_text="  готовый отклик  ")
    assert _response_text(response) == "готовый отклик"


def test_response_text_falls_back_to_response_output_items():
    response = SimpleNamespace(
        output_text="",
        output=[SimpleNamespace(content=[SimpleNamespace(text="первая часть"), SimpleNamespace(text="вторая часть")])],
    )
    assert _response_text(response) == "первая часть\nвторая часть"


def test_response_text_handles_dictionary_response():
    response = {"output": [{"content": [{"text": "из JSON"}]}]}
    assert _response_text(response) == "из JSON"
