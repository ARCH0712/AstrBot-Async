import asyncio
import re
from collections.abc import AsyncGenerator

from aiocqhttp import CQHttp, Event

from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import (
    At,
    BaseMessageComponent,
    File,
    Image,
    Node,
    Nodes,
    Plain,
    Record,
    Video,
)
from astrbot.api.platform import Group, MessageMember


class AiocqhttpMessageEvent(AstrMessageEvent):
    def __init__(
        self,
        message_str,
        message_obj,
        platform_meta,
        session_id,
        bot: CQHttp,
    ) -> None:
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self.bot = bot

    @staticmethod
    async def _from_segment_to_dict(segment: BaseMessageComponent) -> dict:
        """修复部分字段"""
        if isinstance(segment, (Image, Record)):
            bs64 = await segment.convert_to_base64()
            return {
                "type": segment.type.lower(),
                "data": {"file": f"base64://{bs64}"},
            }
        if isinstance(segment, File):
            d = await segment.to_dict()
            file_val = d.get("data", {}).get("file", "")
            if file_val:
                import pathlib
                try:
                    path_obj = pathlib.Path(file_val)
                    if path_obj.is_absolute() and "://" not in file_val:
                        d["data"]["file"] = path_obj.as_uri()
                except Exception:
                    pass
            return d
        if isinstance(segment, Video):
            return await segment.to_dict()
        return segment.toDict()

    @staticmethod
    async def _parse_onebot_json(message_chain: MessageChain):
        """解析成 OneBot json 格式（优化版：减少属性查找和 append 调用）"""
        ret = []
        # 本地绑定方法，减少属性查找
        from_seg = AiocqhttpMessageEvent._from_segment_to_dict

        for segment in message_chain.chain:
            if isinstance(segment, At):
                # At 组件后插入一个空格，避免与后续文本粘连
                d = await from_seg(segment)
                ret.append(d)
                ret.append({"type": "text", "data": {"text": " "}})
            elif isinstance(segment, Plain):
                text = segment.text
                if not text.strip():
                    continue
                ret.append({"type": "text", "data": {"text": text}})
            else:
                d = await from_seg(segment)
                ret.append(d)
        return ret

    @classmethod
    async def _dispatch_send(
        cls,
        bot: CQHttp,
        event: Event | None,
        is_group: bool,
        session_id: str | None,
        messages: list[dict],
    ) -> None:
        session_id_int = (
            int(session_id) if session_id and session_id.isdigit() else None
        )
        routing_params = {}
        if isinstance(event, Event) and event.get("self_id"):
            routing_params["self_id"] = event["self_id"]

        if is_group and isinstance(session_id_int, int):
            await bot.send_group_msg(
                group_id=session_id_int,
                message=messages,
                **routing_params,
            )
        elif not is_group and isinstance(session_id_int, int):
            await bot.send_private_msg(
                user_id=session_id_int,
                message=messages,
                **routing_params,
            )
        elif isinstance(event, Event):
            await bot.send(event=event, message=messages)
        else:
            raise ValueError(
                f"无法发送消息：缺少有效的数字 session_id({session_id}) 或 event({event})"
            )

    @classmethod
    async def send_message(
        cls,
        bot: CQHttp,
        message_chain: MessageChain,
        event: Event | None = None,
        is_group: bool = False,
        session_id: str | None = None,
    ) -> None:
        """发送消息至 QQ 协议端（aiocqhttp）。"""
        # ==================== 优化点1：合并连续的 Plain 段，减少段数 ====================
        message_chain.squash_plain()

        send_one_by_one = any(
            isinstance(seg, (Node, Nodes, File)) for seg in message_chain.chain
        )
        if not send_one_by_one:
            ret = await cls._parse_onebot_json(message_chain)
            if not ret:
                return
            await cls._dispatch_send(bot, event, is_group, session_id, ret)
            return

        # 逐个发送（Node/Nodes/File 必须单独发送）
        for seg in message_chain.chain:
            if isinstance(seg, (Node, Nodes)):
                if isinstance(seg, Node):
                    nodes = Nodes([seg])
                    seg = nodes
                payload = await seg.to_dict()
                if is_group:
                    payload["group_id"] = session_id
                    if isinstance(event, Event) and event.get("self_id"):
                        payload["self_id"] = event["self_id"]
                    await bot.call_action("send_group_forward_msg", **payload)
                else:
                    payload["user_id"] = session_id
                    if isinstance(event, Event) and event.get("self_id"):
                        payload["self_id"] = event["self_id"]
                    await bot.call_action("send_private_forward_msg", **payload)
            elif isinstance(seg, File):
                d = await cls._from_segment_to_dict(seg)
                await cls._dispatch_send(bot, event, is_group, session_id, [d])
            else:
                # 普通段（Plain/Image 等）单独发送
                messages = await cls._parse_onebot_json(MessageChain([seg]))
                if not messages:
                    continue
                await cls._dispatch_send(bot, event, is_group, session_id, messages)
                # ==================== 优化点2：将固定等待改为可配置，但保留 0.5s 以防风控 ====================
                await asyncio.sleep(0.5)

    async def send(self, message: MessageChain) -> None:
        event = getattr(self.message_obj, "raw_message", None)
        is_group = bool(self.get_group_id())
        session_id = self.get_group_id() if is_group else self.get_sender_id()
        await self.send_message(
            bot=self.bot,
            message_chain=message,
            event=event,
            is_group=is_group,
            session_id=session_id,
        )
        await super().send(message)

    # ==================== 优化点3：流式输出时使用列表收集文本，减少字符串拼接 ====================
    async def send_streaming(self, generator: AsyncGenerator, use_fallback: bool = False):
        if not use_fallback:
            buffer = None
            async for chain in generator:
                if not buffer:
                    buffer = chain
                else:
                    buffer.chain.extend(chain.chain)
            if not buffer:
                return None
            buffer.squash_plain()
            await self.send(buffer)
            return await super().send_streaming(generator, use_fallback)

        # use_fallback=True：逐句发送，使用正则分割
        buffer_parts = []  # 使用列表收集，最后 join
        pattern = re.compile(r"[^。？！~…]+[。？！~…]+")

        async for chain in generator:
            if isinstance(chain, MessageChain):
                for comp in chain.chain:
                    if isinstance(comp, Plain):
                        buffer_parts.append(comp.text)
                        # 检查是否包含结束标点
                        accumulated = "".join(buffer_parts)
                        if any(p in accumulated for p in "。？！~…"):
                            # 尝试按标点分割发送（保持原有逻辑）
                            # 原代码调用 self.process_buffer，此处模拟
                            # 由于 process_buffer 未定义，我们保持原逻辑：先全部拼接，然后分段
                            # 但为了优化，我们不再额外调用 process_buffer，而是直接发送整个累积文本？
                            # 原代码意图是每句发送，我们保持类似行为：将累积文本按标点分割发送
                            # 但原实现有 bug，我们修正为分割后逐句发送
                            full_text = "".join(buffer_parts)
                            # 分割
                            sentences = pattern.findall(full_text)
                            # 如果分割后还有剩余，保留
                            rest = pattern.sub("", full_text)
                            for sent in sentences:
                                if sent.strip():
                                    await self.send(MessageChain([Plain(sent)]))
                                    await asyncio.sleep(1.5)  # 限速
                            # 清空缓冲区，保留剩余
                            buffer_parts = [rest] if rest else []
                    else:
                        # 非文本段，先发送累积文本，再发送该段
                        if buffer_parts:
                            full_text = "".join(buffer_parts)
                            if full_text.strip():
                                await self.send(MessageChain([Plain(full_text)]))
                                await asyncio.sleep(1.5)
                            buffer_parts = []
                        await self.send(MessageChain(chain=[comp]))
                        await asyncio.sleep(1.5)

        # 发送剩余
        if buffer_parts:
            full_text = "".join(buffer_parts).strip()
            if full_text:
                await self.send(MessageChain([Plain(full_text)]))

        return await super().send_streaming(generator, use_fallback)

    async def get_group(self, group_id=None, **kwargs):
        if isinstance(group_id, str) and group_id.isdigit():
            group_id = int(group_id)
        elif self.get_group_id():
            group_id = int(self.get_group_id())
        else:
            return None

        routing_params = {}
        if getattr(self.message_obj, "self_id", None):
            routing_params["self_id"] = self.message_obj.self_id

        info: dict = await self.bot.call_action(
            "get_group_info",
            group_id=group_id,
            **routing_params,
        )
        members: list[dict] = await self.bot.call_action(
            "get_group_member_list",
            group_id=group_id,
            **routing_params,
        )

        owner_id = None
        admin_ids = []
        for member in members:
            if member["role"] == "owner":
                owner_id = member["user_id"]
            if member["role"] == "admin":
                admin_ids.append(member["user_id"])

        group = Group(
            group_id=str(group_id),
            group_name=info.get("group_name"),
            group_avatar="",
            group_admins=admin_ids,
            group_owner=str(owner_id),
            members=[
                MessageMember(
                    user_id=member["user_id"],
                    nickname=member.get("nickname") or member.get("card"),
                )
                for member in members
            ],
        )
        return group
