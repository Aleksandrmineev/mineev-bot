"""Telegram bot: message formatting, button menu and command handlers.

This is a single-owner personal bot: once TELEGRAM_CHAT_ID is configured,
every interaction is restricted to that chat. Before it's configured,
/start replies with the chat id so the owner can copy it into telegram.env.

The primary interface is a persistent reply-keyboard menu (tap instead of
type), built from the same underlying actions as the slash commands, which
keep working for anyone who prefers typing.
"""

from __future__ import annotations

import html
import logging
import os
import re
from collections.abc import Awaitable, Callable
from datetime import timezone
from urllib.parse import urlencode

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from src.config import Settings
from src.karriere_client import ensure_date_sort, is_allowed_karriere_url
from src.models import JobPosting, SOURCE_AMS, SOURCE_FREELANCEHUNT
from src.freelancehunt_client import is_allowed_freelancehunt_url
from src.repository import DuplicateSearchError, Repository, feedback_token
from src.ai_drafts import generate_bid_draft

logger = logging.getLogger(__name__)

BTN_LIST = "📋 Мои поиски"
BTN_ADD = "➕ Добавить поиск"
BTN_RECENT = "🕘 Последние вакансии"
BTN_CHECK = "🔍 Проверить сейчас"
BTN_PAUSE = "⏸ Пауза"
BTN_RESUME = "▶️ Возобновить"
BTN_STATUS = "⚙️ Статус"
BTN_CANCEL = "❌ Отмена"

_AWAITING_ADD_URL = "add_url"
_AWAITING_ADD_AMS = "add_ams"
_AWAITING_ADD_FH = "add_freelancehunt"

SOURCE_LABELS = {
    "freelancehunt": "Freelancehunt",
    "karriere": "karriere.at",
    "ams": "AMS",
}


def _main_menu_keyboard(paused: bool) -> ReplyKeyboardMarkup:
    pause_button = BTN_RESUME if paused else BTN_PAUSE
    return ReplyKeyboardMarkup(
        [[BTN_LIST, BTN_ADD], [BTN_RECENT, BTN_CHECK], [pause_button, BTN_STATUS]],
        resize_keyboard=True,
    )


def _cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[BTN_CANCEL]], resize_keyboard=True)


def _escape(text: str) -> str:
    return html.escape(text)


def _freelancehunt_salary(salary: str | None) -> str | None:
    if not salary:
        return None
    # The public channel currently reports budgets in UAH. Keep the original
    # amount and add a transparent approximate EUR value for quick triage.
    currency_match = re.search(r"([\d\s.,]+)\s*(UAH|PLN|zł)", salary, flags=re.IGNORECASE)
    match = currency_match
    if not match:
        return salary
    raw = match.group(1).replace(" ", "").replace(",", ".")
    try:
        amount = float(raw)
        currency = match.group(2).upper()
        if currency == "UAH":
            per_eur = float(os.getenv("FREELANCEHUNT_UAH_PER_EUR", "50"))
        else:
            per_eur = float(os.getenv("FREELANCEHUNT_PLN_PER_EUR", "4.3"))
        if per_eur <= 0:
            raise ValueError
        euros = amount / per_eur
        eur_text = f"≈{euros:.0f} €" if euros >= 10 else f"≈{euros:.2f} €"
        return f"{eur_text} ({salary})"
    except ValueError:
        return salary


def build_job_message(job: JobPosting) -> tuple[str, InlineKeyboardMarkup]:
    """Builds the compact notification message. Intentionally doesn't
    include job.description: karriere.at job ad bodies mix in a lot of
    structured boilerplate (employment type, salary, company size, ...)
    that's already shown elsewhere in this message, so surfacing it here
    reads as noise. description is used for local keyword filtering only,
    see src/filters.py."""
    salary = _freelancehunt_salary(job.salary) if job.source == SOURCE_FREELANCEHUNT else job.salary
    source_label = SOURCE_LABELS.get(job.source, job.source)
    if job.source == SOURCE_FREELANCEHUNT and salary:
        lines = [f"<b>{_escape(source_label)} — {_escape(salary)}</b>"]
    else:
        lines = [f"<b>Источник: {_escape(source_label)}</b>"]
    lines.append(f"<b>{_escape(job.title)}</b>")
    if job.company:
        lines.append(_escape(job.company))
    details = [value for value in (job.location, job.employment_type) if value]
    if salary and job.source != SOURCE_FREELANCEHUNT:
        details.append(salary)
    if details:
        lines.append(_escape(" | ".join(details)))
    if job.source != SOURCE_FREELANCEHUNT and job.homeoffice is not None:
        lines.append(f"Homeoffice: {'да' if job.homeoffice else 'нет'}")

    text = "\n".join(lines)
    token = feedback_token(job.dedup_key)
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("Открыть вакансию", url=job.url)],
        [
            InlineKeyboardButton("👍 Подходит", callback_data=f"job_feedback:yes:{token}"),
            InlineKeyboardButton("👎 Не подходит", callback_data=f"job_feedback:no:{token}"),
        ],
        [InlineKeyboardButton("✍️ Подготовить отклик", callback_data=f"job_draft:{token}")],
    ])
    return text, markup


async def send_job(bot, chat_id: int, job: JobPosting) -> bool:
    """Send one job notification. Never raises: on failure it logs and
    returns False so the caller can leave the job unmarked-as-sent for a
    retry on the next check cycle instead of losing it."""
    text, markup = build_job_message(job)
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=markup)
        return True
    except TelegramError:
        logger.exception("Failed to send job %s to Telegram, will retry next cycle", job.dedup_key)
        return False


def _is_authorized(update: Update, settings: Settings) -> bool:
    if settings.telegram_chat_id is None:
        return True
    return update.effective_chat is not None and update.effective_chat.id == settings.telegram_chat_id


async def _reject_unauthorized(update: Update) -> None:
    if update.callback_query is not None:
        await update.callback_query.answer("Этот бот настроен для другого пользователя.", show_alert=True)
    elif update.message is not None:
        await update.message.reply_text("Этот бот настроен для другого пользователя.")


def register_handlers(
    application: Application,
    *,
    repository: Repository,
    settings: Settings,
    run_check: Callable[[], Awaitable[int]],
) -> None:
    # -- shared actions, used by both button taps and slash commands --------

    async def show_searches(update: Update) -> None:
        searches = repository.list_searches()
        if not searches:
            await update.message.reply_text(
                "Активных поисков нет. Нажми «➕ Добавить поиск» или используй /add <url>.",
                reply_markup=_main_menu_keyboard(repository.is_paused()),
            )
            return
        await update.message.reply_text(f"Активные поиски ({len(searches)}):")
        for search in searches:
            markup = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🗑 Удалить", callback_data=f"remove_search:{search.id}")]]
            )
            await update.message.reply_text(
                f"#{search.id}\n{search.url}\nдобавлен {search.created_at.strftime('%Y-%m-%d %H:%M')}",
                reply_markup=markup,
            )

    async def show_recent(update: Update) -> None:
        jobs = repository.list_recent_sent(limit=5)
        if not jobs:
            await update.message.reply_text(
                "Пока ничего не отправлено — либо ещё не было проверки, либо подходящих вакансий не нашлось.",
                reply_markup=_main_menu_keyboard(repository.is_paused()),
            )
            return
        await update.message.reply_text(f"Последние отправленные вакансии ({len(jobs)}):")
        for job in jobs:
            text, markup = build_job_message(job)
            await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)

    async def do_check(update: Update) -> None:
        if settings.telegram_chat_id is None:
            await update.message.reply_text("Сначала настрой TELEGRAM_CHAT_ID (см. /start) и перезапусти бота.")
            return
        await update.message.reply_text("Проверяю...")
        sent_count = await run_check()
        await update.message.reply_text(
            f"Готово. Новых вакансий отправлено: {sent_count}",
            reply_markup=_main_menu_keyboard(repository.is_paused()),
        )

    async def toggle_pause(update: Update) -> None:
        paused = not repository.is_paused()
        repository.set_paused(paused)
        text = "Автопроверка остановлена." if paused else "Автопроверка возобновлена."
        await update.message.reply_text(text, reply_markup=_main_menu_keyboard(paused))

    async def show_status(update: Update) -> None:
        last_check = repository.get_last_check_at()
        last_check_text = (
            last_check.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if last_check else "ещё не было"
        )
        paused = repository.is_paused()
        state_text = "⏸ на паузе" if paused else "🟢 активна"
        searches_count = len(repository.list_searches())
        await update.message.reply_text(
            f"Автопроверка: {state_text}\n"
            f"Активных поисков: {searches_count}\n"
            f"Последняя проверка: {last_check_text}\n"
            f"Интервал проверки: {settings.poll_interval_seconds // 60} мин",
            reply_markup=_main_menu_keyboard(paused),
        )

    async def perform_add(update: Update, url: str) -> None:
        if not is_allowed_karriere_url(url):
            await update.message.reply_text(
                "Похоже, это не ссылка на поиск вакансий karriere.at "
                "(ожидается https://www.karriere.at/jobs...). Попробуй снова через «➕ Добавить поиск».",
                reply_markup=_main_menu_keyboard(repository.is_paused()),
            )
            return
        normalized = ensure_date_sort(url)
        try:
            search_id = repository.add_search(normalized)
        except DuplicateSearchError:
            await update.message.reply_text(
                "Этот поиск уже добавлен.", reply_markup=_main_menu_keyboard(repository.is_paused())
            )
            return
        await update.message.reply_text(
            f"Добавлено (id={search_id}):\n{normalized}\n(сортировка по дате применяется автоматически)",
            reply_markup=_main_menu_keyboard(repository.is_paused()),
        )

    async def perform_add_ams(update: Update, raw: str) -> None:
        """Add an AMS search as: /addams query | location | radius."""
        parts = [part.strip() for part in raw.split("|")]
        query = parts[0] if parts else ""
        location = parts[1] if len(parts) > 1 else ""
        if not query and not location:
            await update.message.reply_text("Использование: /addams профессия | локация | радиус")
            return
        params: dict[str, object] = {"query": query, "location": location}
        if len(parts) > 2 and parts[2].isdigit() and int(parts[2]) > 0:
            params["vicinity"] = int(parts[2])
        canonical = "ams://search?" + urlencode(sorted((key, value) for key, value in params.items() if value))
        try:
            search_id = repository.add_saved_search(
                url=canonical, source=SOURCE_AMS, params=params, name=query or location
            )
        except DuplicateSearchError:
            await update.message.reply_text("Этот AMS-поиск уже добавлен.")
            return
        await update.message.reply_text(
            f"Добавлен AMS-поиск (id={search_id}): {query or 'любая профессия'} / {location or 'любая локация'}",
            reply_markup=_main_menu_keyboard(repository.is_paused()),
        )

    async def perform_add_freelancehunt(update: Update, url: str) -> None:
        if not is_allowed_freelancehunt_url(url):
            await update.message.reply_text("Нужна ссылка https://freelancehunt.com/projects...", reply_markup=_main_menu_keyboard(repository.is_paused()))
            return
        default_filters = {
            "include_keywords": ["WordPress", "WooCommerce", "OpenCart", "PHP", "API", "Python", "Node.js", "JavaScript", "парсинг", "бот", "CMS", "Shopify", "Webflow", "Tilda", "интернет-магазин", "автоматизация", "интеграция", "сайт"],
            "exclude_keywords": ["логотип", "дизайн", "UI/UX", "баннер", "видеомонтаж", "монтаж видео", "SMM", "копирайтинг", "SEO без разработки"],
            "exclude_currencies": ["UAH"],
            "exclude_ukrainian_without_budget": True,
        }
        try:
            search_id = repository.add_saved_search(
                url=url.strip(), source=SOURCE_FREELANCEHUNT,
                name="Freelancehunt IT", filters=default_filters,
            )
        except DuplicateSearchError:
            await update.message.reply_text("Этот Freelancehunt-поиск уже добавлен.")
            return
        await update.message.reply_text(
            f"Добавлен Freelancehunt-поиск (id={search_id}). Читаю публичный канал проектов, без автоставок.",
            reply_markup=_main_menu_keyboard(repository.is_paused()),
        )

    async def start_add_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "Выбери источник вакансий:",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton("karriere.at", callback_data="add_source:karriere"),
                    InlineKeyboardButton("AMS", callback_data="add_source:ams"),
                    InlineKeyboardButton("Freelancehunt", callback_data="add_source:freelancehunt"),
                ]]
            ),
        )

    # -- slash commands -------------------------------------------------

    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        if settings.telegram_chat_id is None:
            await update.message.reply_text(
                "Бот запущен. TELEGRAM_CHAT_ID ещё не настроен.\n"
                f"Твой chat id: {chat.id}\n"
                "Добавь его в telegram.env как TELEGRAM_CHAT_ID и перезапусти бота."
            )
            return
        if not _is_authorized(update, settings):
            await _reject_unauthorized(update)
            return
        await update.message.reply_text(
                "Привет! Я слежу за поисками karriere.at, AMS и Freelancehunt и присылаю новые подходящие проекты.\n\n"
                "Управляй кнопками внизу — они всегда под рукой. Команды /add, /addams, /addfh, /list, "
            "/remove, /check, /pause, /resume, /status тоже работают, если так привычнее.",
            reply_markup=_main_menu_keyboard(repository.is_paused()),
        )

    async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_authorized(update, settings):
            await _reject_unauthorized(update)
            return
        if context.args:
            await perform_add(update, context.args[0])
            return
        await start_add_flow(update, context)

    async def cmd_addams(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_authorized(update, settings):
            await _reject_unauthorized(update)
            return
        await perform_add_ams(update, " ".join(context.args))

    async def cmd_addfh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_authorized(update, settings):
            await _reject_unauthorized(update)
            return
        await perform_add_freelancehunt(update, context.args[0] if context.args else "https://freelancehunt.com/projects")

    async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_authorized(update, settings):
            await _reject_unauthorized(update)
            return
        await show_searches(update)

    async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_authorized(update, settings):
            await _reject_unauthorized(update)
            return
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text("Использование: /remove <id> (id смотри в /list)")
            return
        removed = repository.remove_search(int(context.args[0]))
        await update.message.reply_text(
            "Удалено." if removed else "Поиск с таким id не найден.",
            reply_markup=_main_menu_keyboard(repository.is_paused()),
        )

    async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_authorized(update, settings):
            await _reject_unauthorized(update)
            return
        await do_check(update)

    async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_authorized(update, settings):
            await _reject_unauthorized(update)
            return
        repository.set_paused(True)
        await update.message.reply_text("Автопроверка остановлена.", reply_markup=_main_menu_keyboard(True))

    async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_authorized(update, settings):
            await _reject_unauthorized(update)
            return
        repository.set_paused(False)
        await update.message.reply_text("Автопроверка возобновлена.", reply_markup=_main_menu_keyboard(False))

    async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_authorized(update, settings):
            await _reject_unauthorized(update)
            return
        await show_status(update)

    # -- button menu (reply keyboard) + add-search flow ------------------

    async def on_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_authorized(update, settings):
            await _reject_unauthorized(update)
            return
        text = (update.message.text or "").strip()

        awaiting = context.user_data.get("awaiting")
        if awaiting in (_AWAITING_ADD_URL, _AWAITING_ADD_AMS, _AWAITING_ADD_FH):
            context.user_data.pop("awaiting", None)
            if text == BTN_CANCEL:
                await update.message.reply_text(
                    "Отменено.", reply_markup=_main_menu_keyboard(repository.is_paused())
                )
                return
            if awaiting == _AWAITING_ADD_AMS:
                await perform_add_ams(update, text)
            elif awaiting == _AWAITING_ADD_FH:
                await perform_add_freelancehunt(update, text)
            else:
                await perform_add(update, text)
            return

        if text == BTN_LIST:
            await show_searches(update)
        elif text == BTN_ADD:
            await start_add_flow(update, context)
        elif text == BTN_RECENT:
            await show_recent(update)
        elif text == BTN_CHECK:
            await do_check(update)
        elif text in (BTN_PAUSE, BTN_RESUME):
            await toggle_pause(update)
        elif text == BTN_STATUS:
            await show_status(update)
        else:
            await update.message.reply_text(
                "Не понял. Выбери действие на клавиатуре внизу.",
                reply_markup=_main_menu_keyboard(repository.is_paused()),
            )

    async def on_remove_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not _is_authorized(update, settings):
            await _reject_unauthorized(update)
            return
        search_id = int(query.data.split(":", 1)[1])
        removed = repository.remove_search(search_id)
        await query.answer("Удалено" if removed else "Уже удалено")
        if removed and query.message is not None:
            try:
                await query.edit_message_text(f"{query.message.text}\n\n✅ Удалено")
            except TelegramError:
                pass  # message may be too old to edit; the deletion itself already succeeded

    async def on_job_feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not _is_authorized(update, settings):
            await _reject_unauthorized(update)
            return
        _, value, token = query.data.split(":")
        saved = repository.set_job_feedback(token, value)
        label = "Подходит" if value == "yes" else "Не подходит"
        await query.answer("Оценка сохранена" if saved else "Вакансия уже удалена")
        if saved and query.message is not None:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
                await query.message.reply_text(f"✅ Отмечено: {label}")
            except TelegramError:
                pass

    async def on_job_draft_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not _is_authorized(update, settings):
            await _reject_unauthorized(update)
            return
        token = query.data.split(":", 1)[1]
        job = repository.get_job_by_feedback_token(token)
        if job is None:
            await query.answer("Данные проекта уже удалены", show_alert=True)
            return
        if not settings.openai_api_key:
            await query.answer("ИИ пока не настроен", show_alert=True)
            return
        await query.answer("Готовлю черновик…")
        try:
            draft = await generate_bid_draft(job, settings)
        except Exception:
            logger.exception("Failed to generate bid draft for %s", job.dedup_key)
            if query.message is not None:
                await query.message.reply_text("Не удалось подготовить черновик. Попробуй ещё раз позже.")
            return
        if query.message is not None:
            await query.message.reply_text(
                f"<b>Черновик отклика — не отправлен</b>\n\n{html.escape(draft)}\n\n"
                "Проверь текст и отправь ставку вручную на странице проекта.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Открыть проект", url=job.url)]]),
            )

    async def on_add_source_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not _is_authorized(update, settings):
            await _reject_unauthorized(update)
            return
        source = query.data.split(":", 1)[1]
        context.user_data["awaiting"] = _AWAITING_ADD_AMS if source == "ams" else (_AWAITING_ADD_FH if source == "freelancehunt" else _AWAITING_ADD_URL)
        await query.answer()
        if query.message is not None:
            if source == "ams":
                await query.message.reply_text(
                    "Пришли параметры AMS в формате:\n"
                    "профессия или компания | локация | радиус в км\n\n"
                    "Например: IT Support | Leoben | 20",
                    reply_markup=_cancel_keyboard(),
                )
            elif source == "freelancehunt":
                await query.message.reply_text(
                    "Пришли публичную ссылку Freelancehunt (например https://freelancehunt.com/projects).",
                    reply_markup=_cancel_keyboard(),
                )
            else:
                await query.message.reply_text(
                    "Пришли ссылку на поиск karriere.at "
                    "(например https://www.karriere.at/jobs?keywords=...&locations=...).",
                    reply_markup=_cancel_keyboard(),
                )

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("add", cmd_add))
    application.add_handler(CommandHandler("addams", cmd_addams))
    application.add_handler(CommandHandler("addfh", cmd_addfh))
    application.add_handler(CommandHandler("list", cmd_list))
    application.add_handler(CommandHandler("remove", cmd_remove))
    application.add_handler(CommandHandler("check", cmd_check))
    application.add_handler(CommandHandler("pause", cmd_pause))
    application.add_handler(CommandHandler("resume", cmd_resume))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CallbackQueryHandler(on_remove_callback, pattern=r"^remove_search:\d+$"))
    application.add_handler(CallbackQueryHandler(on_job_feedback_callback, pattern=r"^job_feedback:(yes|no):[a-f0-9]{12}$"))
    application.add_handler(CallbackQueryHandler(on_job_draft_callback, pattern=r"^job_draft:[a-f0-9]{12}$"))
    application.add_handler(CallbackQueryHandler(on_add_source_callback, pattern=r"^add_source:(karriere|ams|freelancehunt)$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_message))
