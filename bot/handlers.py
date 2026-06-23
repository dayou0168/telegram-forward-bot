from __future__ import annotations

import asyncio
import html
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings
from bot import keyboards
from bot import repositories as repo
from bot.states import GroupForm, OperatorForm, ReplyForm, SendForm

router = Router()


def _user_id_from_event(event: Message | CallbackQuery) -> int | None:
    if event.from_user is None:
        return None
    return event.from_user.id


async def _role_or_reject(
    event: Message | CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    *,
    owner_required: bool = False,
) -> str | None:
    if isinstance(event, Message) and event.chat.type != "private":
        return None
    if isinstance(event, CallbackQuery) and event.message is not None:
        chat = getattr(event.message, "chat", None)
        if chat is not None and chat.type != "private":
            await event.answer("请私聊机器人操作。", show_alert=False)
            return None

    user_id = _user_id_from_event(event)
    if user_id is None:
        return None

    role = await repo.get_user_role(session, user_id, settings.owner_user_ids)
    allowed = role is not None and (not owner_required or role == "owner")
    if allowed:
        if event.from_user is not None:
            await repo.update_authorized_user_profile(
                session,
                user_id=user_id,
                username=event.from_user.username,
                first_name=event.from_user.full_name,
            )
        return role

    if isinstance(event, CallbackQuery):
        await event.answer("无权限使用此机器人。", show_alert=False)
        return None

    if settings.unauthorized_reply and event.chat.type == "private":
        await event.answer("无权限使用此机器人。")
    return None


async def _safe_edit(callback: CallbackQuery, text: str, reply_markup: Any | None = None) -> None:
    if callback.message is None:
        await callback.answer()
        return
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            await callback.message.answer(text, reply_markup=reply_markup)
    await callback.answer()


async def _require_group_access(
    callback: CallbackQuery,
    session: AsyncSession,
    role: str,
    group_id: int,
) -> bool:
    if await repo.has_group_access(session, callback.from_user.id, role, group_id):
        return True
    await _safe_edit(callback, "无权限访问这个分组。", keyboards.main_menu(role))
    return False


async def _has_message_group_access(
    message: Message,
    session: AsyncSession,
    role: str,
    group_id: int,
) -> bool:
    if await repo.has_group_access(session, message.from_user.id, role, group_id):
        return True
    await message.answer("无权限访问这个分组。", reply_markup=keyboards.main_menu(role))
    return False


async def _require_chat_access(
    callback: CallbackQuery,
    session: AsyncSession,
    role: str,
    chat_id: int,
) -> bool:
    if await repo.has_chat_access(session, callback.from_user.id, role, chat_id):
        return True
    await _safe_edit(callback, "无权限访问这个群。", keyboards.main_menu(role))
    return False


async def _has_message_chat_access(
    message: Message,
    session: AsyncSession,
    role: str,
    chat_id: int,
) -> bool:
    if await repo.has_chat_access(session, message.from_user.id, role, chat_id):
        return True
    await message.answer("无权限访问这个群。", reply_markup=keyboards.main_menu(role))
    return False


async def _require_group_broadcast_enabled(
    callback: CallbackQuery,
    session: AsyncSession,
    role: str,
) -> bool:
    if await repo.can_group_broadcast(session, callback.from_user.id, role):
        return True
    await _safe_edit(callback, "你的分组群发权限已关闭。", keyboards.main_menu(role))
    return False


async def _has_message_group_broadcast_enabled(
    message: Message,
    session: AsyncSession,
    role: str,
) -> bool:
    if await repo.can_group_broadcast(session, message.from_user.id, role):
        return True
    await message.answer("你的分组群发权限已关闭。", reply_markup=keyboards.main_menu(role))
    return False


async def _require_direct_send_enabled(
    callback: CallbackQuery,
    session: AsyncSession,
    role: str,
) -> bool:
    if await repo.can_direct_send(session, callback.from_user.id, role):
        return True
    await _safe_edit(callback, "你的单群发送权限已关闭。", keyboards.main_menu(role))
    return False


async def _has_message_direct_send_enabled(
    message: Message,
    session: AsyncSession,
    role: str,
) -> bool:
    if await repo.can_direct_send(session, message.from_user.id, role):
        return True
    await message.answer("你的单群发送权限已关闭。", reply_markup=keyboards.main_menu(role))
    return False


async def _require_operator_management_access(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    role: str,
    target_user_id: int,
) -> bool:
    if not await repo.can_create_child_operator(session, callback.from_user.id, role, settings.owner_user_ids):
        await _safe_edit(callback, "你不能管理下级操作人。", keyboards.main_menu(role))
        return False
    if await repo.can_manage_operator(session, callback.from_user.id, role, target_user_id):
        return True
    await _safe_edit(callback, "无权限管理这个操作人。", keyboards.main_menu(role))
    return False


async def _has_message_operator_management_access(
    message: Message,
    session: AsyncSession,
    settings: Settings,
    role: str,
    target_user_id: int,
) -> bool:
    if not await repo.can_create_child_operator(session, message.from_user.id, role, settings.owner_user_ids):
        await message.answer("你不能管理下级操作人。", reply_markup=keyboards.main_menu(role))
        return False
    if await repo.can_manage_operator(session, message.from_user.id, role, target_user_id):
        return True
    await message.answer("无权限管理这个操作人。", reply_markup=keyboards.main_menu(role))
    return False


async def _operators_menu_markup(
    session: AsyncSession,
    settings: Settings,
    manager_user_id: int,
    role: str,
) -> Any:
    can_add = await repo.can_create_child_operator(session, manager_user_id, role, settings.owner_user_ids)
    operators = []
    if can_add:
        operators = await repo.list_manageable_operators(session, manager_user_id, role)
    return keyboards.operators_menu(operators, can_add_operator=can_add)


async def _operator_detail_markup(
    session: AsyncSession,
    settings: Settings,
    user: Any,
    viewer_role: str = "owner",
) -> Any:
    group_count = await repo.count_operator_group_permissions(session, user.user_id)
    chat_count = await repo.count_operator_chat_permissions(session, user.user_id)
    flags = await repo.get_operator_feature_flags(session, user.user_id)
    allow_manage_operators = await repo.can_manage_child_operators(
        session,
        user.user_id,
        "operator",
        settings.owner_user_ids,
    )
    can_toggle_manage_operators = viewer_role == "owner" and user.created_by in settings.owner_user_ids
    return keyboards.operator_detail(
        user,
        group_count,
        chat_count,
        allow_group_broadcast=flags.allow_group_broadcast,
        allow_direct_send=flags.allow_direct_send,
        allow_manage_operators=allow_manage_operators,
        can_toggle_manage_operators=can_toggle_manage_operators,
    )


async def _grantable_direct_chats(
    *,
    session: AsyncSession,
    bot: Bot,
    operator_user_id: int,
    role: str,
) -> list[Any]:
    return await repo.list_direct_send_chats(session, operator_user_id, role)


def _normalize_group_name(raw: str) -> str:
    return " ".join(raw.strip().split())


def _chat_status_from_member_update(update: ChatMemberUpdated) -> str:
    status = update.new_chat_member.status
    if status in {"member", "administrator", "creator"}:
        return "active"
    if status == "left":
        return "left"
    if status == "kicked":
        return "kicked"
    return status


def _format_chats(chats: list[Any]) -> str:
    if not chats:
        return "暂无群组。"
    return "\n".join(f"- {chat.title} | {chat.chat_id} | {chat.status}" for chat in chats)


def _sender_label(message: Message) -> str:
    if message.from_user is not None:
        if message.from_user.full_name:
            return message.from_user.full_name
        if message.from_user.username:
            return f"@{message.from_user.username}"
        return "未知发送者"
    if message.sender_chat is not None:
        if message.sender_chat.title:
            return message.sender_chat.title
        if message.sender_chat.username:
            return f"@{message.sender_chat.username}"
        return "未知发送者"
    return "未知发送者"


def _sender_url(message: Message) -> str | None:
    if message.from_user is not None:
        return f"tg://user?id={message.from_user.id}"
    if message.sender_chat is not None and message.sender_chat.username:
        return f"https://t.me/{message.sender_chat.username}"
    return None


def _html_link(label: str, url: str | None) -> str:
    safe_label = html.escape(label, quote=False)
    if url is None:
        return safe_label
    safe_url = html.escape(url, quote=True)
    return f'<a href="{safe_url}">{safe_label}</a>'


def _truncate_text(text: str, limit: int = 1200) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    suffix = "\n...内容较长，请打开群回复查看全文"
    if limit <= len(suffix):
        return text[:limit].rstrip()
    return text[: limit - len(suffix)].rstrip() + suffix


def _reply_attachment_label(message: Message) -> str:
    if getattr(message, "photo", None):
        return "图片"
    if getattr(message, "video", None):
        return "视频"
    if getattr(message, "animation", None):
        return "动画"
    if getattr(message, "document", None):
        file_name = getattr(message.document, "file_name", None) or "未命名文件"
        return f"文件：{file_name}"
    if getattr(message, "sticker", None):
        return "贴纸"
    if getattr(message, "voice", None):
        return "语音"
    if getattr(message, "audio", None):
        title = getattr(message.audio, "title", None)
        return f"音频：{title}" if title else "音频"
    if getattr(message, "video_note", None):
        return "视频消息"
    if getattr(message, "contact", None):
        return "联系人"
    if getattr(message, "location", None):
        return "位置"
    if getattr(message, "venue", None):
        return "地点"
    if getattr(message, "poll", None):
        question = getattr(message.poll, "question", None)
        return f"投票：{question}" if question else "投票"
    if getattr(message, "dice", None):
        return "骰子"
    return "非文字消息"


def _reply_content_preview(message: Message, *, text_limit: int = 1200) -> tuple[str, bool]:
    if message.text:
        return _truncate_text(message.text, text_limit), False

    label = _reply_attachment_label(message)
    if message.caption:
        return f"[{label}]\n{_truncate_text(message.caption, text_limit)}", True
    return f"[{label}]\n已附上原消息，点击下方按钮也可以打开群内位置。", True


def _reply_group_link(message: Message, group_url: str | None) -> str:
    return _html_link(str(message.chat.title or message.chat.id), group_url)


def _reply_sender_link(message: Message) -> str:
    return _html_link(_sender_label(message), _sender_url(message))


def _can_embed_notice_in_copied_message(message: Message) -> bool:
    return any(
        getattr(message, field, None)
        for field in ("photo", "video", "animation", "document", "audio", "voice", "video_note")
    )


def _build_reply_notice(
    message: Message,
    *,
    content_limit: int = 1200,
    group_url: str | None = None,
    max_length: int | None = None,
) -> str:
    limit = content_limit
    while True:
        content_preview, _ = _reply_content_preview(message, text_limit=limit)
        notice = (
            f"群：{_reply_group_link(message, group_url)}\n"
            f"人：{_reply_sender_link(message)}\n\n"
            f"内容：\n\n{html.escape(content_preview, quote=False)}"
        )
        if max_length is None or len(notice) <= max_length or limit <= 60:
            return notice
        limit = max(60, limit - max(20, len(notice) - max_length))


async def _notify_owners_operator_sent_message(
    *,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    operator_user_id: int,
    source_chat_id: int,
    source_message_id: int,
    target_text: str,
) -> None:
    if operator_user_id in settings.owner_user_ids:
        return
    operator = await repo.get_operator(session, operator_user_id)
    label = str(operator_user_id)
    if operator is not None:
        label = operator.remark or operator.first_name or operator.username or str(operator.user_id)
    notice = f"操作人发送消息\n\n操作人：{label} ({operator_user_id})\n目标：{target_text}"
    for owner_id in settings.owner_user_ids:
        try:
            await bot.send_message(owner_id, notice)
            await bot.copy_message(
                chat_id=owner_id,
                from_chat_id=source_chat_id,
                message_id=source_message_id,
            )
        except TelegramAPIError:
            continue


def _format_uid_text(user: Any) -> str:
    username = f"@{user.username}" if getattr(user, "username", None) else "无"
    full_name = getattr(user, "full_name", None) or getattr(user, "first_name", None) or "无"
    return (
        "你的 Telegram UID\n\n"
        f"UID：{user.id}\n"
        f"名称：{full_name}\n"
        f"Username：{username}"
    )


def _format_shared_user(user: Any) -> str:
    first_name = getattr(user, "first_name", None) or ""
    last_name = getattr(user, "last_name", None) or ""
    full_name = " ".join(part for part in [first_name, last_name] if part).strip() or "未提供名称"
    username = getattr(user, "username", None)
    username_text = f"@{username}" if username else "无"
    return (
        f"UID：{user.user_id}\n"
        f"名称：{full_name}\n"
        f"Username：{username_text}\n"
        f"添加格式：{user.user_id} {full_name}"
    )


def _telegram_message_url(chat_id: int, message_id: int, username: str | None = None) -> str | None:
    if username:
        return f"https://t.me/{username}/{message_id}"

    chat_id_text = str(chat_id)
    if chat_id_text.startswith("-100"):
        return f"https://t.me/c/{chat_id_text[4:]}/{message_id}"

    return None


async def _is_user_chat_member(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    except TelegramAPIError:
        return False

    if member.status in {"creator", "administrator", "member"}:
        return True
    if member.status == "restricted":
        return bool(getattr(member, "is_member", False))
    return False


async def _filter_chats_visible_to_operator(
    bot: Bot,
    chats: list[Any],
    operator_user_id: int,
    role: str,
) -> list[Any]:
    if role == "owner":
        return chats

    semaphore = asyncio.Semaphore(8)

    async def check(chat: Any) -> tuple[Any, bool]:
        async with semaphore:
            return chat, await _is_user_chat_member(bot, chat.chat_id, operator_user_id)

    checked = await asyncio.gather(*(check(chat) for chat in chats))
    return [chat for chat, is_member in checked if is_member]


async def _visible_available_chats_for_group(
    *,
    bot: Bot,
    session: AsyncSession,
    group_id: int,
    operator_user_id: int,
    role: str,
) -> list[Any]:
    chats = await repo.list_available_chats_for_group(session, group_id)
    return await _filter_chats_visible_to_operator(bot, chats, operator_user_id, role)


async def _get_batch_selected_chat_ids(state: FSMContext, group_id: int) -> set[int]:
    data = await state.get_data()
    if int(data.get("batch_group_id", 0)) != group_id:
        return set()
    return {int(chat_id) for chat_id in data.get("batch_selected_chat_ids", [])}


async def _set_batch_selected_chat_ids(state: FSMContext, group_id: int, selected_chat_ids: set[int]) -> None:
    await state.update_data(
        batch_group_id=group_id,
        batch_selected_chat_ids=sorted(selected_chat_ids),
    )


async def _show_batch_add_page(
    *,
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    state: FSMContext,
    group_id: int,
    page: int,
    role: str,
) -> None:
    group = await repo.get_delivery_group(session, group_id)
    if group is None:
        await _safe_edit(callback, "分组不存在或已删除。", keyboards.back_to_main())
        return
    if not await _require_group_access(callback, session, role, group_id):
        return

    chats = await _visible_available_chats_for_group(
        bot=bot,
        session=session,
        group_id=group_id,
        operator_user_id=callback.from_user.id,
        role=role,
    )
    selected_chat_ids = await _get_batch_selected_chat_ids(state, group_id)
    visible_chat_ids = {chat.chat_id for chat in chats}
    selected_chat_ids &= visible_chat_ids
    await _set_batch_selected_chat_ids(state, group_id, selected_chat_ids)

    if chats:
        max_page = max(0, (len(chats) - 1) // keyboards.PAGE_SIZE)
        page = min(max(page, 0), max_page)
        hint = f"请选择要添加的群组。\n已选择：{len(selected_chat_ids)}"
    elif role == "owner":
        hint = "暂无可批量添加群组。"
    else:
        hint = "暂无可批量添加群组，或机器人无法确认你在这些群内。"

    await _safe_edit(
        callback,
        f"批量添加到「{group.name}」\n\n{hint}",
        keyboards.batch_add_selector(
            chats,
            group_id=group_id,
            page=page,
            selected_chat_ids=selected_chat_ids,
        ),
    )


async def _show_main_menu(message: Message | CallbackQuery, role: str) -> None:
    text = (
        "机器人控制台\n\n"
        f"当前权限：{role}\n"
        "请选择要执行的操作。"
    )
    markup = keyboards.main_menu(role)
    if isinstance(message, CallbackQuery):
        await _safe_edit(message, text, markup)
    else:
        await message.answer(text, reply_markup=markup)


def _command_name(message: Message) -> str:
    raw = (message.text or "").split(maxsplit=1)[0]
    return raw.split("@", 1)[0].lstrip("/").lower()


def _command_args(message: Message) -> str:
    parts = (message.text or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


async def _deliver_message_to_group(
    *,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    operator_user_id: int,
    group_id: int,
    source_chat_id: int,
    source_message_id: int,
) -> tuple[str, bool]:
    group = await repo.get_delivery_group(session, group_id)
    targets = await repo.list_group_chats(session, group_id)
    if group is None or not targets:
        return "分组不存在或暂无群组，无法发送。", False

    job = await repo.create_send_job(
        session,
        operator_user_id=operator_user_id,
        delivery_group_id=group_id,
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
        target_chats=targets,
    )
    await repo.set_send_job_status(session, job.id, "running")
    await session.commit()

    success_count = 0
    failed_count = 0
    failed_lines: list[str] = []

    for chat in targets:
        try:
            sent_message = await bot.copy_message(
                chat_id=chat.chat_id,
                from_chat_id=source_chat_id,
                message_id=source_message_id,
            )
            success_count += 1
            await repo.mark_send_target(session, job.id, chat.chat_id, "sent", sent_message.message_id)
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
            try:
                sent_message = await bot.copy_message(
                    chat_id=chat.chat_id,
                    from_chat_id=source_chat_id,
                    message_id=source_message_id,
                )
                success_count += 1
                await repo.mark_send_target(session, job.id, chat.chat_id, "sent", sent_message.message_id)
            except Exception as retry_exc:
                failed_count += 1
                error = str(retry_exc)
                failed_lines.append(f"- {chat.title} | {chat.chat_id} | {error[:120]}")
                await repo.mark_send_target(session, job.id, chat.chat_id, "failed", error_message=error)
        except TelegramForbiddenError as exc:
            failed_count += 1
            error = str(exc)
            failed_lines.append(f"- {chat.title} | {chat.chat_id} | {error[:120]}")
            await repo.mark_chat_status(session, chat.chat_id, "no_permission")
            await repo.mark_send_target(session, job.id, chat.chat_id, "failed", error_message=error)
        except TelegramAPIError as exc:
            failed_count += 1
            error = str(exc)
            failed_lines.append(f"- {chat.title} | {chat.chat_id} | {error[:120]}")
            await repo.mark_send_target(session, job.id, chat.chat_id, "failed", error_message=error)

        await session.commit()
        if settings.send_delay_seconds:
            await asyncio.sleep(settings.send_delay_seconds)

    await repo.finish_send_job(session, job.id, success_count, failed_count)

    report = (
        f"发送完成\n\n"
        f"分组：{group.name}\n"
        f"成功：{success_count}\n"
        f"失败：{failed_count}"
    )
    if failed_lines:
        report += "\n\n失败明细：\n" + "\n".join(failed_lines[:10])
        if len(failed_lines) > 10:
            report += f"\n... 另有 {len(failed_lines) - 10} 条失败"

    if success_count > 0:
        await _notify_owners_operator_sent_message(
            bot=bot,
            session=session,
            settings=settings,
            operator_user_id=operator_user_id,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            target_text=f"分组「{group.name}」({success_count}/{len(targets)})",
        )

    return report, True


async def _deliver_message_to_chat(
    *,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    operator_user_id: int,
    target_chat_id: int,
    source_chat_id: int,
    source_message_id: int,
) -> tuple[str, bool]:
    chat = await repo.get_active_chat(session, target_chat_id)
    if chat is None:
        return "群组不存在或不可用，无法发送。", False

    try:
        sent_message = await bot.copy_message(
            chat_id=chat.chat_id,
            from_chat_id=source_chat_id,
            message_id=source_message_id,
        )
        await repo.record_direct_send_message(
            session,
            operator_user_id=operator_user_id,
            target_chat_id=chat.chat_id,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            sent_message_id=sent_message.message_id,
        )
        await session.commit()
        await _notify_owners_operator_sent_message(
            bot=bot,
            session=session,
            settings=settings,
            operator_user_id=operator_user_id,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            target_text=f"群「{chat.title}」",
        )
        return f"发送完成\n\n目标群：{chat.title}\n成功：1\n失败：0", True
    except TelegramForbiddenError as exc:
        await repo.mark_chat_status(session, chat.chat_id, "no_permission")
        await repo.add_audit_log(
            session,
            operator_user_id,
            "direct_send_failed",
            "tg_chat",
            str(chat.chat_id),
            str(exc)[:500],
        )
        await session.commit()
        return f"发送失败\n\n目标群：{chat.title}\n原因：{str(exc)[:180]}", False
    except TelegramAPIError as exc:
        await repo.add_audit_log(
            session,
            operator_user_id,
            "direct_send_failed",
            "tg_chat",
            str(chat.chat_id),
            str(exc)[:500],
        )
        await session.commit()
        return f"发送失败\n\n目标群：{chat.title}\n原因：{str(exc)[:180]}", False


async def _notify_reply_if_needed(
    *,
    message: Message,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if message.reply_to_message is None:
        return
    if message.from_user is not None and message.from_user.id == bot.id:
        return

    replied_from = message.reply_to_message.from_user
    if replied_from is None or replied_from.id != bot.id:
        return

    match = await repo.find_sent_message_match(
        session,
        target_chat_id=message.chat.id,
        sent_message_id=message.reply_to_message.message_id,
    )

    recipients = set(settings.owner_user_ids)
    if match is not None:
        operator_role = await repo.get_user_role(session, match.operator_user_id, settings.owner_user_ids)
        if operator_role is not None:
            recipients.add(match.operator_user_id)

    original_deleted = False
    if settings.reply_auto_delete_original and match is not None:
        try:
            await bot.delete_message(
                chat_id=message.chat.id,
                message_id=message.reply_to_message.message_id,
            )
            original_deleted = True
            await repo.add_audit_log(
                session,
                match.operator_user_id,
                "auto_delete_replied_original",
                match.target_type,
                str(match.target_id),
                f"chat={message.chat.id}, original={message.reply_to_message.message_id}, reply={message.message_id}",
            )
            await session.commit()
        except TelegramAPIError:
            original_deleted = False

    reply_url = _telegram_message_url(message.chat.id, message.message_id, message.chat.username)
    original_url = None
    if not original_deleted:
        original_url = _telegram_message_url(
            message.chat.id,
            message.reply_to_message.message_id,
            message.chat.username,
        )
    if original_url == reply_url:
        original_url = None
    reply_markup = keyboards.reply_notice_actions(
        chat_id=message.chat.id,
        reply_message_id=message.message_id,
        reply_url=reply_url,
        original_url=original_url,
    )
    _, should_copy_original = _reply_content_preview(message)
    notice = _build_reply_notice(message, group_url=reply_url)
    media_notice = _build_reply_notice(message, content_limit=420, group_url=reply_url, max_length=900)

    for user_id in recipients:
        try:
            if should_copy_original and _can_embed_notice_in_copied_message(message):
                try:
                    await bot.copy_message(
                        chat_id=user_id,
                        from_chat_id=message.chat.id,
                        message_id=message.message_id,
                        caption=media_notice,
                        parse_mode="HTML",
                        reply_markup=reply_markup,
                    )
                    continue
                except TelegramAPIError:
                    pass

            await bot.send_message(
                user_id,
                notice,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=reply_markup,
            )
            if should_copy_original:
                await bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id,
                )
        except TelegramAPIError:
            continue


@router.my_chat_member()
async def on_my_chat_member(update: ChatMemberUpdated, session: AsyncSession) -> None:
    if update.chat.type not in {"group", "supergroup", "channel"}:
        return

    status = _chat_status_from_member_update(update)
    await repo.upsert_chat(
        session,
        chat_id=update.chat.id,
        title=update.chat.title,
        chat_type=update.chat.type,
        username=update.chat.username,
        status=status,
    )


async def _register_chat_from_message(message: Message, session: AsyncSession) -> None:
    if message.migrate_to_chat_id:
        await repo.migrate_chat(
            session,
            old_chat_id=message.chat.id,
            new_chat_id=message.migrate_to_chat_id,
            title=message.chat.title,
            chat_type="supergroup",
            username=message.chat.username,
        )
        return

    await repo.upsert_chat(
        session,
        chat_id=message.chat.id,
        title=message.chat.title,
        chat_type=message.chat.type,
        username=message.chat.username,
        status="active",
    )


@router.message(F.chat.type.in_({"group", "supergroup"}), Command("register", "sync_group"))
async def register_group_command(message: Message, session: AsyncSession) -> None:
    await _register_chat_from_message(message, session)
    await message.answer(
        "已登记当前群组。\n\n"
        f"群组：{message.chat.title or message.chat.id}\n"
        f"群ID：{message.chat.id}\n\n"
        "现在可以到机器人私聊里的「分组管理」把这个群添加到分组。"
    )


@router.message(F.chat.type.in_({"group", "supergroup", "channel"}))
async def remember_group_from_message(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
) -> None:
    await _register_chat_from_message(message, session)
    await _notify_reply_if_needed(message=message, bot=bot, session=session, settings=settings)


@router.message(Command("id", "uid"))
async def uid_command(message: Message, session: AsyncSession, settings: Settings) -> None:
    if message.from_user is None:
        return
    role = await repo.get_user_role(session, message.from_user.id, settings.owner_user_ids)
    if role is not None and await repo.can_create_child_operator(
        session,
        message.from_user.id,
        role,
        settings.owner_user_ids,
    ):
        await message.answer(
            "点击下方「选择用户」按钮，选择后机器人会显示对方 UID。",
            reply_markup=keyboards.user_picker_keyboard(),
        )
        return
    await message.answer(_format_uid_text(message.from_user))


@router.message(F.chat.type == "private", F.text.in_({"查询UID", "我的UID", "UID", "uid", "查询ID", "我的ID", "id"}))
async def uid_text(message: Message, session: AsyncSession, settings: Settings) -> None:
    if message.from_user is None:
        return
    role = await repo.get_user_role(session, message.from_user.id, settings.owner_user_ids)
    if role is not None and await repo.can_create_child_operator(
        session,
        message.from_user.id,
        role,
        settings.owner_user_ids,
    ):
        await message.answer(
            "点击下方「选择用户」按钮，选择后机器人会显示对方 UID。",
            reply_markup=keyboards.user_picker_keyboard(),
        )
        return
    await message.answer(_format_uid_text(message.from_user))


@router.message(F.users_shared)
async def users_shared_uid(message: Message, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(message, session, settings)
    if role is None:
        return
    if not await repo.can_create_child_operator(session, message.from_user.id, role, settings.owner_user_ids):
        await message.answer(
            "你不能创建下级操作人，无法使用选择用户查 UID。",
            reply_markup=ReplyKeyboardRemove(remove_keyboard=True),
        )
        return
    if message.users_shared is None or not message.users_shared.users:
        await message.answer("没有收到用户信息。", reply_markup=ReplyKeyboardRemove(remove_keyboard=True))
        return
    lines = [_format_shared_user(user) for user in message.users_shared.users]
    await message.answer(
        "已选择用户\n\n" + "\n\n".join(lines),
        reply_markup=ReplyKeyboardRemove(remove_keyboard=True),
    )


@router.callback_query(F.data == "user:uid")
async def uid_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(_format_uid_text(callback.from_user))


@router.message(Command("start", "menu"))
async def start(message: Message, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    role = await _role_or_reject(message, session, settings)
    if role is None:
        return
    await _show_main_menu(message, role)


@router.message(Command("cancel"))
async def cancel_command(message: Message, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    role = await _role_or_reject(message, session, settings)
    if role is None:
        return
    await state.clear()
    await message.answer("已取消当前操作。", reply_markup=keyboards.main_menu(role))


@router.message(F.text.in_({"取消", "停止", "退出"}))
async def cancel_text_command(message: Message, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    role = await _role_or_reject(message, session, settings)
    if role is None:
        return
    await state.clear()
    await message.answer("已取消当前操作。", reply_markup=keyboards.main_menu(role))


@router.callback_query(F.data == "state:cancel")
async def cancel_callback(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    await state.clear()
    await _safe_edit(callback, "已取消当前操作。", keyboards.main_menu(role))


@router.callback_query(F.data.startswith("reply:start:"))
async def reply_start(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return

    _, _, chat_id_raw, message_id_raw = callback.data.split(":")
    target_chat_id = int(chat_id_raw)
    target_message_id = int(message_id_raw)

    await state.set_state(ReplyForm.wait_message)
    await state.update_data(
        target_chat_id=target_chat_id,
        target_message_id=target_message_id,
    )

    await callback.answer("已进入快速回复模式。")
    if callback.message is not None:
        await callback.message.answer(
            "请发送要回复到群里的内容。\n"
            "机器人会把你的下一条消息发送到原群，并回复那条群消息。\n"
            "发送 /cancel 取消。",
            reply_markup=keyboards.cancel_keyboard(),
        )


@router.message(ReplyForm.wait_message)
async def reply_wait_message(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    role = await _role_or_reject(message, session, settings)
    if role is None:
        return

    if message.media_group_id:
        await message.answer("第一版暂不合并媒体相册，请把内容作为单条消息发送。", reply_markup=keyboards.cancel_keyboard())
        return

    data = await state.get_data()
    target_chat_id = int(data["target_chat_id"])
    target_message_id = int(data["target_message_id"])

    try:
        await bot.copy_message(
            chat_id=target_chat_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            reply_to_message_id=target_message_id,
            allow_sending_without_reply=True,
        )
        await repo.add_audit_log(
            session,
            message.from_user.id,
            "reply_to_group",
            "message",
            f"{target_chat_id}:{target_message_id}",
        )
        await _notify_owners_operator_sent_message(
            bot=bot,
            session=session,
            settings=settings,
            operator_user_id=message.from_user.id,
            source_chat_id=message.chat.id,
            source_message_id=message.message_id,
            target_text=f"快速回复 {target_chat_id}:{target_message_id}",
        )
        await state.clear()
        await message.answer("已回复到群里。", reply_markup=keyboards.main_menu(role))
    except TelegramAPIError as exc:
        await state.clear()
        await message.answer(
            f"回复失败：{str(exc)[:180]}",
            reply_markup=keyboards.main_menu(role),
        )


async def _start_quick_send_from_text(
    *,
    message: Message,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
    role: str,
    command: str,
    value: str,
) -> None:
    if not await repo.can_group_broadcast(session, message.from_user.id, role):
        await message.answer("你的分组群发权限已关闭。", reply_markup=keyboards.main_menu(role))
        return

    if not value:
        await message.answer(
            "请输入分组名称或分组 ID。\n\n"
            "一次性发送：发送到 分组名\n"
            "连续快捷发送：快捷发送 分组名\n"
            "停止当前操作：取消",
            reply_markup=keyboards.main_menu(role),
        )
        return

    group = await repo.find_delivery_group(session, value)
    if group is None or not await repo.has_group_access(session, message.from_user.id, role, group.id):
        await message.answer("未找到这个分组，或你没有权限访问。", reply_markup=keyboards.main_menu(role))
        return

    target_count = await repo.count_group_chats(session, group.id)
    if target_count == 0:
        await message.answer("这个分组暂无群组，不能发送。", reply_markup=keyboards.group_detail(group, 0))
        return

    if command == "to" and message.reply_to_message is not None:
        if message.reply_to_message.media_group_id:
            await message.answer("第一版暂不合并媒体相册，请把内容作为单条消息发送。")
            return
        await message.answer(f"开始发送到「{group.name}」，目标群数量：{target_count}。")
        report, _ = await _deliver_message_to_group(
            bot=bot,
            session=session,
            settings=settings,
            operator_user_id=message.from_user.id,
            group_id=group.id,
            source_chat_id=message.chat.id,
            source_message_id=message.reply_to_message.message_id,
        )
        await state.clear()
        await message.answer(report, reply_markup=keyboards.main_menu(role))
        return

    keep_quick = command in {"quick", "q"}
    await state.set_state(SendForm.wait_message)
    await state.update_data(target_type="group", group_id=group.id, auto_send=True, keep_quick=keep_quick)

    if keep_quick:
        await message.answer(
            f"已进入快捷发送模式：{group.name}\n"
            f"目标群数量：{target_count}\n\n"
            "接下来你私聊发送的每条单条消息都会自动投递到这个分组。\n"
            "点击「取消 / 停止」退出快捷发送。",
            reply_markup=keyboards.cancel_keyboard(),
        )
    else:
        await message.answer(
            f"已选择一次性快捷发送：{group.name}\n"
            f"目标群数量：{target_count}\n\n"
            "请发送下一条要投递的消息，机器人会直接发送，不再二次确认。",
            reply_markup=keyboards.cancel_keyboard(),
        )


@router.message(Command("to", "quick", "q"))
async def quick_send_command(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    role = await _role_or_reject(message, session, settings)
    if role is None:
        return

    await _start_quick_send_from_text(
        message=message,
        bot=bot,
        session=session,
        settings=settings,
        state=state,
        role=role,
        command=_command_name(message),
        value=_command_args(message),
    )


@router.message(F.text.regexp(r"^(发送到|快捷发送|连续发送)\s+.+"))
async def chinese_quick_send_command(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    role = await _role_or_reject(message, session, settings)
    if role is None:
        return

    text = (message.text or "").strip()
    action, value = text.split(maxsplit=1)
    command = "to" if action == "发送到" else "quick"
    await _start_quick_send_from_text(
        message=message,
        bot=bot,
        session=session,
        settings=settings,
        state=state,
        role=role,
        command=command,
        value=value.strip(),
    )


@router.callback_query(F.data == "menu:main")
async def menu_main(callback: CallbackQuery, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    await _show_main_menu(callback, role)


@router.callback_query(F.data == "menu:groups")
async def menu_groups(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    groups = await repo.list_accessible_delivery_groups(session, callback.from_user.id, role)
    await _safe_edit(callback, "分组管理\n\n括号内是当前已绑定群数量。", keyboards.groups_menu(groups))


@router.callback_query(F.data == "group:new")
async def group_new(callback: CallbackQuery, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    await state.set_state(GroupForm.new_name)
    await _safe_edit(callback, "请输入新分组名称。", keyboards.cancel_keyboard())


@router.message(GroupForm.new_name)
async def group_new_name(message: Message, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    role = await _role_or_reject(message, session, settings)
    if role is None:
        return

    name = _normalize_group_name(message.text or "")
    if not name:
        await message.answer("分组名称不能为空，请重新输入。", reply_markup=keyboards.cancel_keyboard())
        return
    if len(name) > 120:
        await message.answer("分组名称不能超过 120 个字符，请重新输入。", reply_markup=keyboards.cancel_keyboard())
        return
    if await repo.group_name_exists(session, name):
        await message.answer("这个分组名称已存在，请换一个。", reply_markup=keyboards.cancel_keyboard())
        return

    group = await repo.create_delivery_group(session, name, message.from_user.id)
    if role != "owner":
        await repo.set_operator_group_access(
            session,
            user_id=message.from_user.id,
            group_id=group.id,
            enabled=True,
            changed_by=message.from_user.id,
        )
    await state.clear()
    await message.answer(
        f"已创建分组：{group.name}",
        reply_markup=keyboards.group_detail(group, chat_count=0),
    )


@router.callback_query(F.data.startswith("group:view:"))
async def group_view(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    group_id = int(callback.data.split(":")[2])
    if not await _require_group_access(callback, session, role, group_id):
        return
    group = await repo.get_delivery_group(session, group_id)
    if group is None:
        await _safe_edit(callback, "分组不存在或已删除。", keyboards.back_to_main())
        return
    chat_count = await repo.count_group_chats(session, group_id)
    await _safe_edit(
        callback,
        f"分组：{group.name}\n当前群数量：{chat_count}",
        keyboards.group_detail(group, chat_count),
    )


@router.callback_query(F.data.startswith("group:rename:"))
async def group_rename(callback: CallbackQuery, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    group_id = int(callback.data.split(":")[2])
    if not await _require_group_access(callback, session, role, group_id):
        return
    group = await repo.get_delivery_group(session, group_id)
    if group is None:
        await _safe_edit(callback, "分组不存在或已删除。", keyboards.back_to_main())
        return
    await state.set_state(GroupForm.rename_name)
    await state.update_data(group_id=group_id)
    await _safe_edit(callback, f"当前分组名：{group.name}\n请输入新的分组名称。", keyboards.cancel_keyboard())


@router.message(GroupForm.rename_name)
async def group_rename_name(message: Message, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    role = await _role_or_reject(message, session, settings)
    if role is None:
        return
    data = await state.get_data()
    group_id = int(data["group_id"])
    if not await _has_message_group_access(message, session, role, group_id):
        await state.clear()
        return
    name = _normalize_group_name(message.text or "")
    if not name:
        await message.answer("分组名称不能为空，请重新输入。", reply_markup=keyboards.cancel_keyboard())
        return
    if await repo.group_name_exists(session, name, exclude_group_id=group_id):
        await message.answer("这个分组名称已存在，请换一个。", reply_markup=keyboards.cancel_keyboard())
        return

    ok = await repo.rename_delivery_group(session, group_id, name, message.from_user.id)
    await state.clear()
    if not ok:
        await message.answer("分组不存在或已删除。", reply_markup=keyboards.back_to_main())
        return
    group = await repo.get_delivery_group(session, group_id)
    chat_count = await repo.count_group_chats(session, group_id)
    await message.answer(f"已重命名为：{name}", reply_markup=keyboards.group_detail(group, chat_count))


@router.callback_query(F.data.startswith("group:delete:"))
async def group_delete(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    group_id = int(callback.data.split(":")[2])
    if not await _require_group_access(callback, session, role, group_id):
        return
    group = await repo.get_delivery_group(session, group_id)
    if group is None:
        await _safe_edit(callback, "分组不存在或已删除。", keyboards.back_to_main())
        return
    await _safe_edit(
        callback,
        f"确认删除分组「{group.name}」？\n删除后该分组的群绑定也会移除。",
        keyboards.confirm_group_delete(group_id),
    )


@router.callback_query(F.data.startswith("group:delete_confirm:"))
async def group_delete_confirm(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    group_id = int(callback.data.split(":")[2])
    if not await _require_group_access(callback, session, role, group_id):
        return
    ok = await repo.delete_delivery_group(session, group_id, callback.from_user.id)
    groups = await repo.list_accessible_delivery_groups(session, callback.from_user.id, role)
    text = "分组已删除。" if ok else "分组不存在或已删除。"
    await _safe_edit(callback, f"{text}\n\n分组管理", keyboards.groups_menu(groups))


@router.callback_query(F.data.startswith("group:add:"))
async def group_add_list(callback: CallbackQuery, bot: Bot, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    _, _, group_id_raw, page_raw = callback.data.split(":")
    group_id = int(group_id_raw)
    page = int(page_raw)
    if not await _require_group_access(callback, session, role, group_id):
        return
    group = await repo.get_delivery_group(session, group_id)
    if group is None:
        await _safe_edit(callback, "分组不存在或已删除。", keyboards.back_to_main())
        return
    chats = await repo.list_available_chats_for_group(session, group_id)
    chats = await _filter_chats_visible_to_operator(bot, chats, callback.from_user.id, role)
    if chats:
        hint = "请选择群组。"
    elif role == "owner":
        hint = "暂无可添加群组。"
    else:
        hint = "暂无可添加群组，或机器人无法确认你在这些群内。"
    text = f"给「{group.name}」添加群组\n\n{hint}"
    await _safe_edit(callback, text, keyboards.group_chat_selector(chats, group_id=group_id, page=page, mode="add"))


@router.callback_query(F.data.startswith("group:add_chat:"))
async def group_add_chat(callback: CallbackQuery, bot: Bot, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    _, _, group_id_raw, chat_id_raw = callback.data.split(":")
    group_id = int(group_id_raw)
    chat_id = int(chat_id_raw)
    if not await _require_group_access(callback, session, role, group_id):
        return
    if role != "owner" and not await _is_user_chat_member(bot, chat_id, callback.from_user.id):
        group = await repo.get_delivery_group(session, group_id)
        if group is None:
            await _safe_edit(callback, "分组不存在或已删除。", keyboards.back_to_main())
            return
        chat_count = await repo.count_group_chats(session, group_id)
        await _safe_edit(
            callback,
            "添加失败：你不在这个群内，或机器人无法确认你的群成员状态。",
            keyboards.group_detail(group, chat_count),
        )
        return
    ok = await repo.add_chat_to_group(session, group_id, chat_id, callback.from_user.id)
    group = await repo.get_delivery_group(session, group_id)
    if group is None:
        await _safe_edit(callback, "分组不存在或已删除。", keyboards.back_to_main())
        return
    chat_count = await repo.count_group_chats(session, group_id)
    text = "已添加群组。" if ok else "添加失败：群不存在、已失效或分组不存在。"
    await _safe_edit(callback, f"{text}\n\n分组：{group.name}\n当前群数量：{chat_count}", keyboards.group_detail(group, chat_count))


@router.callback_query(F.data.startswith("batch:add:list:"))
async def batch_add_list(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    _, _, _, group_id_raw, page_raw = callback.data.split(":")
    await _show_batch_add_page(
        callback=callback,
        bot=bot,
        session=session,
        state=state,
        group_id=int(group_id_raw),
        page=int(page_raw),
        role=role,
    )


@router.callback_query(F.data.startswith("batch:add:toggle:"))
async def batch_add_toggle(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    _, _, _, group_id_raw, page_raw, chat_id_raw = callback.data.split(":")
    group_id = int(group_id_raw)
    page = int(page_raw)
    chat_id = int(chat_id_raw)
    if not await _require_group_access(callback, session, role, group_id):
        return

    chats = await _visible_available_chats_for_group(
        bot=bot,
        session=session,
        group_id=group_id,
        operator_user_id=callback.from_user.id,
        role=role,
    )
    visible_chat_ids = {chat.chat_id for chat in chats}
    if chat_id not in visible_chat_ids:
        await callback.answer("这个群不可添加。", show_alert=False)
        await _show_batch_add_page(
            callback=callback,
            bot=bot,
            session=session,
            state=state,
            group_id=group_id,
            page=page,
            role=role,
        )
        return

    selected_chat_ids = await _get_batch_selected_chat_ids(state, group_id)
    if chat_id in selected_chat_ids:
        selected_chat_ids.remove(chat_id)
    else:
        selected_chat_ids.add(chat_id)
    await _set_batch_selected_chat_ids(state, group_id, selected_chat_ids)
    await _show_batch_add_page(
        callback=callback,
        bot=bot,
        session=session,
        state=state,
        group_id=group_id,
        page=page,
        role=role,
    )


@router.callback_query(F.data.startswith("batch:add:select_page:"))
async def batch_add_select_page(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    _, _, _, group_id_raw, page_raw = callback.data.split(":")
    group_id = int(group_id_raw)
    page = int(page_raw)
    if not await _require_group_access(callback, session, role, group_id):
        return
    chats = await _visible_available_chats_for_group(
        bot=bot,
        session=session,
        group_id=group_id,
        operator_user_id=callback.from_user.id,
        role=role,
    )
    selected_chat_ids = await _get_batch_selected_chat_ids(state, group_id)
    start = page * keyboards.PAGE_SIZE
    end = start + keyboards.PAGE_SIZE
    selected_chat_ids.update(chat.chat_id for chat in chats[start:end])
    await _set_batch_selected_chat_ids(state, group_id, selected_chat_ids)
    await _show_batch_add_page(
        callback=callback,
        bot=bot,
        session=session,
        state=state,
        group_id=group_id,
        page=page,
        role=role,
    )


@router.callback_query(F.data.startswith("batch:add:select_all:"))
async def batch_add_select_all(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    _, _, _, group_id_raw, page_raw = callback.data.split(":")
    group_id = int(group_id_raw)
    page = int(page_raw)
    if not await _require_group_access(callback, session, role, group_id):
        return
    chats = await _visible_available_chats_for_group(
        bot=bot,
        session=session,
        group_id=group_id,
        operator_user_id=callback.from_user.id,
        role=role,
    )
    await _set_batch_selected_chat_ids(state, group_id, {chat.chat_id for chat in chats})
    await _show_batch_add_page(
        callback=callback,
        bot=bot,
        session=session,
        state=state,
        group_id=group_id,
        page=page,
        role=role,
    )


@router.callback_query(F.data.startswith("batch:add:clear:"))
async def batch_add_clear(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    _, _, _, group_id_raw, page_raw = callback.data.split(":")
    group_id = int(group_id_raw)
    if not await _require_group_access(callback, session, role, group_id):
        return
    await _set_batch_selected_chat_ids(state, group_id, set())
    await _show_batch_add_page(
        callback=callback,
        bot=bot,
        session=session,
        state=state,
        group_id=group_id,
        page=int(page_raw),
        role=role,
    )


@router.callback_query(F.data.startswith("batch:add:confirm:"))
async def batch_add_confirm(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    _, _, _, group_id_raw = callback.data.split(":")
    group_id = int(group_id_raw)
    if not await _require_group_access(callback, session, role, group_id):
        return
    group = await repo.get_delivery_group(session, group_id)
    if group is None:
        await _safe_edit(callback, "分组不存在或已删除。", keyboards.back_to_main())
        return

    selected_chat_ids = await _get_batch_selected_chat_ids(state, group_id)
    if not selected_chat_ids:
        await _show_batch_add_page(
            callback=callback,
            bot=bot,
            session=session,
            state=state,
            group_id=group_id,
            page=0,
            role=role,
        )
        return

    chats = await _visible_available_chats_for_group(
        bot=bot,
        session=session,
        group_id=group_id,
        operator_user_id=callback.from_user.id,
        role=role,
    )
    allowed_chat_ids = {chat.chat_id for chat in chats}
    valid_chat_ids = sorted(selected_chat_ids & allowed_chat_ids)

    added_count = 0
    skipped_count = len(selected_chat_ids) - len(valid_chat_ids)
    for chat_id in valid_chat_ids:
        if await repo.add_chat_to_group(session, group_id, chat_id, callback.from_user.id):
            added_count += 1
        else:
            skipped_count += 1

    await _set_batch_selected_chat_ids(state, group_id, set())
    chat_count = await repo.count_group_chats(session, group_id)
    await _safe_edit(
        callback,
        f"批量添加完成。\n\n"
        f"分组：{group.name}\n"
        f"成功添加：{added_count}\n"
        f"跳过：{skipped_count}\n"
        f"当前群数量：{chat_count}",
        keyboards.group_detail(group, chat_count),
    )


@router.callback_query(F.data.startswith("group:remove:"))
async def group_remove_list(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    _, _, group_id_raw, page_raw = callback.data.split(":")
    group_id = int(group_id_raw)
    page = int(page_raw)
    if not await _require_group_access(callback, session, role, group_id):
        return
    group = await repo.get_delivery_group(session, group_id)
    if group is None:
        await _safe_edit(callback, "分组不存在或已删除。", keyboards.back_to_main())
        return
    chats = await repo.list_group_chats(session, group_id)
    text = f"从「{group.name}」删除群组\n\n" + ("请选择要移除的群组。" if chats else "这个分组暂无群组。")
    await _safe_edit(callback, text, keyboards.group_chat_selector(chats, group_id=group_id, page=page, mode="remove"))


@router.callback_query(F.data.startswith("group:remove_chat:"))
async def group_remove_chat(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    _, _, group_id_raw, chat_id_raw = callback.data.split(":")
    group_id = int(group_id_raw)
    chat_id = int(chat_id_raw)
    if not await _require_group_access(callback, session, role, group_id):
        return
    ok = await repo.remove_chat_from_group(session, group_id, chat_id, callback.from_user.id)
    group = await repo.get_delivery_group(session, group_id)
    if group is None:
        await _safe_edit(callback, "分组不存在或已删除。", keyboards.back_to_main())
        return
    chat_count = await repo.count_group_chats(session, group_id)
    text = "已移除群组。" if ok else "移除失败：群不在该分组内。"
    await _safe_edit(callback, f"{text}\n\n分组：{group.name}\n当前群数量：{chat_count}", keyboards.group_detail(group, chat_count))


@router.callback_query(F.data.startswith("group:members:"))
async def group_members(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    _, _, group_id_raw, page_raw = callback.data.split(":")
    group_id = int(group_id_raw)
    page = int(page_raw)
    if not await _require_group_access(callback, session, role, group_id):
        return
    group = await repo.get_delivery_group(session, group_id)
    if group is None:
        await _safe_edit(callback, "分组不存在或已删除。", keyboards.back_to_main())
        return
    chats = await repo.list_group_chats(session, group_id)
    text = f"「{group.name}」已绑定群组\n\n{_format_chats(chats)}"
    await _safe_edit(callback, text, keyboards.group_chat_selector(chats, group_id=group_id, page=page, mode="members"))


@router.callback_query(F.data.startswith("menu:chats"))
async def menu_chats(callback: CallbackQuery, bot: Bot, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    parts = callback.data.split(":")
    page = int(parts[2]) if len(parts) > 2 else 0
    chats = await repo.list_chats_for_accessible_groups(session, callback.from_user.id, role)
    text = "群组库\n\n机器人被拉入群后会自动登记。"
    if chats:
        text += "\n\n" + _format_chats(chats)
    else:
        text += "\n\n暂无可查看群组。"
    await _safe_edit(callback, text, keyboards.chats_library(chats, page))


@router.callback_query(F.data == "menu:operators")
async def menu_operators(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    operators_markup = await _operators_menu_markup(session, settings, callback.from_user.id, role)
    title = "权限管理" if role == "owner" else "下级操作人管理"
    can_add = await repo.can_create_child_operator(session, callback.from_user.id, role, settings.owner_user_ids)
    hint = "可以添加操作人，并给操作人分配可访问的分组。" if can_add else "你当前不能再创建下级操作人。"
    await _safe_edit(
        callback,
        f"{title}\n\n{hint}",
        operators_markup,
    )


@router.callback_query(F.data.startswith("op:view:"))
async def op_view(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    user_id = int(callback.data.split(":")[2])
    if not await _require_operator_management_access(callback, session, settings, role, user_id):
        return
    user = await repo.get_operator(session, user_id)
    if user is None:
        operators_markup = await _operators_menu_markup(session, settings, callback.from_user.id, role)
        await _safe_edit(callback, "操作人不存在。", operators_markup)
        return
    group_count = await repo.count_operator_group_permissions(session, user_id)
    chat_count = await repo.count_operator_chat_permissions(session, user_id)
    flags = await repo.get_operator_feature_flags(session, user_id)
    allow_manage_operators = await repo.can_manage_child_operators(
        session,
        user_id,
        "operator",
        settings.owner_user_ids,
    )
    can_toggle_manage_operators = role == "owner" and user.created_by in settings.owner_user_ids
    display_name = user.remark or user.first_name or "未备注用户"
    username = f"@{user.username}" if user.username else "无 username"
    await _safe_edit(
        callback,
        f"操作人详情\n\n"
        f"名称：{display_name}\n"
        f"Username：{username}\n"
        f"UID：{user.user_id}\n"
        f"状态：{user.status}\n"
        f"分组群发：{'开启' if flags.allow_group_broadcast else '关闭'}\n"
        f"单群发送：{'开启' if flags.allow_direct_send else '关闭'}\n"
        f"设置下级：{'开启' if allow_manage_operators else '关闭'}\n"
        f"已授权分组：{group_count}\n"
        f"已授权单群：{chat_count}",
        keyboards.operator_detail(
            user,
            group_count,
            chat_count,
            allow_group_broadcast=flags.allow_group_broadcast,
            allow_direct_send=flags.allow_direct_send,
            allow_manage_operators=allow_manage_operators,
            can_toggle_manage_operators=can_toggle_manage_operators,
        ),
    )


@router.callback_query(F.data.startswith("op:groups:"))
async def op_groups(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    user_id = int(callback.data.split(":")[2])
    if not await _require_operator_management_access(callback, session, settings, role, user_id):
        return
    user = await repo.get_operator(session, user_id)
    if user is None:
        operators_markup = await _operators_menu_markup(session, settings, callback.from_user.id, role)
        await _safe_edit(callback, "操作人不存在。", operators_markup)
        return
    groups = await repo.list_accessible_delivery_groups(session, callback.from_user.id, role)
    allowed_group_ids = await repo.list_operator_group_ids(session, user_id)
    display_name = user.remark or user.first_name or str(user.user_id)
    await _safe_edit(
        callback,
        f"分组权限：{display_name}\n\n"
        "勾选后，操作人才能看到、管理并发送到该分组。",
        keyboards.operator_group_permissions(user_id, groups, allowed_group_ids),
    )


@router.callback_query(F.data.startswith("op:group_toggle:"))
async def op_group_toggle(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    _, _, user_id_raw, group_id_raw = callback.data.split(":")
    user_id = int(user_id_raw)
    group_id = int(group_id_raw)
    if not await _require_operator_management_access(callback, session, settings, role, user_id):
        return
    if not await repo.has_group_access(session, callback.from_user.id, role, group_id):
        await _safe_edit(callback, "不能授权自己没有权限的分组。", keyboards.main_menu(role))
        return
    allowed_group_ids = await repo.list_operator_group_ids(session, user_id)
    enabled = group_id not in allowed_group_ids
    ok = await repo.set_operator_group_access(
        session,
        user_id=user_id,
        group_id=group_id,
        enabled=enabled,
        changed_by=callback.from_user.id,
    )
    if not ok:
        operators_markup = await _operators_menu_markup(session, settings, callback.from_user.id, role)
        await _safe_edit(callback, "操作人或分组不存在。", operators_markup)
        return
    user = await repo.get_operator(session, user_id)
    groups = await repo.list_accessible_delivery_groups(session, callback.from_user.id, role)
    allowed_group_ids = await repo.list_operator_group_ids(session, user_id)
    display_name = user.remark or user.first_name or str(user.user_id)
    await _safe_edit(
        callback,
        f"分组权限：{display_name}\n\n"
        "勾选后，操作人才能看到、管理并发送到该分组。",
        keyboards.operator_group_permissions(user_id, groups, allowed_group_ids),
    )


@router.callback_query(F.data.startswith("op:feature:"))
async def op_feature_toggle(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    _, _, user_id_raw, feature = callback.data.split(":")
    user_id = int(user_id_raw)
    if not await _require_operator_management_access(callback, session, settings, role, user_id):
        return
    flags = await repo.get_operator_feature_flags(session, user_id)
    if feature == "group_broadcast":
        enabled = not flags.allow_group_broadcast
    elif feature == "direct_send":
        enabled = not flags.allow_direct_send
    elif feature == "manage_operators":
        target = await repo.get_operator(session, user_id)
        if role != "owner" or target is None or target.created_by not in settings.owner_user_ids:
            await callback.answer("只有宿主可以设置直属操作人的下级权限。", show_alert=False)
            return
        enabled = not flags.allow_manage_operators
    else:
        await callback.answer("未知权限。", show_alert=False)
        return
    ok = await repo.set_operator_feature_flag(
        session,
        user_id=user_id,
        feature=feature,
        enabled=enabled,
        changed_by=callback.from_user.id,
    )
    if not ok:
        operators_markup = await _operators_menu_markup(session, settings, callback.from_user.id, role)
        await _safe_edit(callback, "操作人不存在。", operators_markup)
        return
    user = await repo.get_operator(session, user_id)
    await _safe_edit(callback, "权限开关已更新。", await _operator_detail_markup(session, settings, user, role))


@router.callback_query(F.data.startswith("op:chats:"))
async def op_chats(callback: CallbackQuery, bot: Bot, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    _, _, user_id_raw, page_raw = callback.data.split(":")
    user_id = int(user_id_raw)
    page = int(page_raw)
    if not await _require_operator_management_access(callback, session, settings, role, user_id):
        return
    user = await repo.get_operator(session, user_id)
    if user is None:
        operators_markup = await _operators_menu_markup(session, settings, callback.from_user.id, role)
        await _safe_edit(callback, "操作人不存在。", operators_markup)
        return
    chats = await _grantable_direct_chats(
        session=session,
        bot=bot,
        operator_user_id=callback.from_user.id,
        role=role,
    )
    allowed_chat_ids = await repo.list_operator_chat_ids(session, user_id)
    visible_chat_ids = {chat.chat_id for chat in chats}
    allowed_chat_ids &= visible_chat_ids
    display_name = user.remark or user.first_name or str(user.user_id)
    if chats:
        max_page = max(0, (len(chats) - 1) // keyboards.PAGE_SIZE)
        page = min(max(page, 0), max_page)
    else:
        page = 0
    await _safe_edit(
        callback,
        f"单群权限：{display_name}\n\n"
        "勾选后，操作人才能单独发送到该群。单群发送总开关关闭时，勾选权限会保留但不能发送。",
        keyboards.operator_chat_permissions(user_id, chats, allowed_chat_ids, page),
    )


@router.callback_query(F.data.startswith("op:chat_toggle:"))
async def op_chat_toggle(callback: CallbackQuery, bot: Bot, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    _, _, user_id_raw, page_raw, chat_id_raw = callback.data.split(":")
    user_id = int(user_id_raw)
    page = int(page_raw)
    chat_id = int(chat_id_raw)
    if not await _require_operator_management_access(callback, session, settings, role, user_id):
        return
    chats = await _grantable_direct_chats(
        session=session,
        bot=bot,
        operator_user_id=callback.from_user.id,
        role=role,
    )
    grantable_chat_ids = {chat.chat_id for chat in chats}
    if chat_id not in grantable_chat_ids:
        await _safe_edit(callback, "不能授权自己不可发送的群。", keyboards.main_menu(role))
        return
    allowed_chat_ids = await repo.list_operator_chat_ids(session, user_id)
    enabled = chat_id not in allowed_chat_ids
    ok = await repo.set_operator_chat_access(
        session,
        user_id=user_id,
        chat_id=chat_id,
        enabled=enabled,
        changed_by=callback.from_user.id,
    )
    if not ok:
        operators_markup = await _operators_menu_markup(session, settings, callback.from_user.id, role)
        await _safe_edit(callback, "操作人或群组不存在。", operators_markup)
        return
    user = await repo.get_operator(session, user_id)
    allowed_chat_ids = await repo.list_operator_chat_ids(session, user_id)
    allowed_chat_ids &= grantable_chat_ids
    display_name = user.remark or user.first_name or str(user.user_id)
    await _safe_edit(
        callback,
        f"单群权限：{display_name}\n\n"
        "勾选后，操作人才能单独发送到该群。单群发送总开关关闭时，勾选权限会保留但不能发送。",
        keyboards.operator_chat_permissions(user_id, chats, allowed_chat_ids, page),
    )


@router.callback_query(F.data.startswith("op:remark:"))
async def op_remark(callback: CallbackQuery, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    user_id = int(callback.data.split(":")[2])
    if not await _require_operator_management_access(callback, session, settings, role, user_id):
        return
    user = await repo.get_operator(session, user_id)
    if user is None:
        operators_markup = await _operators_menu_markup(session, settings, callback.from_user.id, role)
        await _safe_edit(callback, "操作人不存在。", operators_markup)
        return
    await state.set_state(OperatorForm.edit_remark)
    await state.update_data(operator_user_id=user_id)
    current = user.remark or "未设置"
    await _safe_edit(
        callback,
        f"当前备注：{current}\n\n请输入新的备注。\n发送「清空」可以移除备注。",
        keyboards.cancel_keyboard(),
    )


@router.message(OperatorForm.edit_remark)
async def op_edit_remark_value(
    message: Message,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    role = await _role_or_reject(message, session, settings)
    if role is None:
        return
    data = await state.get_data()
    user_id = int(data["operator_user_id"])
    if not await _has_message_operator_management_access(message, session, settings, role, user_id):
        await state.clear()
        return
    raw = (message.text or "").strip()
    remark = None if raw == "清空" else raw
    if remark is not None:
        remark = " ".join(remark.split())
    if remark is not None and len(remark) > 255:
        await message.answer("备注不能超过 255 个字符，请重新输入。", reply_markup=keyboards.cancel_keyboard())
        return

    ok = await repo.update_operator_remark(session, user_id, remark, changed_by=message.from_user.id)
    await state.clear()
    user = await repo.get_operator(session, user_id)
    if not ok or user is None:
        operators_markup = await _operators_menu_markup(session, settings, message.from_user.id, role)
        await message.answer("操作人不存在。", reply_markup=operators_markup)
        return
    await message.answer("备注已更新。", reply_markup=await _operator_detail_markup(session, settings, user, role))


@router.callback_query(F.data.startswith("op:delete:"))
async def op_delete(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    user_id = int(callback.data.split(":")[2])
    if not await _require_operator_management_access(callback, session, settings, role, user_id):
        return
    user = await repo.get_operator(session, user_id)
    if user is None:
        operators_markup = await _operators_menu_markup(session, settings, callback.from_user.id, role)
        await _safe_edit(callback, "操作人不存在。", operators_markup)
        return
    display_name = user.remark or user.first_name or user.username or str(user.user_id)
    await _safe_edit(
        callback,
        f"确认删除操作人「{display_name}」？\n\n删除后会移除他的分组权限，发送历史仍会保留 UID 记录。",
        keyboards.confirm_operator_delete(user_id),
    )


@router.callback_query(F.data.startswith("op:delete_confirm:"))
async def op_delete_confirm(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    user_id = int(callback.data.split(":")[2])
    if not await _require_operator_management_access(callback, session, settings, role, user_id):
        return
    ok = await repo.delete_operator(session, user_id, deleted_by=callback.from_user.id)
    operators_markup = await _operators_menu_markup(session, settings, callback.from_user.id, role)
    text = "操作人已删除。" if ok else "操作人不存在。"
    await _safe_edit(callback, f"{text}\n\n权限管理", operators_markup)


@router.callback_query(F.data == "op:add")
async def op_add(callback: CallbackQuery, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    if not await repo.can_create_child_operator(session, callback.from_user.id, role, settings.owner_user_ids):
        await _safe_edit(callback, "你不能再创建下级操作人。", keyboards.main_menu(role))
        return
    await state.set_state(OperatorForm.add_operator)
    await _safe_edit(
        callback,
        "请输入操作人的 Telegram 用户 ID，可在后面加备注。\n\n示例：123456789 张三",
        keyboards.cancel_keyboard(),
    )


@router.callback_query(F.data == "op:pick_user")
async def op_pick_user(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    if not await repo.can_create_child_operator(session, callback.from_user.id, role, settings.owner_user_ids):
        await _safe_edit(callback, "你不能创建下级操作人，无法使用选择用户查 UID。", keyboards.main_menu(role))
        return
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            "点击下方「选择用户」按钮，选择后机器人会显示对方 UID。",
            reply_markup=keyboards.user_picker_keyboard(),
        )


@router.message(F.chat.type == "private", F.text.in_({"选择用户UID", "选择用户查UID", "查用户UID"}))
async def pick_user_text(message: Message, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(message, session, settings)
    if role is None:
        return
    if not await repo.can_create_child_operator(session, message.from_user.id, role, settings.owner_user_ids):
        await message.answer("你不能创建下级操作人，无法使用选择用户查 UID。", reply_markup=keyboards.main_menu(role))
        return
    await message.answer(
        "点击下方「选择用户」按钮，选择后机器人会显示对方 UID。",
        reply_markup=keyboards.user_picker_keyboard(),
    )


@router.message(OperatorForm.add_operator)
async def op_add_value(message: Message, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    role = await _role_or_reject(message, session, settings)
    if role is None:
        return
    if not await repo.can_create_child_operator(session, message.from_user.id, role, settings.owner_user_ids):
        await state.clear()
        await message.answer("你不能再创建下级操作人。", reply_markup=keyboards.main_menu(role))
        return
    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    if not parts:
        await message.answer("请输入 Telegram 用户 ID。", reply_markup=keyboards.cancel_keyboard())
        return
    try:
        user_id = int(parts[0])
    except ValueError:
        await message.answer("用户 ID 必须是数字，请重新输入。", reply_markup=keyboards.cancel_keyboard())
        return
    if user_id in settings.owner_user_ids:
        await message.answer("这个用户已经是宿主，不需要添加为操作人。", reply_markup=keyboards.cancel_keyboard())
        return
    if not await repo.can_add_operator(session, message.from_user.id, role, user_id):
        await message.answer("这个用户已经是其他人的操作人，不能接管。", reply_markup=keyboards.main_menu(role))
        await state.clear()
        return
    remark = parts[1].strip() if len(parts) > 1 else None
    await repo.add_operator(session, user_id, created_by=message.from_user.id, remark=remark)
    await state.clear()
    operators_markup = await _operators_menu_markup(session, settings, message.from_user.id, role)
    await message.answer("已添加操作人。", reply_markup=operators_markup)


@router.callback_query(F.data.startswith("op:disable:"))
async def op_disable(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    user_id = int(callback.data.split(":")[2])
    if not await _require_operator_management_access(callback, session, settings, role, user_id):
        return
    ok = await repo.disable_operator(session, user_id, disabled_by=callback.from_user.id)
    user = await repo.get_operator(session, user_id)
    if not ok or user is None:
        operators_markup = await _operators_menu_markup(session, settings, callback.from_user.id, role)
        await _safe_edit(callback, "操作人不存在。", operators_markup)
        return
    await _safe_edit(callback, "已禁用操作人。", await _operator_detail_markup(session, settings, user, role))


@router.callback_query(F.data.startswith("op:enable:"))
async def op_enable(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    user_id = int(callback.data.split(":")[2])
    if not await _require_operator_management_access(callback, session, settings, role, user_id):
        return
    ok = await repo.enable_operator(session, user_id, enabled_by=callback.from_user.id)
    user = await repo.get_operator(session, user_id)
    if not ok or user is None:
        operators_markup = await _operators_menu_markup(session, settings, callback.from_user.id, role)
        await _safe_edit(callback, "操作人不存在。", operators_markup)
        return
    await _safe_edit(callback, "已启用操作人。", await _operator_detail_markup(session, settings, user, role))


@router.callback_query(F.data == "send:choose")
async def send_choose(callback: CallbackQuery, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    active_groups = []
    group_send_enabled = await repo.can_group_broadcast(session, callback.from_user.id, role)
    if group_send_enabled:
        groups = await repo.list_accessible_delivery_groups(session, callback.from_user.id, role)
        active_groups = [item for item in groups if item.chat_count > 0]
    text = "分组发送\n\n选择要投递的分组，或切换到指定群发送。"
    if not group_send_enabled:
        text = "分组发送权限已关闭。\n\n可以切换到指定群发送。"
    elif not active_groups:
        text = "暂无可发送的分组。请先创建分组并添加群组，或切换到指定群发送。"
    await _safe_edit(callback, text, keyboards.send_group_selector(active_groups))


@router.callback_query(F.data.startswith("send:chat_choose"))
async def send_chat_choose(callback: CallbackQuery, bot: Bot, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    parts = callback.data.split(":")
    page = int(parts[2]) if len(parts) > 2 else 0
    if not await _require_direct_send_enabled(callback, session, role):
        return
    chats = await repo.list_direct_send_chats(session, callback.from_user.id, role)
    if chats:
        max_page = max(0, (len(chats) - 1) // keyboards.PAGE_SIZE)
        page = min(max(page, 0), max_page)
    else:
        page = 0
    text = "指定群发送\n\n请选择目标群。"
    if not chats:
        text = "暂无可单独发送的群。请让宿主在权限管理里给你勾选单群权限。"
    await _safe_edit(callback, text, keyboards.send_chat_selector(chats, page))


@router.callback_query(F.data == "quick:choose")
async def quick_choose(callback: CallbackQuery, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    active_groups = []
    group_send_enabled = await repo.can_group_broadcast(session, callback.from_user.id, role)
    if group_send_enabled:
        groups = await repo.list_accessible_delivery_groups(session, callback.from_user.id, role)
        active_groups = [item for item in groups if item.chat_count > 0]
    text = "快捷发送\n\n请选择目标分组，或切换到指定群快捷发送。"
    if not group_send_enabled:
        text = "分组群发权限已关闭。\n\n可以切换到指定群快捷发送。"
    elif not active_groups:
        text = "暂无可快捷发送的分组。请先创建分组并添加群组，或切换到指定群快捷发送。"
    await _safe_edit(callback, text, keyboards.quick_group_selector(active_groups))


@router.callback_query(F.data.startswith("quick:chat_choose"))
async def quick_chat_choose(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    parts = callback.data.split(":")
    page = int(parts[2]) if len(parts) > 2 else 0
    if not await _require_direct_send_enabled(callback, session, role):
        return
    chats = await repo.list_direct_send_chats(session, callback.from_user.id, role)
    if chats:
        max_page = max(0, (len(chats) - 1) // keyboards.PAGE_SIZE)
        page = min(max(page, 0), max_page)
    else:
        page = 0
    text = "指定群快捷发送\n\n请选择目标群。"
    if not chats:
        text = "暂无可单独快捷发送的群。请让宿主在权限管理里给你勾选单群权限。"
    await _safe_edit(callback, text, keyboards.quick_chat_selector(chats, page))


@router.callback_query(F.data.startswith("quick:chat:"))
async def quick_chat(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    if not await _require_direct_send_enabled(callback, session, role):
        return
    chat_id = int(callback.data.split(":")[2])
    if not await _require_chat_access(callback, session, role, chat_id):
        return
    chat = await repo.get_active_chat(session, chat_id)
    if chat is None:
        await _safe_edit(callback, "群组不存在或不可用。", keyboards.back_to_main())
        return
    await _safe_edit(
        callback,
        f"快捷发送：{chat.title}\n\n"
        "发下一条：下一条私聊消息直接投递，发送后自动退出。\n"
        "连续发送：之后每条私聊消息都直接投递，直到点击停止。",
        keyboards.quick_chat_mode_selector(chat),
    )


@router.callback_query(F.data.startswith("quick:chat_once:") | F.data.startswith("quick:chat_keep:"))
async def quick_chat_mode_start(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    if not await _require_direct_send_enabled(callback, session, role):
        return
    parts = callback.data.split(":")
    mode = parts[1]
    chat_id = int(parts[2])
    if not await _require_chat_access(callback, session, role, chat_id):
        return
    chat = await repo.get_active_chat(session, chat_id)
    if chat is None:
        await _safe_edit(callback, "群组不存在或不可用。", keyboards.back_to_main())
        return

    keep_quick = mode == "chat_keep"
    await state.set_state(SendForm.wait_message)
    await state.update_data(target_type="chat", chat_id=chat_id, auto_send=True, keep_quick=keep_quick)
    if keep_quick:
        text = (
            f"已开启连续快捷发送：{chat.title}\n\n"
            "接下来你私聊发送的每条单条消息都会自动投递到这个群。\n"
            "点击下面按钮即可停止。"
        )
    else:
        text = (
            f"已开启下一条快捷发送：{chat.title}\n\n"
            "请发送下一条要投递的消息，机器人会直接发送，不再二次确认。"
        )
    await _safe_edit(callback, text, keyboards.cancel_keyboard())


@router.callback_query(F.data.startswith("quick:group:"))
async def quick_group(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    if not await _require_group_broadcast_enabled(callback, session, role):
        return
    group_id = int(callback.data.split(":")[2])
    if not await _require_group_access(callback, session, role, group_id):
        return
    group = await repo.get_delivery_group(session, group_id)
    if group is None:
        await _safe_edit(callback, "分组不存在或已删除。", keyboards.back_to_main())
        return
    target_count = await repo.count_group_chats(session, group_id)
    if target_count == 0:
        await _safe_edit(callback, "这个分组暂无群组，不能发送。", keyboards.group_detail(group, 0))
        return
    await _safe_edit(
        callback,
        f"快捷发送：{group.name}\n目标群数量：{target_count}\n\n"
        "发下一条：下一条私聊消息直接投递，发送后自动退出。\n"
        "连续发送：之后每条私聊消息都直接投递，直到点击停止。",
        keyboards.quick_mode_selector(group, target_count),
    )


@router.callback_query(F.data.startswith("quick:once:") | F.data.startswith("quick:keep:"))
async def quick_mode_start(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    if not await _require_group_broadcast_enabled(callback, session, role):
        return
    _, mode, group_id_raw = callback.data.split(":")
    group_id = int(group_id_raw)
    if not await _require_group_access(callback, session, role, group_id):
        return
    group = await repo.get_delivery_group(session, group_id)
    if group is None:
        await _safe_edit(callback, "分组不存在或已删除。", keyboards.back_to_main())
        return
    target_count = await repo.count_group_chats(session, group_id)
    if target_count == 0:
        await _safe_edit(callback, "这个分组暂无群组，不能发送。", keyboards.group_detail(group, 0))
        return

    keep_quick = mode == "keep"
    await state.set_state(SendForm.wait_message)
    await state.update_data(target_type="group", group_id=group_id, auto_send=True, keep_quick=keep_quick)
    if keep_quick:
        text = (
            f"已开启连续快捷发送：{group.name}\n"
            f"目标群数量：{target_count}\n\n"
            "接下来你私聊发送的每条单条消息都会自动投递到这个分组。\n"
            "点击下面按钮即可停止。"
        )
    else:
        text = (
            f"已开启下一条快捷发送：{group.name}\n"
            f"目标群数量：{target_count}\n\n"
            "请发送下一条要投递的消息，机器人会直接发送，不再二次确认。"
        )
    await _safe_edit(callback, text, keyboards.cancel_keyboard())


@router.callback_query(F.data.startswith("send:chat:"))
async def send_chat(callback: CallbackQuery, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    if not await _require_direct_send_enabled(callback, session, role):
        return
    chat_id = int(callback.data.split(":")[2])
    if not await _require_chat_access(callback, session, role, chat_id):
        return
    chat = await repo.get_active_chat(session, chat_id)
    if chat is None:
        await _safe_edit(callback, "群组不存在或不可用。", keyboards.back_to_main())
        return
    await state.set_state(SendForm.wait_message)
    await state.update_data(target_type="chat", chat_id=chat_id)
    await _safe_edit(
        callback,
        f"目标群：{chat.title}\n\n请发送要投递的单条消息。",
        keyboards.cancel_keyboard(),
    )


@router.callback_query(F.data.startswith("send:group:"))
async def send_group(callback: CallbackQuery, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    if not await _require_group_broadcast_enabled(callback, session, role):
        return
    group_id = int(callback.data.split(":")[2])
    if not await _require_group_access(callback, session, role, group_id):
        return
    group = await repo.get_delivery_group(session, group_id)
    if group is None:
        await _safe_edit(callback, "分组不存在或已删除。", keyboards.back_to_main())
        return
    target_count = await repo.count_group_chats(session, group_id)
    if target_count == 0:
        await _safe_edit(callback, "这个分组暂无群组，不能发送。", keyboards.group_detail(group, 0))
        return
    await state.set_state(SendForm.wait_message)
    await state.update_data(target_type="group", group_id=group_id)
    await _safe_edit(
        callback,
        f"目标分组：{group.name}\n目标群数量：{target_count}\n\n请发送要投递的单条消息。",
        keyboards.cancel_keyboard(),
    )


@router.message(SendForm.wait_message)
async def send_wait_message(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    role = await _role_or_reject(message, session, settings)
    if role is None:
        return
    data = await state.get_data()
    if message.media_group_id:
        await message.answer("第一版暂不合并媒体相册，请把内容作为单条消息发送。", reply_markup=keyboards.cancel_keyboard())
        return
    target_type = data.get("target_type", "group")
    if target_type == "chat":
        chat_id = int(data["chat_id"])
        if not await _has_message_direct_send_enabled(message, session, role):
            await state.clear()
            return
        if not await _has_message_chat_access(message, session, role, chat_id):
            await state.clear()
            return
        chat = await repo.get_active_chat(session, chat_id)
        if chat is None:
            await state.clear()
            await message.answer("群组不存在或不可用。", reply_markup=keyboards.back_to_main())
            return
        if data.get("auto_send"):
            keep_quick = bool(data.get("keep_quick"))
            report, ok = await _deliver_message_to_chat(
                bot=bot,
                session=session,
                settings=settings,
                operator_user_id=message.from_user.id,
                target_chat_id=chat_id,
                source_chat_id=message.chat.id,
                source_message_id=message.message_id,
            )
            if keep_quick:
                await state.set_state(SendForm.wait_message)
                await state.update_data(target_type="chat", chat_id=chat_id, auto_send=True, keep_quick=True)
                await message.answer(
                    "已发送。继续发送下一条，或点击「取消 / 停止」退出。" if ok else report,
                    reply_markup=keyboards.cancel_keyboard(),
                )
            else:
                await state.clear()
                await message.answer("已发送。" if ok else report, reply_markup=keyboards.main_menu(role))
            return
        await state.set_state(SendForm.confirm)
        await state.update_data(
            target_type="chat",
            chat_id=chat_id,
            source_chat_id=message.chat.id,
            source_message_id=message.message_id,
        )
        await message.answer(
            f"确认发送到群「{chat.title}」？",
            reply_markup=keyboards.confirm_direct_send(chat_id),
        )
        return

    group_id = int(data["group_id"])
    if not await _has_message_group_broadcast_enabled(message, session, role):
        await state.clear()
        return
    if not await _has_message_group_access(message, session, role, group_id):
        await state.clear()
        return
    group = await repo.get_delivery_group(session, group_id)
    if group is None:
        await state.clear()
        await message.answer("分组不存在或已删除。", reply_markup=keyboards.back_to_main())
        return
    target_count = await repo.count_group_chats(session, group_id)
    if target_count == 0:
        await state.clear()
        await message.answer("这个分组暂无群组，不能发送。", reply_markup=keyboards.group_detail(group, 0))
        return
    if data.get("auto_send"):
        keep_quick = bool(data.get("keep_quick"))
        report, ok = await _deliver_message_to_group(
            bot=bot,
            session=session,
            settings=settings,
            operator_user_id=message.from_user.id,
            group_id=group_id,
            source_chat_id=message.chat.id,
            source_message_id=message.message_id,
        )
        if keep_quick:
            await state.set_state(SendForm.wait_message)
            await state.update_data(target_type="group", group_id=group_id, auto_send=True, keep_quick=True)
            await message.answer(
                "已发送。继续发送下一条，或点击「取消 / 停止」退出。" if ok else report,
                reply_markup=keyboards.cancel_keyboard(),
            )
        else:
            await state.clear()
            await message.answer("已发送。" if ok else report, reply_markup=keyboards.main_menu(role))
        return
    await state.set_state(SendForm.confirm)
    await state.update_data(
        target_type="group",
        group_id=group_id,
        source_chat_id=message.chat.id,
        source_message_id=message.message_id,
    )
    await message.answer(
        f"确认发送到分组「{group.name}」？\n目标群数量：{target_count}",
        reply_markup=keyboards.confirm_send(group_id),
    )


@router.callback_query(F.data == "send:cancel")
async def send_cancel(callback: CallbackQuery, session: AsyncSession, settings: Settings, state: FSMContext) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    await state.clear()
    await _safe_edit(callback, "已取消发送。", keyboards.main_menu(role))


@router.callback_query(F.data.startswith("send:confirm:"))
async def send_confirm(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return

    data = await state.get_data()
    if not data:
        await _safe_edit(callback, "发送状态已过期，请重新选择分组。", keyboards.back_to_main())
        return

    group_id = int(callback.data.split(":")[2])
    if int(data.get("group_id", 0)) != group_id:
        await _safe_edit(callback, "发送状态不匹配，请重新选择分组。", keyboards.back_to_main())
        return
    if not await _require_group_broadcast_enabled(callback, session, role):
        await state.clear()
        return
    if not await _require_group_access(callback, session, role, group_id):
        await state.clear()
        return

    source_chat_id = int(data["source_chat_id"])
    source_message_id = int(data["source_message_id"])
    group = await repo.get_delivery_group(session, group_id)
    target_count = await repo.count_group_chats(session, group_id)
    if group is None or target_count == 0:
        await state.clear()
        await _safe_edit(callback, "分组不存在或暂无群组，无法发送。", keyboards.back_to_main())
        return

    await _safe_edit(callback, f"开始发送到「{group.name}」，目标群数量：{target_count}。")
    report, _ = await _deliver_message_to_group(
        bot=bot,
        session=session,
        settings=settings,
        operator_user_id=callback.from_user.id,
        group_id=group_id,
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
    )
    await state.clear()

    await callback.message.answer(report, reply_markup=keyboards.main_menu(role))


@router.callback_query(F.data.startswith("send:confirm_chat:"))
async def send_confirm_chat(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return

    data = await state.get_data()
    if not data:
        await _safe_edit(callback, "发送状态已过期，请重新选择目标群。", keyboards.back_to_main())
        return

    chat_id = int(callback.data.split(":")[2])
    if int(data.get("chat_id", 0)) != chat_id:
        await _safe_edit(callback, "发送状态不匹配，请重新选择目标群。", keyboards.back_to_main())
        return
    if not await _require_direct_send_enabled(callback, session, role):
        await state.clear()
        return
    if not await _require_chat_access(callback, session, role, chat_id):
        await state.clear()
        return

    source_chat_id = int(data["source_chat_id"])
    source_message_id = int(data["source_message_id"])
    chat = await repo.get_active_chat(session, chat_id)
    if chat is None:
        await state.clear()
        await _safe_edit(callback, "群组不存在或不可用，无法发送。", keyboards.back_to_main())
        return

    await _safe_edit(callback, f"开始发送到「{chat.title}」。")
    report, _ = await _deliver_message_to_chat(
        bot=bot,
        session=session,
        settings=settings,
        operator_user_id=callback.from_user.id,
        target_chat_id=chat_id,
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
    )
    await state.clear()

    await callback.message.answer(report, reply_markup=keyboards.main_menu(role))


@router.callback_query(F.data.startswith("noop:"))
async def noop(callback: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(callback, session, settings)
    if role is None:
        return
    await callback.answer("这里只展示信息。")


@router.message(F.chat.type == "private")
async def private_fallback(message: Message, session: AsyncSession, settings: Settings) -> None:
    role = await _role_or_reject(message, session, settings)
    if role is None:
        return
    await message.answer("请使用菜单操作。", reply_markup=keyboards.main_menu(role))
