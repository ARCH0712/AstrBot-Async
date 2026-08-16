import abc
import asyncio
import hashlib
import os
import re
import uuid
from collections.abc import AsyncGenerator
from time import time
from typing import Any

from deprecated import deprecated

from astrbot import logger
from astrbot.core.agent.tool import ToolSet
from astrbot.core.db.po import Conversation
from astrbot.core.message.components import (
    At,
    AtAll,
    BaseMessageComponent,
    Face,
    Forward,
    Image,
    Plain,
    Reply,
)
from astrbot.core.message.message_event_result import MessageChain, MessageEventResult
from astrbot.core.platform.message_type import MessageType
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.utils.metrics import Metric
from astrbot.core.utils.trace import TraceSpan

from .astrbot_message import AstrBotMessage, Group
from .message_session import MessageSesion, MessageSession  # noqa
from .platform_metadata import PlatformMetadata


class AstrMessageEvent(abc.ABC):
    def __init__(
        self,
        message_str: str,
        message_obj: AstrBotMessage,
        platform_meta: PlatformMetadata,
        session_id: str,
    ) -> None:
        self.message_str = message_str
        self.message_obj = message_obj
        self.platform_meta = platform_meta
        self.role = "member"
        self.is_wake = False
        self.is_at_or_wake_command = False
        self._extras: dict[str, Any] = {}
        self._force_stopped: bool = False
        message_type = getattr(message_obj, "type", None)
        if not isinstance(message_type, MessageType):
            try:
                message_type = MessageType(str(message_type))
            except (ValueError, TypeError, AttributeError):
                logger.warning(
                    f"Failed to convert message type {message_obj.type!r} to MessageType. "
                    f"Falling back to FRIEND_MESSAGE."
                )
                message_type = MessageType.FRIEND_MESSAGE
        self.session = MessageSession(
            platform_name=platform_meta.id,
            message_type=message_type,
            session_id=session_id,
        )
        self._result: MessageEventResult | None = None
        self.created_at = time()
        self.trace = TraceSpan(
            name="AstrMessageEvent",
            umo=self.unified_msg_origin,
            sender_name=self.get_sender_name(),
            message_outline=self.get_message_outline(),
        )
        self.span = self.trace
        self._has_send_oper = False
        self.call_llm = False
        self._temporary_local_files: list[str] = []
        self.plugins_name: list[str] | None = None
        self.platform = platform_meta

    @property
    def unified_msg_origin(self) -> str:
        return str(self.session)

    @unified_msg_origin.setter
    def unified_msg_origin(self, value: str) -> None:
        self.new_session = MessageSession.from_str(value)
        self.session = self.new_session

    @property
    def session_id(self) -> str:
        return self.session.session_id

    @session_id.setter
    def session_id(self, value: str) -> None:
        self.session.session_id = value

    def get_platform_name(self):
        return self.platform_meta.name

    def get_platform_id(self):
        return self.platform_meta.id

    def get_message_str(self) -> str:
        return self.message_str

    def _outline_chain(self, chain: list[BaseMessageComponent] | None) -> str:
        if not chain:
            return ""

        parts = []
        for i in chain:
            if isinstance(i, Plain):
                parts.append(i.text)
            elif isinstance(i, Image):
                parts.append("[图片]")
            elif isinstance(i, Face):
                parts.append(f"[表情:{i.id}]")
            elif isinstance(i, At):
                parts.append(f"[At:{i.qq}]")
            elif isinstance(i, AtAll):
                parts.append("[At:全体成员]")
            elif isinstance(i, Forward):
                parts.append("[转发消息]")
            elif isinstance(i, Reply):
                if i.message_str:
                    parts.append(f"[引用消息({i.sender_nickname}: {i.message_str})]")
                else:
                    parts.append("[引用消息]")
            else:
                parts.append(f"[{i.type}]")
        return " ".join(parts)

    def get_message_outline(self) -> str:
        return self._outline_chain(getattr(self.message_obj, "message", None))

    def get_messages(self) -> list[BaseMessageComponent]:
        return getattr(self.message_obj, "message", [])

    def get_message_type(self) -> MessageType:
        message_type = getattr(self.message_obj, "type", None)
        if isinstance(message_type, MessageType):
            return message_type
        return self.session.message_type

    def get_session_id(self) -> str:
        return self.session_id

    def get_group_id(self) -> str:
        return getattr(self.message_obj, "group_id", "")

    def get_self_id(self) -> str:
        return getattr(self.message_obj, "self_id", "")

    def get_sender_id(self) -> str:
        sender = getattr(self.message_obj, "sender", None)
        if sender and isinstance(getattr(sender, "user_id", None), str):
            return sender.user_id
        return ""

    def get_sender_name(self) -> str:
        sender = getattr(self.message_obj, "sender", None)
        if not sender:
            return ""
        nickname = getattr(sender, "nickname", None)
        if nickname is None:
            return ""
        if isinstance(nickname, str):
            return nickname
        return str(nickname)

    def set_extra(self, key, value) -> None:
        self._extras[key] = value

    def get_extra(self, key: str | None = None, default=None) -> Any:
        if key is None:
            return self._extras
        return self._extras.get(key, default)

    def clear_extra(self) -> None:
        logger.info(f"清除 {self.get_platform_name()} 的额外信息: {self._extras}")
        self._extras.clear()

    def track_temporary_local_file(self, path: str) -> None:
        if path and path not in self._temporary_local_files:
            self._temporary_local_files.append(path)

    def cleanup_temporary_local_files(self) -> None:
        paths = list(self._temporary_local_files)
        self._temporary_local_files.clear()
        for path in paths:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError as e:
                logger.warning(
                    "Failed to remove temporary local file %s: %s",
                    path,
                    e,
                )

    # ========== 新增：增强场景判断 ==========
    def is_private_chat(self) -> bool:
        msg_type = self.get_message_type()
        if msg_type == MessageType.FRIEND_MESSAGE:
            return True
        if msg_type == MessageType.GROUP_MESSAGE:
            return False
        # 兜底
        return not self._is_group_by_session_id()

    def _is_group_by_session_id(self) -> bool:
        session_id = self.get_session_id()
        if not session_id:
            return False
        group_keywords = (
            "GroupMessage",
            ":Group:",
            ":group:",
            "!group!",
            "Group:",
            "GROUP",
        )
        for keyword in group_keywords:
            if keyword in session_id:
                return True
        if "group" in session_id.lower():
            return True
        return False

    def is_group_chat(self) -> bool:
        msg_type = self.get_message_type()
        if msg_type == MessageType.GROUP_MESSAGE:
            return True
        if msg_type == MessageType.FRIEND_MESSAGE:
            return False
        return self._is_group_by_session_id()
    # ========================================

    def is_wake_up(self) -> bool:
        return self.is_wake

    def is_admin(self) -> bool:
        return self.role == "admin"

    async def process_buffer(self, buffer: str, pattern: re.Pattern) -> str:
        while True:
            match = re.search(pattern, buffer)
            if not match:
                break
            matched_text = match.group().strip()
            if matched_text:
                await self.send(MessageChain([Plain(matched_text)]))
                await asyncio.sleep(1.5)
            buffer = buffer[match.end():]
        return buffer

    async def send_streaming(
        self,
        generator: AsyncGenerator[MessageChain, None],
        use_fallback: bool = False,
    ) -> None:
        asyncio.create_task(
            Metric.upload(msg_event_tick=1, adapter_name=self.platform_meta.name),
        )
        self._has_send_oper = True

    async def send_typing(self) -> None:
        """默认实现为空，由具体平台重写。"""

    async def stop_typing(self) -> None:
        """默认实现为空，由具体平台重写。"""

    @deprecated(version="3.5.18", reason="No longer invoked by the message scheduler.")
    async def _pre_send(self) -> None:
        pass

    @deprecated(version="3.5.18", reason="No longer invoked by the message scheduler.")
    async def _post_send(self) -> None:
        pass

    def set_result(self, result: MessageEventResult | str) -> None:
        if isinstance(result, str):
            result = MessageEventResult().message(result)
        if isinstance(result, MessageEventResult) and result.chain is None:
            result.chain = []
        self._result = result

    def stop_event(self) -> None:
        self._force_stopped = True
        if self._result is None:
            self.set_result(MessageEventResult().stop_event())
        else:
            self._result.stop_event()

    def continue_event(self) -> None:
        self._force_stopped = False
        if self._result is None:
            self.set_result(MessageEventResult().continue_event())
        else:
            self._result.continue_event()

    def is_stopped(self) -> bool:
        if self._force_stopped:
            return True
        if self._result is None:
            return False
        return self._result.is_stopped()

    def should_call_llm(self, call_llm: bool) -> None:
        self.call_llm = call_llm

    def get_result(self) -> MessageEventResult | None:
        return self._result

    def clear_result(self) -> None:
        self._result = None

    def make_result(self) -> MessageEventResult:
        return MessageEventResult()

    def plain_result(self, text: str) -> MessageEventResult:
        return MessageEventResult().message(text)

    def image_result(self, url_or_path: str) -> MessageEventResult:
        if url_or_path.startswith("http"):
            return MessageEventResult().url_image(url_or_path)
        return MessageEventResult().file_image(url_or_path)

    def chain_result(self, chain: list[BaseMessageComponent]) -> MessageEventResult:
        mer = MessageEventResult()
        mer.chain = chain
        return mer

    def request_llm(
        self,
        prompt: str,
        func_tool_manager=None,
        tool_set: ToolSet | None = None,
        session_id: str = "",
        image_urls: list[str] | None = None,
        audio_urls: list[str] | None = None,
        contexts: list | None = None,
        system_prompt: str = "",
        conversation: Conversation | None = None,
    ) -> ProviderRequest:
        if image_urls is None:
            image_urls = []
        if audio_urls is None:
            audio_urls = []
        if contexts is None:
            contexts = []
        if len(contexts) > 0 and conversation:
            conversation = None

        return ProviderRequest(
            prompt=prompt,
            session_id=session_id,
            image_urls=image_urls,
            audio_urls=audio_urls,
            func_tool=tool_set,
            contexts=contexts,
            system_prompt=system_prompt,
            conversation=conversation,
        )

    async def send(self, message: MessageChain) -> None:
        hash_obj = hashlib.blake2b(self.get_sender_id().encode("utf-8"), digest_size=16)
        sid = str(uuid.UUID(bytes=hash_obj.digest()))
        asyncio.create_task(
            Metric.upload(
                msg_event_tick=1,
                adapter_name=self.platform_meta.name,
                sid=sid,
            ),
        )
        self._has_send_oper = True

    async def react(self, emoji: str) -> None:
        await self.send(MessageChain([Plain(emoji)]))

    async def get_group(self, group_id: str | None = None, **kwargs) -> Group | None:
        # 占位实现，具体由子类重写
        return None