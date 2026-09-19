"""On-demand AI bid drafts. This module never runs from the scheduler."""

from __future__ import annotations

from pathlib import Path

from src.config import Settings
from src.models import JobPosting


def _response_text(response) -> str:
    if isinstance(response, dict):
        text = response.get("output_text")
        output = response.get("output") or []
    else:
        text = getattr(response, "output_text", None)
        output = getattr(response, "output", []) or []
    if text:
        return text.strip()
    chunks: list[str] = []
    for item in output:
        contents = item.get("content", []) if isinstance(item, dict) else getattr(item, "content", []) or []
        for content in contents:
            value = content.get("text") if isinstance(content, dict) else getattr(content, "text", None)
            if value:
                chunks.append(value)
    return "\n".join(chunks).strip()


async def generate_bid_draft(job: JobPosting, settings: Settings) -> str:
    from openai import AsyncOpenAI

    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    profile = ""
    if settings.profile_context_path.exists():
        profile = settings.profile_context_path.read_text(encoding="utf-8")
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.responses.create(
        model=settings.openai_model,
        instructions=(
            "Ты помогаешь фрилансеру подготовить короткий отклик на Freelancehunt. "
            "Пиши на языке задания, максимум 500 знаков, в 2–3 коротких абзацах. "
            "Тон дружелюбный, уверенный и живой. Начни с естественного приветствия и "
            "одной фразы, показывающей понимание задачи. Затем упомяни только релевантный "
            "опыт из профиля и напиши, что готов приступить сегодня. Закончи фразой "
            "«Буду рад помочь 🙂» или естественным эквивалентом на языке задания. "
            "Перед написанием мысленно определи основной тип задачи: WordPress/OpenCart, "
            "API и интеграции, парсинг и боты, интернет-магазин или автоматизация. "
            "Выбери только один наиболее подходящий блок опыта и свяжи его с конкретной "
            "деталью проекта. Если у задачи есть общепринятое название решения или услуги "
            "(например, webhook-интеграция, REST API, оптимизация Core Web Vitals, настройка "
            "WooCommerce checkout), назови его один раз и сразу поясни простыми словами, "
            "что именно будет сделано. Используй термин только если он действительно следует "
            "из описания, не придумывай архитектуру. Не перечисляй весь стек подряд. "
            "Не задавай вопросов, если без них можно начать работу. Не используй шаблонные "
            "списки, громкие обещания и лишние подробности. Не добавляй телефон, email, "
            "Telegram, ссылки, готовое решение или выдуманные гарантии. Верни только текст "
            "отклика без заголовка и комментариев."
        ),
        input=(
            f"ПРОФИЛЬ ИСПОЛНИТЕЛЯ:\n{profile}\n\n"
            f"ПРОЕКТ:\nНазвание: {job.title}\n"
            f"Бюджет: {job.salary or 'не указан'}\n"
            f"Описание:\n{job.description or 'описание недоступно'}"
        ),
        max_output_tokens=1000,
    )
    draft = _response_text(response)
    if not draft:
        raise RuntimeError("AI returned an empty draft")
    return draft
