import enum
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from typing_extensions import deprecated

from astrbot.core.message.components import (
    At,
    AtAll,
    BaseMessageComponent,
    Image,
    Json,
    Plain,
)


@dataclass
class MessageChain:
    """MessageChain 描述了一整条消息中带有的所有组件。
    现代消息平台的一条富文本消息中可能由多个组件构成，如文本、图片、At 等，并且保留了顺序。

    Attributes:
        `chain` (list): 用于顺序存储各个组件。
        `use_t2i_` (bool): 用于标记是否使用文本转图片服务。默认为 None，即跟随用户的设置。当设置为 True 时，将会使用文本转图片服务。
    """

    chain: list[BaseMessageComponent] = field(default_factory=list)
    use_t2i_: bool | None = None  # None 为跟随用户设置
    use_markdown_: bool | None = (
        None  # 是否使用 Markdown 发送消息。None 跟随平台默认，True 强制 Markdown，False 强制纯文本。
    )
    type: str | None = None
    """消息链承载的消息的类型。可选，用于让消息平台区分不同业务场景的消息链。"""

    def derive(self, chain: list[BaseMessageComponent] | None = None) -> "MessageChain":
        """基于当前消息链创建一个新的 MessageChain，继承元数据（use_t2i_、use_markdown_ 等）。

        Args:
            chain: 新消息链的组件列表。如果为 None，则使用空列表。
        """
        new = MessageChain(chain=chain if chain is not None else [])
        new.use_t2i_ = self.use_t2i_
        new.use_markdown_ = self.use_markdown_
        new.type = self.type
        return new

    def message(self, message: str):
        """添加一条文本消息到消息链 `chain` 中。

        Example:
            CommandResult().message("Hello ").message("world!")
            # 输出 Hello world!
        """
        self.chain.append(Plain(message))
        return self

    def at(self, name: str, qq: str | int):
        """添加一条 At 消息到消息链 `chain` 中。

        Example:
            CommandResult().at("张三", "12345678910")
            # 输出 @张三
        """
        self.chain.append(At(name=name, qq=qq))
        return self

    def at_all(self):
        """添加一条 AtAll 消息到消息链 `chain` 中。

        Example:
            CommandResult().at_all()
            # 输出 @所有人
        """
        self.chain.append(AtAll())
        return self

    @deprecated("请使用 message 方法代替。")
    def error(self, message: str):
        """添加一条错误消息到消息链 `chain` 中

        Example:
            CommandResult().error("解析失败")
        """
        self.chain.append(Plain(message))
        return self

    def url_image(self, url: str):
        """添加一条图片消息（https 链接）到消息链 `chain` 中。

        Note:
            如果需要发送本地图片，请使用 `file_image` 方法。

        Example:
            CommandResult().image("https://example.com/image.jpg")
        """
        self.chain.append(Image.fromURL(url))
        return self

    def file_image(self, path: str):
        """添加一条图片消息（本地文件路径）到消息链 `chain` 中。

        Note:
            如果需要发送网络图片，请使用 `url_image` 方法。

        CommandResult().image("image.jpg")
        """
        self.chain.append(Image.fromFileSystem(path))
        return self

    def base64_image(self, base64_str: str):
        """添加一条图片消息（base64 编码字符串）到消息链 `chain` 中。
        Example:
            CommandResult().base64_image("iVBORw0KGgoAAAANSUhEUgAAAAUA...")
        """
        self.chain.append(Image.fromBase64(base64_str))
        return self

    def use_t2i(self, use_t2i: bool):
        """设置是否使用文本转图片服务。

        Args:
            use_t2i (bool): 是否使用文本转图片服务。默认为 None，即跟随用户的设置。当设置为 True 时，将会使用文本转图片服务。
        """
        self.use_t2i_ = use_t2i
        return self

    def use_markdown(self, use: bool | None = True):
        """设置是否使用 Markdown 发送消息。

        仅对支持 Markdown 的平台生效（如 QQ Official），不支持的平台会忽略此字段。

        Args:
            use: True 强制使用 Markdown，False 强制纯文本，None 跟随平台默认行为。
        """
        self.use_markdown_ = use
        return self

    # ========== 优化点1：get_plain_text 使用生成器表达式减少临时列表 ==========
    def get_plain_text(self, with_other_comps_mark: bool = False) -> str:
        """获取纯文本消息。这个方法将获取 chain 中所有 Plain 组件的文本并拼接成一条消息。空格分隔。

        Args:
            with_other_comps_mark (bool): 是否在纯文本中标记其他组件的位置
        """
        if not with_other_comps_mark:
            # 使用生成器表达式代替列表推导，节省临时列表内存
            return " ".join(comp.text for comp in self.chain if isinstance(comp, Plain))
        else:
            # 同样使用生成器，因为 join 接受任何可迭代对象
            return " ".join(
                comp.text if isinstance(comp, Plain)
                else (f"{comp.data}" if isinstance(comp, Json) else f"[{comp.__class__.__name__}]")
                for comp in self.chain
            )

    # ========== 优化点2：squash_plain 尽早返回并避免冗余列表操作 ==========
    def squash_plain(self):
        """将消息链中的所有 Plain 消息段聚合到第一个 Plain 消息段中。"""
        if not self.chain:
            return self  # 提前返回，避免后续遍历

        new_chain = []
        first_plain = None
        plain_texts = []

        for comp in self.chain:
            if isinstance(comp, Plain):
                if first_plain is None:
                    first_plain = comp
                    new_chain.append(comp)  # 保留第一个 Plain
                # 其他 Plain 不加入新链，只收集文本
                plain_texts.append(comp.text)
            else:
                new_chain.append(comp)

        # 如果有 Plain 组件，则合并所有文本到第一个
        if first_plain is not None:
            first_plain.text = "".join(plain_texts)  # 直接拼接，不加分隔符（保持原逻辑）

        self.chain = new_chain
        return self


class EventResultType(enum.Enum):
    """用于描述事件处理的结果类型。

    Attributes:
        CONTINUE: 事件将会继续传播
        STOP: 事件将会终止传播
    """
    CONTINUE = enum.auto()
    STOP = enum.auto()


class ResultContentType(enum.Enum):
    """用于描述事件结果的内容的类型。"""
    LLM_RESULT = enum.auto()
    """调用 LLM 产生的结果"""
    AGENT_RUNNER_ERROR = enum.auto()
    """第三方 Agent Runner 返回的错误结果"""
    GENERAL_RESULT = enum.auto()
    """普通的消息结果"""
    STREAMING_RESULT = enum.auto()
    """调用 LLM 产生的流式结果"""
    STREAMING_FINISH = enum.auto()
    """流式输出完成"""


@dataclass
class MessageEventResult(MessageChain):
    """MessageEventResult 描述了一整条消息中带有的所有组件以及事件处理的结果。
    现代消息平台的一条富文本消息中可能由多个组件构成，如文本、图片、At 等，并且保留了顺序。

    Attributes:
        `chain` (list): 用于顺序存储各个组件。
        `use_t2i_` (bool): 用于标记是否使用文本转图片服务。默认为 None，即跟随用户的设置。当设置为 True 时，将会使用文本转图片服务。
        `result_type` (EventResultType): 事件处理的结果类型。
    """
    result_type: EventResultType | None = field(
        default_factory=lambda: EventResultType.CONTINUE,
    )
    result_content_type: ResultContentType | None = field(
        default_factory=lambda: ResultContentType.GENERAL_RESULT,
    )
    async_stream: AsyncGenerator | None = None
    """异步流"""

    def stop_event(self) -> "MessageEventResult":
        """终止事件传播。"""
        self.result_type = EventResultType.STOP
        return self

    def continue_event(self) -> "MessageEventResult":
        """继续事件传播。"""
        self.result_type = EventResultType.CONTINUE
        return self

    def is_stopped(self) -> bool:
        """是否终止事件传播。"""
        return self.result_type == EventResultType.STOP

    def set_async_stream(self, stream: AsyncGenerator) -> "MessageEventResult":
        """设置异步流。"""
        self.async_stream = stream
        return self

    def set_result_content_type(self, typ: ResultContentType) -> "MessageEventResult":
        """设置事件处理的结果类型。

        Args:
            result_type (EventResultType): 事件处理的结果类型。
        """
        self.result_content_type = typ
        return self

    def is_llm_result(self) -> bool:
        """是否为 LLM 结果。"""
        return self.result_content_type == ResultContentType.LLM_RESULT

    def is_model_result(self) -> bool:
        """Whether result comes from model execution (including runner errors)."""
        return self.result_content_type in (
            ResultContentType.LLM_RESULT,
            ResultContentType.AGENT_RUNNER_ERROR,
        )


# 为了兼容旧版代码，保留 CommandResult 的别名
CommandResult = MessageEventResult
