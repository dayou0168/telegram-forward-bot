from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.models import (
    AuditLog,
    AuthorizedUser,
    BotSetting,
    DeliveryGroup,
    DeliveryGroupChat,
    DirectSendMessage,
    OperatorChatPermission,
    OperatorFeaturePermission,
    OperatorGroupPermission,
    SendJob,
    SendJobTarget,
    TgChat,
)


@dataclass(frozen=True)
class DeliveryGroupSummary:
    group: DeliveryGroup
    chat_count: int


@dataclass(frozen=True)
class SentMessageMatch:
    operator_user_id: int
    target_type: str
    target_id: int


@dataclass(frozen=True)
class OperatorFeatureFlags:
    allow_group_broadcast: bool
    allow_direct_send: bool
    allow_manage_operators: bool


@dataclass(frozen=True)
class ReplyOriginalReplacement:
    text: str
    text_is_configured: bool
    photo_file_id: str | None = None


REPLY_REPLACEMENT_TEXT_KEY = "reply_original_replacement_text"
REPLY_REPLACEMENT_PHOTO_KEY = "reply_original_replacement_photo_file_id"


async def ensure_owner_users(session: AsyncSession, owner_user_ids: frozenset[int]) -> None:
    for user_id in owner_user_ids:
        user = await session.get(AuthorizedUser, user_id)
        if user is None:
            session.add(
                AuthorizedUser(
                    user_id=user_id,
                    role="owner",
                    status="active",
                    remark="env owner",
                )
            )
        else:
            user.role = "owner"
            user.status = "active"
            user.remark = user.remark or "env owner"


async def get_bot_setting(session: AsyncSession, key: str) -> str | None:
    setting = await session.get(BotSetting, key)
    if setting is None:
        return None
    return setting.value


async def set_bot_setting(session: AsyncSession, key: str, value: str) -> None:
    setting = await session.get(BotSetting, key)
    if setting is None:
        session.add(BotSetting(key=key, value=value))
    else:
        setting.value = value


async def get_reply_original_replacement(
    session: AsyncSession,
    default_text: str,
) -> ReplyOriginalReplacement:
    configured_text = await get_bot_setting(session, REPLY_REPLACEMENT_TEXT_KEY)
    text = configured_text or default_text
    photo_file_id = await get_bot_setting(session, REPLY_REPLACEMENT_PHOTO_KEY)
    return ReplyOriginalReplacement(
        text=text,
        text_is_configured=configured_text is not None,
        photo_file_id=photo_file_id,
    )


async def set_reply_original_replacement_text(session: AsyncSession, text: str, changed_by: int) -> None:
    await set_bot_setting(session, REPLY_REPLACEMENT_TEXT_KEY, text)
    await add_audit_log(session, changed_by, "set_reply_original_replacement_text", "bot_setting", None, text[:500])


async def set_reply_original_replacement_photo(
    session: AsyncSession,
    *,
    photo_file_id: str,
    caption: str,
    changed_by: int,
) -> None:
    await set_bot_setting(session, REPLY_REPLACEMENT_PHOTO_KEY, photo_file_id)
    await add_audit_log(session, changed_by, "set_reply_original_replacement_photo", "bot_setting", None, caption[:500])


async def get_user_role(
    session: AsyncSession,
    user_id: int,
    owner_user_ids: frozenset[int],
) -> str | None:
    if user_id in owner_user_ids:
        return "owner"

    user = await session.get(AuthorizedUser, user_id)
    if user is None or user.status != "active":
        return None
    if user.role == "operator":
        return "operator"
    return None


async def update_authorized_user_profile(
    session: AsyncSession,
    user_id: int,
    username: str | None,
    first_name: str | None,
) -> None:
    user = await session.get(AuthorizedUser, user_id)
    if user is None:
        return
    user.username = username
    user.first_name = first_name


async def add_audit_log(
    session: AsyncSession,
    user_id: int,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: str | None = None,
) -> None:
    session.add(
        AuditLog(
            user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
        )
    )


async def list_operators(session: AsyncSession) -> list[AuthorizedUser]:
    result = await session.execute(
        select(AuthorizedUser)
        .where(AuthorizedUser.role == "operator")
        .order_by(AuthorizedUser.status, AuthorizedUser.user_id)
    )
    return list(result.scalars().all())


async def list_manageable_operators(
    session: AsyncSession,
    manager_user_id: int,
    manager_role: str,
) -> list[AuthorizedUser]:
    stmt = select(AuthorizedUser).where(AuthorizedUser.role == "operator")
    if manager_role != "owner":
        stmt = stmt.where(AuthorizedUser.created_by == manager_user_id)
    stmt = stmt.order_by(AuthorizedUser.status, AuthorizedUser.user_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def can_manage_operator(
    session: AsyncSession,
    manager_user_id: int,
    manager_role: str,
    target_user_id: int,
) -> bool:
    if manager_role == "owner":
        return True
    target = await get_operator(session, target_user_id)
    return target is not None and target.created_by == manager_user_id


async def get_operator(session: AsyncSession, user_id: int) -> AuthorizedUser | None:
    user = await session.get(AuthorizedUser, user_id)
    if user is None or user.role != "operator":
        return None
    return user


async def can_add_operator(
    session: AsyncSession,
    manager_user_id: int,
    manager_role: str,
    target_user_id: int,
) -> bool:
    if manager_role == "owner":
        return True
    existing = await get_operator(session, target_user_id)
    return existing is None or existing.created_by == manager_user_id


async def can_create_child_operator(
    session: AsyncSession,
    manager_user_id: int,
    manager_role: str,
    owner_user_ids: frozenset[int],
) -> bool:
    return await can_manage_child_operators(session, manager_user_id, manager_role, owner_user_ids)


async def add_operator(
    session: AsyncSession,
    user_id: int,
    created_by: int,
    remark: str | None = None,
) -> AuthorizedUser:
    user = await session.get(AuthorizedUser, user_id)
    if user is None:
        user = AuthorizedUser(
            user_id=user_id,
            role="operator",
            status="active",
            remark=remark,
            created_by=created_by,
        )
        session.add(user)
    else:
        user.role = "operator"
        user.status = "active"
        user.remark = remark or user.remark
        user.created_by = user.created_by or created_by
    await add_audit_log(session, created_by, "add_operator", "user", str(user_id), remark)
    return user


async def disable_operator(session: AsyncSession, user_id: int, disabled_by: int) -> bool:
    user = await session.get(AuthorizedUser, user_id)
    if user is None or user.role != "operator":
        return False
    user.status = "disabled"
    await add_audit_log(session, disabled_by, "disable_operator", "user", str(user_id))
    return True


async def enable_operator(session: AsyncSession, user_id: int, enabled_by: int) -> bool:
    user = await session.get(AuthorizedUser, user_id)
    if user is None or user.role != "operator":
        return False
    user.status = "active"
    await add_audit_log(session, enabled_by, "enable_operator", "user", str(user_id))
    return True


async def update_operator_remark(
    session: AsyncSession,
    user_id: int,
    remark: str | None,
    changed_by: int,
) -> bool:
    user = await get_operator(session, user_id)
    if user is None:
        return False
    old_remark = user.remark
    user.remark = remark
    await add_audit_log(
        session,
        changed_by,
        "update_operator_remark",
        "user",
        str(user_id),
        f"{old_remark or ''} -> {remark or ''}",
    )
    return True


async def delete_operator(session: AsyncSession, user_id: int, deleted_by: int) -> bool:
    user = await get_operator(session, user_id)
    if user is None:
        return False

    await session.execute(
        delete(OperatorGroupPermission).where(OperatorGroupPermission.user_id == user_id)
    )
    await session.execute(delete(OperatorChatPermission).where(OperatorChatPermission.user_id == user_id))
    await session.execute(delete(OperatorFeaturePermission).where(OperatorFeaturePermission.user_id == user_id))
    await add_audit_log(
        session,
        deleted_by,
        "delete_operator",
        "user",
        str(user_id),
        user.remark or user.first_name or user.username,
    )
    await session.delete(user)
    return True


async def list_operator_group_ids(session: AsyncSession, user_id: int) -> set[int]:
    result = await session.execute(
        select(OperatorGroupPermission.delivery_group_id).where(
            OperatorGroupPermission.user_id == user_id,
            OperatorGroupPermission.enabled.is_(True),
        )
    )
    return {int(group_id) for group_id in result.scalars().all()}


async def count_operator_group_permissions(session: AsyncSession, user_id: int) -> int:
    result = await session.execute(
        select(func.count(OperatorGroupPermission.id)).where(
            OperatorGroupPermission.user_id == user_id,
            OperatorGroupPermission.enabled.is_(True),
        )
    )
    return int(result.scalar_one())


async def get_operator_feature_flags(session: AsyncSession, user_id: int) -> OperatorFeatureFlags:
    permission = await session.get(OperatorFeaturePermission, user_id)
    if permission is None:
        return OperatorFeatureFlags(
            allow_group_broadcast=True,
            allow_direct_send=True,
            allow_manage_operators=True,
        )
    return OperatorFeatureFlags(
        allow_group_broadcast=permission.allow_group_broadcast,
        allow_direct_send=permission.allow_direct_send,
        allow_manage_operators=permission.allow_manage_operators,
    )


async def set_operator_feature_flag(
    session: AsyncSession,
    *,
    user_id: int,
    feature: str,
    enabled: bool,
    changed_by: int,
) -> bool:
    user = await get_operator(session, user_id)
    if user is None:
        return False
    permission = await session.get(OperatorFeaturePermission, user_id)
    if permission is None:
        permission = OperatorFeaturePermission(user_id=user_id)
        session.add(permission)
        await session.flush()
    if feature == "group_broadcast":
        permission.allow_group_broadcast = enabled
    elif feature == "direct_send":
        permission.allow_direct_send = enabled
    elif feature == "manage_operators":
        permission.allow_manage_operators = enabled
    else:
        return False

    await add_audit_log(
        session,
        changed_by,
        "set_operator_feature_flag",
        "operator_feature_permission",
        f"{user_id}:{feature}",
        f"enabled={enabled}",
    )
    return True


async def can_group_broadcast(session: AsyncSession, user_id: int, role: str) -> bool:
    if role == "owner":
        return True
    if role != "operator":
        return False
    flags = await get_operator_feature_flags(session, user_id)
    return flags.allow_group_broadcast


async def can_direct_send(session: AsyncSession, user_id: int, role: str) -> bool:
    if role == "owner":
        return True
    if role != "operator":
        return False
    flags = await get_operator_feature_flags(session, user_id)
    return flags.allow_direct_send


async def can_manage_child_operators(
    session: AsyncSession,
    user_id: int,
    role: str,
    owner_user_ids: frozenset[int],
) -> bool:
    if role == "owner":
        return True
    if role != "operator":
        return False
    user = await get_operator(session, user_id)
    if user is None or user.created_by not in owner_user_ids:
        return False
    flags = await get_operator_feature_flags(session, user_id)
    return flags.allow_manage_operators


async def bootstrap_legacy_operator_group_permissions(session: AsyncSession, changed_by: int) -> int:
    existing_count_result = await session.execute(select(func.count(OperatorGroupPermission.id)))
    if int(existing_count_result.scalar_one()) > 0:
        return 0

    operators_result = await session.execute(
        select(AuthorizedUser).where(AuthorizedUser.role == "operator")
    )
    operators = list(operators_result.scalars().all())
    groups_result = await session.execute(select(DeliveryGroup).order_by(DeliveryGroup.name))
    groups = list(groups_result.scalars().all())
    if not operators or not groups:
        return 0

    created_count = 0
    for operator in operators:
        for group in groups:
            session.add(
                OperatorGroupPermission(
                    user_id=operator.user_id,
                    delivery_group_id=group.id,
                    enabled=True,
                )
            )
            created_count += 1

    await add_audit_log(
        session,
        changed_by,
        "bootstrap_legacy_operator_group_permissions",
        "operator_group_permission",
        None,
        f"created={created_count}",
    )
    return created_count


async def set_operator_group_access(
    session: AsyncSession,
    user_id: int,
    group_id: int,
    enabled: bool,
    changed_by: int,
) -> bool:
    user = await get_operator(session, user_id)
    group = await get_delivery_group(session, group_id)
    if user is None or group is None:
        return False

    result = await session.execute(
        select(OperatorGroupPermission).where(
            OperatorGroupPermission.user_id == user_id,
            OperatorGroupPermission.delivery_group_id == group_id,
        )
    )
    permission = result.scalar_one_or_none()
    if permission is None:
        permission = OperatorGroupPermission(
            user_id=user_id,
            delivery_group_id=group_id,
            enabled=enabled,
        )
        session.add(permission)
    else:
        permission.enabled = enabled

    await add_audit_log(
        session,
        changed_by,
        "set_operator_group_access",
        "operator_group_permission",
        f"{user_id}:{group_id}",
        f"enabled={enabled}",
    )
    return True


async def list_operator_chat_ids(session: AsyncSession, user_id: int) -> set[int]:
    result = await session.execute(
        select(OperatorChatPermission.chat_id).where(
            OperatorChatPermission.user_id == user_id,
            OperatorChatPermission.enabled.is_(True),
        )
    )
    return {int(chat_id) for chat_id in result.scalars().all()}


async def count_operator_chat_permissions(session: AsyncSession, user_id: int) -> int:
    result = await session.execute(
        select(func.count(OperatorChatPermission.id)).where(
            OperatorChatPermission.user_id == user_id,
            OperatorChatPermission.enabled.is_(True),
        )
    )
    return int(result.scalar_one())


async def set_operator_chat_access(
    session: AsyncSession,
    *,
    user_id: int,
    chat_id: int,
    enabled: bool,
    changed_by: int,
) -> bool:
    user = await get_operator(session, user_id)
    chat = await get_active_chat(session, chat_id)
    if user is None or chat is None:
        return False

    result = await session.execute(
        select(OperatorChatPermission).where(
            OperatorChatPermission.user_id == user_id,
            OperatorChatPermission.chat_id == chat_id,
        )
    )
    permission = result.scalar_one_or_none()
    if permission is None:
        permission = OperatorChatPermission(user_id=user_id, chat_id=chat_id, enabled=enabled)
        session.add(permission)
    else:
        permission.enabled = enabled

    await add_audit_log(
        session,
        changed_by,
        "set_operator_chat_access",
        "operator_chat_permission",
        f"{user_id}:{chat_id}",
        f"enabled={enabled}",
    )
    return True


async def has_group_access(session: AsyncSession, user_id: int, role: str, group_id: int) -> bool:
    if role == "owner":
        return True
    if role != "operator":
        return False
    result = await session.execute(
        select(OperatorGroupPermission.id).where(
            OperatorGroupPermission.user_id == user_id,
            OperatorGroupPermission.delivery_group_id == group_id,
            OperatorGroupPermission.enabled.is_(True),
        )
    )
    return result.scalar_one_or_none() is not None


async def upsert_chat(
    session: AsyncSession,
    chat_id: int,
    title: str | None,
    chat_type: str,
    username: str | None,
    status: str = "active",
) -> TgChat:
    chat = await session.get(TgChat, chat_id)
    if chat is None:
        chat = TgChat(
            chat_id=chat_id,
            title=title or str(chat_id),
            type=chat_type,
            username=username,
            status=status,
        )
        session.add(chat)
    else:
        chat.title = title or chat.title or str(chat_id)
        chat.type = chat_type
        chat.username = username
        chat.status = status
    return chat


async def mark_chat_status(session: AsyncSession, chat_id: int, status: str) -> None:
    chat = await session.get(TgChat, chat_id)
    if chat is not None:
        chat.status = status


async def migrate_chat(
    session: AsyncSession,
    old_chat_id: int,
    new_chat_id: int,
    title: str | None,
    chat_type: str,
    username: str | None,
) -> None:
    old_chat = await session.get(TgChat, old_chat_id)
    if old_chat is not None:
        old_chat.status = "migrated"
        old_chat.migrated_to_chat_id = new_chat_id

    new_chat = await upsert_chat(session, new_chat_id, title, chat_type, username, "active")

    result = await session.execute(
        select(DeliveryGroupChat).where(DeliveryGroupChat.chat_id == old_chat_id)
    )
    old_links = result.scalars().all()
    for old_link in old_links:
        existing = await get_group_chat_link(session, old_link.delivery_group_id, new_chat_id)
        if existing is None:
            session.add(
                DeliveryGroupChat(
                    delivery_group_id=old_link.delivery_group_id,
                    chat_id=new_chat.chat_id,
                    enabled=old_link.enabled,
                )
            )
        old_link.enabled = False


async def list_chats(session: AsyncSession, status: str | None = None) -> list[TgChat]:
    stmt = select(TgChat).order_by(TgChat.status, TgChat.title, TgChat.chat_id)
    if status is not None:
        stmt = stmt.where(TgChat.status == status)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_active_chat(session: AsyncSession, chat_id: int) -> TgChat | None:
    chat = await session.get(TgChat, chat_id)
    if chat is None or chat.status != "active":
        return None
    return chat


async def get_delivery_group(session: AsyncSession, group_id: int) -> DeliveryGroup | None:
    return await session.get(DeliveryGroup, group_id)


async def find_delivery_group(session: AsyncSession, value: str) -> DeliveryGroup | None:
    value = value.strip()
    if not value:
        return None

    try:
        group_id = int(value)
    except ValueError:
        group_id = None

    if group_id is not None:
        return await get_delivery_group(session, group_id)

    result = await session.execute(
        select(DeliveryGroup).where(func.lower(DeliveryGroup.name) == value.lower())
    )
    return result.scalar_one_or_none()


async def list_delivery_groups(session: AsyncSession) -> list[DeliveryGroupSummary]:
    result = await session.execute(
        select(DeliveryGroup, func.count(DeliveryGroupChat.id))
        .outerjoin(
            DeliveryGroupChat,
            and_(
                DeliveryGroupChat.delivery_group_id == DeliveryGroup.id,
                DeliveryGroupChat.enabled.is_(True),
            ),
        )
        .group_by(DeliveryGroup.id)
        .order_by(DeliveryGroup.name)
    )
    return [DeliveryGroupSummary(group=group, chat_count=count) for group, count in result.all()]


async def list_accessible_delivery_groups(
    session: AsyncSession,
    user_id: int,
    role: str,
) -> list[DeliveryGroupSummary]:
    if role == "owner":
        return await list_delivery_groups(session)

    result = await session.execute(
        select(DeliveryGroup, func.count(DeliveryGroupChat.id))
        .join(
            OperatorGroupPermission,
            and_(
                OperatorGroupPermission.delivery_group_id == DeliveryGroup.id,
                OperatorGroupPermission.user_id == user_id,
                OperatorGroupPermission.enabled.is_(True),
            ),
        )
        .outerjoin(
            DeliveryGroupChat,
            and_(
                DeliveryGroupChat.delivery_group_id == DeliveryGroup.id,
                DeliveryGroupChat.enabled.is_(True),
            ),
        )
        .group_by(DeliveryGroup.id)
        .order_by(DeliveryGroup.name)
    )
    return [DeliveryGroupSummary(group=group, chat_count=count) for group, count in result.all()]


async def list_chats_for_accessible_groups(
    session: AsyncSession,
    user_id: int,
    role: str,
) -> list[TgChat]:
    if role == "owner":
        return await list_chats(session, status="active")

    result = await session.execute(
        select(TgChat)
        .join(DeliveryGroupChat, DeliveryGroupChat.chat_id == TgChat.chat_id)
        .join(
            OperatorGroupPermission,
            and_(
                OperatorGroupPermission.delivery_group_id == DeliveryGroupChat.delivery_group_id,
                OperatorGroupPermission.user_id == user_id,
                OperatorGroupPermission.enabled.is_(True),
            ),
        )
        .where(
            DeliveryGroupChat.enabled.is_(True),
            TgChat.status == "active",
        )
        .distinct()
        .order_by(TgChat.title, TgChat.chat_id)
    )
    return list(result.scalars().all())


async def list_direct_send_chats(
    session: AsyncSession,
    user_id: int,
    role: str,
) -> list[TgChat]:
    if role == "owner":
        return await list_chats(session, status="active")

    result = await session.execute(
        select(TgChat)
        .join(OperatorChatPermission, OperatorChatPermission.chat_id == TgChat.chat_id)
        .where(
            OperatorChatPermission.user_id == user_id,
            OperatorChatPermission.enabled.is_(True),
            TgChat.status == "active",
        )
        .order_by(TgChat.title, TgChat.chat_id)
    )
    return list(result.scalars().all())


async def has_chat_access(
    session: AsyncSession,
    user_id: int,
    role: str,
    chat_id: int,
) -> bool:
    chat = await get_active_chat(session, chat_id)
    if chat is None:
        return False
    if role == "owner":
        return True

    result = await session.execute(
        select(OperatorChatPermission.id).where(
            OperatorChatPermission.user_id == user_id,
            OperatorChatPermission.chat_id == chat_id,
            OperatorChatPermission.enabled.is_(True),
        )
    )
    return result.scalar_one_or_none() is not None


async def group_name_exists(
    session: AsyncSession,
    name: str,
    exclude_group_id: int | None = None,
) -> bool:
    stmt = select(DeliveryGroup.id).where(func.lower(DeliveryGroup.name) == name.lower())
    if exclude_group_id is not None:
        stmt = stmt.where(DeliveryGroup.id != exclude_group_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def create_delivery_group(session: AsyncSession, name: str, created_by: int) -> DeliveryGroup:
    group = DeliveryGroup(name=name, created_by=created_by)
    session.add(group)
    await session.flush()
    await add_audit_log(session, created_by, "create_group", "delivery_group", str(group.id), name)
    return group


async def rename_delivery_group(
    session: AsyncSession,
    group_id: int,
    new_name: str,
    operator_user_id: int,
) -> bool:
    group = await get_delivery_group(session, group_id)
    if group is None:
        return False
    old_name = group.name
    group.name = new_name
    await add_audit_log(
        session,
        operator_user_id,
        "rename_group",
        "delivery_group",
        str(group_id),
        f"{old_name} -> {new_name}",
    )
    return True


async def delete_delivery_group(session: AsyncSession, group_id: int, operator_user_id: int) -> bool:
    group = await get_delivery_group(session, group_id)
    if group is None:
        return False
    await session.execute(delete(OperatorGroupPermission).where(OperatorGroupPermission.delivery_group_id == group_id))
    await session.execute(delete(DeliveryGroupChat).where(DeliveryGroupChat.delivery_group_id == group_id))
    await session.delete(group)
    await add_audit_log(session, operator_user_id, "delete_group", "delivery_group", str(group_id), group.name)
    return True


async def get_group_chat_link(
    session: AsyncSession,
    group_id: int,
    chat_id: int,
) -> DeliveryGroupChat | None:
    result = await session.execute(
        select(DeliveryGroupChat).where(
            DeliveryGroupChat.delivery_group_id == group_id,
            DeliveryGroupChat.chat_id == chat_id,
        )
    )
    return result.scalar_one_or_none()


async def list_available_chats_for_group(session: AsyncSession, group_id: int) -> list[TgChat]:
    linked_chat_ids = (
        select(DeliveryGroupChat.chat_id)
        .where(
            DeliveryGroupChat.delivery_group_id == group_id,
            DeliveryGroupChat.enabled.is_(True),
        )
        .subquery()
    )
    result = await session.execute(
        select(TgChat)
        .where(
            TgChat.status == "active",
            TgChat.chat_id.not_in(select(linked_chat_ids.c.chat_id)),
        )
        .order_by(TgChat.title, TgChat.chat_id)
    )
    return list(result.scalars().all())


async def list_group_chats(session: AsyncSession, group_id: int) -> list[TgChat]:
    result = await session.execute(
        select(TgChat)
        .join(DeliveryGroupChat, DeliveryGroupChat.chat_id == TgChat.chat_id)
        .where(
            DeliveryGroupChat.delivery_group_id == group_id,
            DeliveryGroupChat.enabled.is_(True),
        )
        .order_by(TgChat.title, TgChat.chat_id)
    )
    return list(result.scalars().all())


async def count_group_chats(session: AsyncSession, group_id: int) -> int:
    result = await session.execute(
        select(func.count(DeliveryGroupChat.id)).where(
            DeliveryGroupChat.delivery_group_id == group_id,
            DeliveryGroupChat.enabled.is_(True),
        )
    )
    return int(result.scalar_one())


async def add_chat_to_group(
    session: AsyncSession,
    group_id: int,
    chat_id: int,
    operator_user_id: int,
) -> bool:
    group = await get_delivery_group(session, group_id)
    chat = await session.get(TgChat, chat_id)
    if group is None or chat is None or chat.status != "active":
        return False

    link = await get_group_chat_link(session, group_id, chat_id)
    if link is None:
        link = DeliveryGroupChat(delivery_group_id=group_id, chat_id=chat_id, enabled=True)
        session.add(link)
    else:
        link.enabled = True

    await add_audit_log(
        session,
        operator_user_id,
        "add_chat_to_group",
        "delivery_group_chat",
        f"{group_id}:{chat_id}",
        chat.title,
    )
    return True


async def remove_chat_from_group(
    session: AsyncSession,
    group_id: int,
    chat_id: int,
    operator_user_id: int,
) -> bool:
    link = await get_group_chat_link(session, group_id, chat_id)
    if link is None or not link.enabled:
        return False
    link.enabled = False
    await add_audit_log(
        session,
        operator_user_id,
        "remove_chat_from_group",
        "delivery_group_chat",
        f"{group_id}:{chat_id}",
    )
    return True


async def create_send_job(
    session: AsyncSession,
    operator_user_id: int,
    delivery_group_id: int,
    source_chat_id: int,
    source_message_id: int,
    target_chats: list[TgChat],
) -> SendJob:
    job = SendJob(
        operator_user_id=operator_user_id,
        delivery_group_id=delivery_group_id,
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
        status="pending",
    )
    session.add(job)
    await session.flush()
    for chat in target_chats:
        session.add(SendJobTarget(send_job_id=job.id, target_chat_id=chat.chat_id, status="pending"))
    await add_audit_log(
        session,
        operator_user_id,
        "create_send_job",
        "send_job",
        str(job.id),
        f"group={delivery_group_id}, targets={len(target_chats)}",
    )
    return job


async def set_send_job_status(session: AsyncSession, job_id: int, status: str) -> None:
    await session.execute(update(SendJob).where(SendJob.id == job_id).values(status=status))


async def mark_send_target(
    session: AsyncSession,
    job_id: int,
    target_chat_id: int,
    status: str,
    sent_message_id: int | None = None,
    error_message: str | None = None,
) -> None:
    await session.execute(
        update(SendJobTarget)
        .where(
            SendJobTarget.send_job_id == job_id,
            SendJobTarget.target_chat_id == target_chat_id,
        )
        .values(
            status=status,
            sent_message_id=sent_message_id,
            error_message=error_message,
        )
    )


async def finish_send_job(session: AsyncSession, job_id: int, success_count: int, failed_count: int) -> None:
    status = "done" if failed_count == 0 else "partial_failed" if success_count else "failed"
    await session.execute(
        update(SendJob)
        .where(SendJob.id == job_id)
        .values(
            status=status,
            success_count=success_count,
            failed_count=failed_count,
        )
    )


async def record_direct_send_message(
    session: AsyncSession,
    *,
    operator_user_id: int,
    target_chat_id: int,
    source_chat_id: int,
    source_message_id: int,
    sent_message_id: int,
) -> DirectSendMessage:
    record = DirectSendMessage(
        operator_user_id=operator_user_id,
        target_chat_id=target_chat_id,
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
        sent_message_id=sent_message_id,
    )
    session.add(record)
    await session.flush()
    await add_audit_log(
        session,
        operator_user_id,
        "direct_send_message",
        "direct_send_message",
        str(record.id),
        f"chat={target_chat_id}, sent={sent_message_id}",
    )
    return record


async def find_sent_message_match(
    session: AsyncSession,
    target_chat_id: int,
    sent_message_id: int,
) -> SentMessageMatch | None:
    result = await session.execute(
        select(SendJobTarget, SendJob)
        .join(SendJob, SendJob.id == SendJobTarget.send_job_id)
        .where(
            SendJobTarget.target_chat_id == target_chat_id,
            SendJobTarget.sent_message_id == sent_message_id,
            SendJobTarget.status == "sent",
        )
        .order_by(SendJobTarget.id.desc())
    )
    row = result.first()
    if row is not None:
        target, job = row
        return SentMessageMatch(
            operator_user_id=job.operator_user_id,
            target_type="send_job_target",
            target_id=target.id,
        )

    direct_result = await session.execute(
        select(DirectSendMessage)
        .where(
            DirectSendMessage.target_chat_id == target_chat_id,
            DirectSendMessage.sent_message_id == sent_message_id,
        )
        .order_by(DirectSendMessage.id.desc())
    )
    direct = direct_result.scalars().first()
    if direct is None:
        return None
    return SentMessageMatch(
        operator_user_id=direct.operator_user_id,
        target_type="direct_send_message",
        target_id=direct.id,
    )
