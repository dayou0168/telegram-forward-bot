from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import InlineKeyboardMarkup, KeyboardButton, KeyboardButtonRequestUsers, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.models import AuthorizedUser, DeliveryGroup, TgChat
from bot.repositories import DeliveryGroupSummary


PAGE_SIZE = 8
USER_PICKER_REQUEST_ID = 7001


def _trim(text: str, limit: int = 28) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."


def main_menu(role: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="分组管理", callback_data="menu:groups")
    builder.button(text="群组库", callback_data="menu:chats")
    builder.button(text="发送消息", callback_data="send:choose")
    builder.button(text="快捷发送", callback_data="quick:choose")
    builder.button(text="权限管理", callback_data="menu:operators")
    builder.button(text="查询UID", callback_data="op:pick_user")
    if role == "owner":
        builder.button(text="机器人配置", callback_data="config:reply_original")
        builder.adjust(2, 2, 2, 1)
    else:
        builder.adjust(2, 2, 2)
    return builder.as_markup()


def back_to_main() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="返回主菜单", callback_data="menu:main")
    return builder.as_markup()


def groups_menu(groups: Sequence[DeliveryGroupSummary]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="新建分组", callback_data="group:new")
    for item in groups:
        builder.button(
            text=f"{_trim(item.group.name)} ({item.chat_count})",
            callback_data=f"group:view:{item.group.id}",
        )
    builder.button(text="返回主菜单", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def group_detail(group: DeliveryGroup, chat_count: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="添加群组", callback_data=f"group:add:{group.id}:0")
    builder.button(text="批量添加", callback_data=f"batch:add:list:{group.id}:0")
    builder.button(text=f"删除群组 ({chat_count})", callback_data=f"group:remove:{group.id}:0")
    builder.button(text="查看群组", callback_data=f"group:members:{group.id}:0")
    builder.button(text="重命名", callback_data=f"group:rename:{group.id}")
    builder.button(text="删除分组", callback_data=f"group:delete:{group.id}")
    builder.button(text="返回分组列表", callback_data="menu:groups")
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()


def confirm_group_delete(group_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="确认删除", callback_data=f"group:delete_confirm:{group_id}")
    builder.button(text="取消", callback_data=f"group:view:{group_id}")
    builder.adjust(2)
    return builder.as_markup()


def chats_page(
    chats: Sequence[TgChat],
    *,
    page: int,
    page_callback: str,
    return_callback: str,
    item_prefix: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total = len(chats)
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    for chat in chats[start:end]:
        label = f"{_trim(chat.title, 24)} | {chat.chat_id}"
        if item_prefix:
            builder.button(text=label, callback_data=f"{item_prefix}:{chat.chat_id}")
        else:
            builder.button(text=label, callback_data=f"noop:chat:{chat.chat_id}")

    nav_buttons = []
    if page > 0:
        nav_buttons.append(("上一页", f"{page_callback}:{page - 1}"))
    if end < total:
        nav_buttons.append(("下一页", f"{page_callback}:{page + 1}"))
    for text, callback_data in nav_buttons:
        builder.button(text=text, callback_data=callback_data)

    builder.button(text="返回", callback_data=return_callback)
    builder.adjust(1)
    return builder.as_markup()


def group_chat_selector(
    chats: Sequence[TgChat],
    *,
    group_id: int,
    page: int,
    mode: str,
) -> InlineKeyboardMarkup:
    if mode == "add":
        item_prefix = f"group:add_chat:{group_id}"
        page_callback = f"group:add:{group_id}"
    elif mode == "remove":
        item_prefix = f"group:remove_chat:{group_id}"
        page_callback = f"group:remove:{group_id}"
    else:
        item_prefix = None
        page_callback = f"group:members:{group_id}"
    return chats_page(
        chats,
        page=page,
        page_callback=page_callback,
        return_callback=f"group:view:{group_id}",
        item_prefix=item_prefix,
    )


def batch_add_selector(
    chats: Sequence[TgChat],
    *,
    group_id: int,
    page: int,
    selected_chat_ids: set[int],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total = len(chats)
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    page_chats = chats[start:end]

    for chat in page_chats:
        marker = "[x]" if chat.chat_id in selected_chat_ids else "[ ]"
        label = f"{marker} {_trim(chat.title, 22)} | {chat.chat_id}"
        builder.button(text=label, callback_data=f"batch:add:toggle:{group_id}:{page}:{chat.chat_id}")

    if page_chats:
        builder.button(text="本页全选", callback_data=f"batch:add:select_page:{group_id}:{page}")
    if chats:
        builder.button(text="全选可见群", callback_data=f"batch:add:select_all:{group_id}:{page}")
    if selected_chat_ids:
        builder.button(text=f"确认添加 ({len(selected_chat_ids)})", callback_data=f"batch:add:confirm:{group_id}")
        builder.button(text="清空选择", callback_data=f"batch:add:clear:{group_id}:{page}")

    if page > 0:
        builder.button(text="上一页", callback_data=f"batch:add:list:{group_id}:{page - 1}")
    if end < total:
        builder.button(text="下一页", callback_data=f"batch:add:list:{group_id}:{page + 1}")

    builder.button(text="返回", callback_data=f"group:view:{group_id}")
    builder.adjust(1)
    return builder.as_markup()


def chats_library(chats: Sequence[TgChat], page: int) -> InlineKeyboardMarkup:
    return chats_page(
        chats,
        page=page,
        page_callback="menu:chats",
        return_callback="menu:main",
        item_prefix=None,
    )


def send_group_selector(groups: Sequence[DeliveryGroupSummary]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="切换到指定群发送", callback_data="send:chat_choose:0")
    for item in groups:
        builder.button(
            text=f"{_trim(item.group.name)} ({item.chat_count})",
            callback_data=f"send:group:{item.group.id}",
        )
    builder.button(text="返回主菜单", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def send_chat_selector(chats: Sequence[TgChat], page: int) -> InlineKeyboardMarkup:
    return chats_page(
        chats,
        page=page,
        page_callback="send:chat_choose",
        return_callback="send:choose",
        item_prefix="send:chat",
    )


def quick_group_selector(groups: Sequence[DeliveryGroupSummary]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="切换到指定群快捷发送", callback_data="quick:chat_choose:0")
    for item in groups:
        builder.button(
            text=f"{_trim(item.group.name)} ({item.chat_count})",
            callback_data=f"quick:group:{item.group.id}",
        )
    builder.button(text="返回主菜单", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def quick_chat_selector(chats: Sequence[TgChat], page: int) -> InlineKeyboardMarkup:
    return chats_page(
        chats,
        page=page,
        page_callback="quick:chat_choose",
        return_callback="quick:choose",
        item_prefix="quick:chat",
    )


def quick_mode_selector(group: DeliveryGroup, chat_count: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="发下一条", callback_data=f"quick:once:{group.id}")
    builder.button(text="连续发送", callback_data=f"quick:keep:{group.id}")
    builder.button(text="带确认发送", callback_data=f"send:group:{group.id}")
    builder.button(text="返回分组选择", callback_data="quick:choose")
    builder.button(text="返回主菜单", callback_data="menu:main")
    builder.adjust(2, 1, 1, 1)
    return builder.as_markup()


def quick_chat_mode_selector(chat: TgChat) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="发下一条", callback_data=f"quick:chat_once:{chat.chat_id}")
    builder.button(text="连续发送", callback_data=f"quick:chat_keep:{chat.chat_id}")
    builder.button(text="带确认发送", callback_data=f"send:chat:{chat.chat_id}")
    builder.button(text="返回指定群选择", callback_data="quick:chat_choose:0")
    builder.button(text="返回主菜单", callback_data="menu:main")
    builder.adjust(2, 1, 1, 1)
    return builder.as_markup()


def confirm_send(group_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="确认发送", callback_data=f"send:confirm:{group_id}")
    builder.button(text="取消", callback_data="send:cancel")
    builder.adjust(2)
    return builder.as_markup()


def confirm_direct_send(chat_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="确认发送", callback_data=f"send:confirm_chat:{chat_id}")
    builder.button(text="取消", callback_data="send:cancel")
    builder.adjust(2)
    return builder.as_markup()


def reply_original_config() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="改成文字", callback_data="config:reply_original:text")
    builder.button(text="改成图片", callback_data="config:reply_original:photo")
    builder.button(text="返回主菜单", callback_data="menu:main")
    builder.adjust(2, 1)
    return builder.as_markup()


def reply_notice_actions(
    *,
    chat_id: int,
    reply_message_id: int,
    reply_url: str | None,
    original_url: str | None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="快速回复", callback_data=f"reply:start:{chat_id}:{reply_message_id}")
    if reply_url:
        builder.button(text="定位回复消息", url=reply_url)
    if original_url:
        builder.button(text="定位原投递消息", url=original_url)
    builder.adjust(1)
    return builder.as_markup()


def operators_menu(operators: Sequence[AuthorizedUser], can_add_operator: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if can_add_operator:
        builder.button(text="添加操作人", callback_data="op:add")
        builder.button(text="选择用户UID", callback_data="op:pick_user")
    for user in operators:
        status = "启用" if user.status == "active" else "停用"
        display_name = user.remark or user.first_name or "未备注用户"
        username = f" | @{user.username}" if user.username else ""
        builder.button(
            text=f"{status} {_trim(display_name, 16)}{username} | {user.user_id}",
            callback_data=f"op:view:{user.user_id}",
        )
    builder.button(text="返回主菜单", callback_data="menu:main")
    builder.adjust(1)
    return builder.as_markup()


def user_picker_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="选择用户",
                    request_users=KeyboardButtonRequestUsers(
                        request_id=USER_PICKER_REQUEST_ID,
                        user_is_bot=False,
                        max_quantity=1,
                        request_name=True,
                        request_username=True,
                    ),
                )
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="点击按钮选择用户",
    )


def operator_detail(
    user: AuthorizedUser,
    group_count: int,
    chat_count: int,
    *,
    allow_group_broadcast: bool,
    allow_direct_send: bool,
    allow_manage_operators: bool,
    can_toggle_manage_operators: bool,
    receive_sent_notifications: bool,
    receive_reply_notifications: bool,
    can_toggle_visibility: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=f"分组权限 ({group_count})", callback_data=f"op:groups:{user.user_id}")
    builder.button(text=f"单群权限 ({chat_count})", callback_data=f"op:chats:{user.user_id}:0")
    group_text = "群发：开启" if allow_group_broadcast else "群发：关闭"
    direct_text = "单群：开启" if allow_direct_send else "单群：关闭"
    manage_text = "下级：开启" if allow_manage_operators else "下级：关闭"
    sent_text = "看发送：开启" if receive_sent_notifications else "看发送：关闭"
    reply_text = "看回复：开启" if receive_reply_notifications else "看回复：关闭"
    builder.button(text=group_text, callback_data=f"op:feature:{user.user_id}:group_broadcast")
    builder.button(text=direct_text, callback_data=f"op:feature:{user.user_id}:direct_send")
    if can_toggle_manage_operators:
        builder.button(text=manage_text, callback_data=f"op:feature:{user.user_id}:manage_operators")
    if can_toggle_visibility:
        builder.button(text=sent_text, callback_data=f"op:feature:{user.user_id}:sent_notifications")
        builder.button(text=reply_text, callback_data=f"op:feature:{user.user_id}:reply_notifications")
    builder.button(text="编辑备注", callback_data=f"op:remark:{user.user_id}")
    if user.status == "active":
        builder.button(text="停用操作人", callback_data=f"op:disable:{user.user_id}")
    else:
        builder.button(text="启用操作人", callback_data=f"op:enable:{user.user_id}")
    builder.button(text="删除操作人", callback_data=f"op:delete:{user.user_id}")
    builder.button(text="返回权限管理", callback_data="menu:operators")
    builder.adjust(2, 2, 1, 2, 1, 1, 1, 1)
    return builder.as_markup()


def confirm_operator_delete(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="确认删除", callback_data=f"op:delete_confirm:{user_id}")
    builder.button(text="取消", callback_data=f"op:view:{user_id}")
    builder.adjust(2)
    return builder.as_markup()


def operator_group_permissions(
    user_id: int,
    groups: Sequence[DeliveryGroupSummary],
    allowed_group_ids: set[int],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in groups:
        marker = "[x]" if item.group.id in allowed_group_ids else "[ ]"
        builder.button(
            text=f"{marker} {_trim(item.group.name)} ({item.chat_count})",
            callback_data=f"op:group_toggle:{user_id}:{item.group.id}",
        )
    builder.button(text="返回操作人详情", callback_data=f"op:view:{user_id}")
    builder.adjust(1)
    return builder.as_markup()


def operator_chat_permissions(
    user_id: int,
    chats: Sequence[TgChat],
    allowed_chat_ids: set[int],
    page: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total = len(chats)
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    for chat in chats[start:end]:
        marker = "[x]" if chat.chat_id in allowed_chat_ids else "[ ]"
        builder.button(
            text=f"{marker} {_trim(chat.title, 22)} | {chat.chat_id}",
            callback_data=f"op:chat_toggle:{user_id}:{page}:{chat.chat_id}",
        )
    if page > 0:
        builder.button(text="上一页", callback_data=f"op:chats:{user_id}:{page - 1}")
    if end < total:
        builder.button(text="下一页", callback_data=f"op:chats:{user_id}:{page + 1}")
    builder.button(text="返回操作人详情", callback_data=f"op:view:{user_id}")
    builder.adjust(1)
    return builder.as_markup()


def cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="取消 / 停止", callback_data="state:cancel")
    return builder.as_markup()
