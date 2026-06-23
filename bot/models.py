from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class AuthorizedUser(TimestampMixin, Base):
    __tablename__ = "authorized_users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    group_permissions: Mapped[list[OperatorGroupPermission]] = relationship(back_populates="operator")


class TgChat(TimestampMixin, Base):
    __tablename__ = "tg_chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    migrated_to_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    group_links: Mapped[list[DeliveryGroupChat]] = relationship(back_populates="chat")


class DeliveryGroup(TimestampMixin, Base):
    __tablename__ = "delivery_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)

    chat_links: Mapped[list[DeliveryGroupChat]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
    )
    operator_permissions: Mapped[list[OperatorGroupPermission]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
    )


class OperatorGroupPermission(TimestampMixin, Base):
    __tablename__ = "operator_group_permissions"
    __table_args__ = (UniqueConstraint("user_id", "delivery_group_id", name="uq_operator_group_permission"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("authorized_users.user_id"), nullable=False)
    delivery_group_id: Mapped[int] = mapped_column(ForeignKey("delivery_groups.id"), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    operator: Mapped[AuthorizedUser] = relationship(back_populates="group_permissions")
    group: Mapped[DeliveryGroup] = relationship(back_populates="operator_permissions")


class OperatorFeaturePermission(TimestampMixin, Base):
    __tablename__ = "operator_feature_permissions"

    user_id: Mapped[int] = mapped_column(ForeignKey("authorized_users.user_id"), primary_key=True)
    allow_group_broadcast: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allow_direct_send: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allow_manage_operators: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    receive_sent_notifications: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    receive_reply_notifications: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class OperatorChatPermission(TimestampMixin, Base):
    __tablename__ = "operator_chat_permissions"
    __table_args__ = (UniqueConstraint("user_id", "chat_id", name="uq_operator_chat_permission"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("authorized_users.user_id"), nullable=False)
    chat_id: Mapped[int] = mapped_column(ForeignKey("tg_chats.chat_id"), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class DeliveryGroupChat(TimestampMixin, Base):
    __tablename__ = "delivery_group_chats"
    __table_args__ = (UniqueConstraint("delivery_group_id", "chat_id", name="uq_group_chat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    delivery_group_id: Mapped[int] = mapped_column(ForeignKey("delivery_groups.id"), nullable=False)
    chat_id: Mapped[int] = mapped_column(ForeignKey("tg_chats.chat_id"), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    group: Mapped[DeliveryGroup] = relationship(back_populates="chat_links")
    chat: Mapped[TgChat] = relationship(back_populates="group_links")


class SendJob(TimestampMixin, Base):
    __tablename__ = "send_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operator_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delivery_group_id: Mapped[int] = mapped_column(ForeignKey("delivery_groups.id"), nullable=False)
    source_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class SendJobTarget(TimestampMixin, Base):
    __tablename__ = "send_job_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    send_job_id: Mapped[int] = mapped_column(ForeignKey("send_jobs.id"), nullable=False)
    target_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    sent_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class DirectSendMessage(TimestampMixin, Base):
    __tablename__ = "direct_send_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operator_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    sent_message_id: Mapped[int] = mapped_column(Integer, nullable=False)


class BotSetting(TimestampMixin, Base):
    __tablename__ = "bot_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class AuditLog(TimestampMixin, Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
