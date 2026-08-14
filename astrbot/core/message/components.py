"""
MIT License

Copyright (c) 2021 Lxns-Network

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import asyncio
import base64
import json
import os
import sys
import uuid
from enum import Enum
from functools import lru_cache
from pathlib import Path, PurePosixPath

from deprecated import deprecated

if sys.version_info >= (3, 14):
    from pydantic import BaseModel
else:
    from pydantic.v1 import BaseModel

from astrbot.core import astrbot_config, file_token_service, logger
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
from astrbot.core.utils.io import download_file
from astrbot.core.utils.media_utils import MediaResolver, file_uri_to_path, is_file_uri


class ComponentType(str, Enum):
    # Basic Segment Types
    Plain = "Plain"
    Image = "Image"
    Record = "Record"
    Video = "Video"
    File = "File"

    # IM-specific Segment Types
    Face = "Face"
    At = "At"
    Node = "Node"
    Nodes = "Nodes"
    Poke = "Poke"
    Reply = "Reply"
    Forward = "Forward"
    RPS = "RPS"
    Dice = "Dice"
    Shake = "Shake"
    Share = "Share"
    Contact = "Contact"
    Location = "Location"
    Music = "Music"
    Json = "Json"
    Unknown = "Unknown"


class BaseMessageComponent(BaseModel):
    type: ComponentType

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

    def __repr_args__(self):
        """截断超长 / base64 字段值，避免 repr() 与日志输出被 base64 正文污染。"""
        max_len = 64

        def truncate(value):
            if isinstance(value, str):
                if value.startswith("base64://"):
                    return f"base64://<{len(value) - 9} chars>"
                if len(value) > max_len:
                    return f"{value[:max_len]}...<{len(value)} chars>"
            return value

        for key, value in super().__repr_args__():
            yield key, truncate(value)

    def toDict(self):
        data = {}
        for k, v in self.__dict__.items():
            if k == "type" or v is None:
                continue
            if k == "_type":
                k = "type"
            data[k] = v
        return {"type": self.type.lower(), "data": data}

    async def to_dict(self) -> dict:
        return self.toDict()


# ======================== 优化点1：提取公共路径解析函数 ========================
# 将 Record/Video 中重复的 _resolve_file_source 逻辑提取为纯函数，
# 并减少 os.path.exists 的调用次数（对每个候选只检查一次）

def _resolve_file_source_from_attrs(file_: str | None, url: str | None, path: str | None) -> str:
    """
    按优先级 (file > url > path) 解析出一个可用的文件源字符串。
    优化：优先通过前缀判断，避免对 http/base64/data 等非本地资源调用 os.path.exists。
    """
    # 按优先级顺序检查候选
    candidates = [file_, url, path]
    for candidate in candidates:
        if not candidate:
            continue
        # 快速前缀匹配 —— 这些无需文件系统访问
        if (candidate.startswith("http://") or candidate.startswith("https://") or
            candidate.startswith("base64://") or candidate.startswith("data:")):
            return candidate
        # file:// URI 需要解码再判断
        if is_file_uri(candidate):
            try:
                decoded = file_uri_to_path(candidate)
                if os.path.exists(decoded):
                    return decoded  # 返回解码后的路径（更直接）
            except OSError:
                pass
            continue
        # 普通路径
        try:
            if os.path.exists(candidate):
                return candidate
        except OSError:
            pass
    # 如果都不可用，返回第一个非空或空字符串（保持向后兼容）
    return file_ or url or path or ""


# ======================== 优化点2：缓存 file_uri_to_path 结果 ========================
# 因为同一个 URI 可能被多次解码，使用 LRU 缓存减少重复计算

@lru_cache(maxsize=128)
def _cached_file_uri_to_path(uri: str) -> str:
    """带缓存的 file URI 解码，仅用于优化重复调用。"""
    return file_uri_to_path(uri)


# ======================== 具体消息组件类 ========================

class Plain(BaseMessageComponent):
    type: ComponentType = ComponentType.Plain
    text: str

    def __init__(self, text: str, convert: bool = True, **_) -> None:
        super().__init__(text=text, convert=convert, **_)

    def toDict(self) -> dict:
        return {"type": "text", "data": {"text": self.text}}

    async def to_dict(self) -> dict:
        return {"type": "text", "data": {"text": self.text}}


class Face(BaseMessageComponent):
    type: ComponentType = ComponentType.Face
    id: int

    def __init__(self, **_) -> None:
        super().__init__(**_)


class Record(BaseMessageComponent):
    type: ComponentType = ComponentType.Record
    file: str | None = ""
    url: str | None = ""
    text: str | None = None
    path: str | None = None

    def __init__(self, file: str | None, **_) -> None:
        super().__init__(file=file, **_)

    @staticmethod
    def fromFileSystem(path, **_):
        file_path = Path(path).resolve(strict=False)
        return Record(file=file_path.as_uri(), path=str(file_path), **_)

    @staticmethod
    def fromURL(url: str, **_):
        if url.startswith("http://") or url.startswith("https://"):
            return Record(file=url, **_)
        raise Exception("not a valid url")

    @staticmethod
    def fromBase64(bs64_data: str, **_):
        return Record(file=f"base64://{bs64_data}", **_)

    # 优化：移除原有的 _resolve_file_source，直接使用公共函数
    async def _resolve_file_source(self) -> str:
        return _resolve_file_source_from_attrs(self.file, self.url, self.path)

    async def convert_to_file_path(self) -> str:
        file_source = await self._resolve_file_source()
        if not file_source:
            raise Exception(f"not a valid file: {self.file}")
        return await MediaResolver(
            file_source,
            media_type="audio",
            default_suffix=".wav",
        ).to_path(target_format="wav")

    async def convert_to_base64(self) -> str:
        file_source = await self._resolve_file_source()
        if not file_source:
            raise Exception(f"not a valid file: {self.file}")
        return await MediaResolver(
            file_source,
            media_type="audio",
            default_suffix=".wav",
        ).to_base64(target_format="wav")

    async def register_to_file_service(self) -> str:
        callback_host = astrbot_config.get("callback_api_base")
        if not callback_host:
            raise Exception("未配置 callback_api_base，文件服务不可用")
        file_path = await self.convert_to_file_path()
        token = await file_token_service.register_file(file_path)
        logger.debug(f"已注册：{callback_host}/api/file/{token}")
        return f"{callback_host}/api/file/{token}"


class Video(BaseMessageComponent):
    type: ComponentType = ComponentType.Video
    file: str
    url: str | None = ""
    cover: str | None = ""
    path: str | None = ""

    def __init__(self, file: str, **_) -> None:
        super().__init__(file=file, **_)

    @staticmethod
    def fromFileSystem(path, **_):
        file_path = Path(path).resolve(strict=False)
        return Video(file=file_path.as_uri(), path=str(file_path), **_)

    @staticmethod
    def fromURL(url: str, **_):
        if url.startswith("http://") or url.startswith("https://"):
            return Video(file=url, **_)
        raise Exception("not a valid url")

    @staticmethod
    def fromBase64(base64_data: str, **_):
        return Video(file=f"base64://{base64_data}", **_)

    async def _resolve_file_source(self) -> str:
        return _resolve_file_source_from_attrs(self.file, self.url, self.path)

    async def convert_to_file_path(self) -> str:
        file_source = await self._resolve_file_source()
        if not file_source:
            raise Exception(f"not a valid file: {self.file}")

        # 优化：提前处理 file:// 和本地路径，避免不必要的 MediaResolver 处理
        if is_file_uri(file_source):
            return _cached_file_uri_to_path(file_source)  # 使用缓存解码
        if file_source.startswith(("http://", "https://", "base64://", "data:")):
            return await MediaResolver(
                file_source,
                media_type="video",
                default_suffix=".mp4",
            ).to_path()
        # 普通路径，直接返回绝对路径（已存在性由 _resolve 保证）
        return os.path.abspath(file_source)

    async def register_to_file_service(self) -> str:
        callback_host = astrbot_config.get("callback_api_base")
        if not callback_host:
            raise Exception("未配置 callback_api_base，文件服务不可用")
        file_path = await self.convert_to_file_path()
        token = await file_token_service.register_file(file_path)
        logger.debug(f"已注册：{callback_host}/api/file/{token}")
        return f"{callback_host}/api/file/{token}"

    async def to_dict(self):
        url_or_path = self.file
        if url_or_path.startswith("http"):
            payload_file = url_or_path
        elif callback_host := astrbot_config.get("callback_api_base"):
            callback_host = str(callback_host).removesuffix("/")
            token = await file_token_service.register_file(url_or_path)
            payload_file = f"{callback_host}/api/file/{token}"
            logger.debug(f"Generated video file callback link: {payload_file}")
        else:
            payload_file = url_or_path
        return {
            "type": "video",
            "data": {"file": payload_file},
        }


class At(BaseMessageComponent):
    type: ComponentType = ComponentType.At
    qq: int | str
    name: str | None = ""

    def __init__(self, **_) -> None:
        super().__init__(**_)

    def toDict(self):
        return {
            "type": "at",
            "data": {"qq": str(self.qq)},
        }


class AtAll(At):
    qq: str = "all"

    def __init__(self, **_) -> None:
        super().__init__(**_)


class RPS(BaseMessageComponent):
    type: ComponentType = ComponentType.RPS

    def __init__(self, **_) -> None:
        super().__init__(**_)


class Dice(BaseMessageComponent):
    type: ComponentType = ComponentType.Dice

    def __init__(self, **_) -> None:
        super().__init__(**_)


class Shake(BaseMessageComponent):
    type: ComponentType = ComponentType.Shake

    def __init__(self, **_) -> None:
        super().__init__(**_)


class Share(BaseMessageComponent):
    type: ComponentType = ComponentType.Share
    url: str
    title: str
    content: str | None = ""
    image: str | None = ""

    def __init__(self, **_) -> None:
        super().__init__(**_)


class Contact(BaseMessageComponent):
    type: ComponentType = ComponentType.Contact
    _type: str
    id: int | None = 0

    def __init__(self, **_) -> None:
        super().__init__(**_)


class Location(BaseMessageComponent):
    type: ComponentType = ComponentType.Location
    lat: float
    lon: float
    title: str | None = ""
    content: str | None = ""

    def __init__(self, **_) -> None:
        super().__init__(**_)


class Music(BaseMessageComponent):
    type: ComponentType = ComponentType.Music
    _type: str
    id: int | None = 0
    url: str | None = ""
    audio: str | None = ""
    title: str | None = ""
    content: str | None = ""
    image: str | None = ""

    def __init__(self, **_) -> None:
        super().__init__(**_)


class Image(BaseMessageComponent):
    type: ComponentType = ComponentType.Image
    file: str | None = ""
    _type: str | None = ""
    url: str | None = ""
    path: str | None = ""

    def __init__(self, file: str | None, **_) -> None:
        super().__init__(file=file, **_)

    @staticmethod
    def fromURL(url: str, **_):
        if url.startswith("http://") or url.startswith("https://"):
            return Image(file=url, **_)
        raise Exception("not a valid url")

    @staticmethod
    def fromFileSystem(path, **_):
        file_path = Path(path).resolve(strict=False)
        return Image(file=file_path.as_uri(), path=str(file_path), **_)

    @staticmethod
    def fromBase64(base64: str, **_):
        return Image(f"base64://{base64}", **_)

    @staticmethod
    def fromBytes(byte: bytes):
        return Image.fromBase64(base64.b64encode(byte).decode())

    @staticmethod
    def fromIO(IO):
        return Image.fromBytes(IO.read())

    async def convert_to_file_path(self) -> str:
        # 优化：直接使用公共解析，减少重复
        url = _resolve_file_source_from_attrs(self.file, self.url, self.path)
        if not url:
            raise ValueError("No valid file or URL provided")
        return await MediaResolver(url, media_type="image").to_path()

    async def convert_to_base64(self) -> str:
        url = _resolve_file_source_from_attrs(self.file, self.url, self.path)
        if not url:
            raise ValueError("No valid file or URL provided")
        return await MediaResolver(url, media_type="image").to_base64()

    async def register_to_file_service(self) -> str:
        callback_host = astrbot_config.get("callback_api_base")
        if not callback_host:
            raise Exception("未配置 callback_api_base，文件服务不可用")
        file_path = await self.convert_to_file_path()
        token = await file_token_service.register_file(file_path)
        logger.debug(f"已注册：{callback_host}/api/file/{token}")
        return f"{callback_host}/api/file/{token}"


class Reply(BaseMessageComponent):
    type: ComponentType = ComponentType.Reply
    id: str | int
    chain: list["BaseMessageComponent"] | None = []
    sender_id: int | None | str = 0
    sender_nickname: str | None = ""
    time: int | None = 0
    message_str: str | None = ""
    text: str | None = ""
    qq: int | None = 0
    seq: int | None = 0

    def __init__(self, **_) -> None:
        super().__init__(**_)

    def toDict(self):
        return {"type": "reply", "data": {"id": str(self.id)}}


class Poke(BaseMessageComponent):
    type: ComponentType = ComponentType.Poke
    _type: str | int = "126"
    id: int | str | None = 0
    qq: int | str | None = 0

    def __init__(self, poke_type: str | int | None = None, **_) -> None:
        legacy_type = _.pop("type", None)
        if poke_type is None:
            poke_type = legacy_type
        if poke_type in (None, "", "poke", "Poke"):
            poke_type = "126"
        super().__init__(_type=str(poke_type), **_)

    def target_id(self) -> str | None:
        for value in (self.id, self.qq):
            if value is None:
                continue
            text = str(value).strip()
            if text and text != "0":
                return text
        return None

    def toDict(self):
        target_id = self.target_id()
        data = {"type": str(self._type or "126")}
        if target_id:
            data["id"] = target_id
        return {"type": "poke", "data": data}


class Forward(BaseMessageComponent):
    type: ComponentType = ComponentType.Forward
    id: str

    def __init__(self, **_) -> None:
        super().__init__(**_)


class Node(BaseMessageComponent):
    type: ComponentType = ComponentType.Node
    id: int | None = 0
    name: str | None = ""
    uin: str | None = "0"
    content: list[BaseMessageComponent] = []
    seq: str | list | None = ""
    time: int | None = 0

    def __init__(self, content: list[BaseMessageComponent], **_) -> None:
        if isinstance(content, Node):
            content = [content]
        super().__init__(content=content, **_)

    async def to_dict(self):
        data_content = []
        for comp in self.content:
            if isinstance(comp, (Image, Record)):
                bs64 = await comp.convert_to_base64()
                data_content.append({
                    "type": comp.type.lower(),
                    "data": {"file": f"base64://{bs64}"}
                })
            elif isinstance(comp, Plain):
                data_content.append(await comp.to_dict())
            elif isinstance(comp, File):
                data_content.append(await comp.to_dict())
            elif isinstance(comp, (Node, Nodes)):
                data_content.append(await comp.to_dict())
            else:
                data_content.append(comp.toDict())
        return {
            "type": "node",
            "data": {
                "user_id": str(self.uin),
                "nickname": self.name,
                "content": data_content,
            },
        }


class Nodes(BaseMessageComponent):
    type: ComponentType = ComponentType.Nodes
    nodes: list[Node]

    def __init__(self, nodes: list[Node], **_) -> None:
        super().__init__(nodes=nodes, **_)

    @deprecated(reason="Use to_dict instead.")
    def toDict(self):
        ret = {"messages": []}
        for node in self.nodes:
            ret["messages"].append(node.toDict())
        return ret

    async def to_dict(self) -> dict:
        ret = {"messages": []}
        for node in self.nodes:
            ret["messages"].append(await node.to_dict())
        return ret


class Json(BaseMessageComponent):
    type: ComponentType = ComponentType.Json
    data: dict

    def __init__(self, data: str | dict, **_) -> None:
        if isinstance(data, str):
            data = json.loads(data)
        super().__init__(data=data, **_)


class Unknown(BaseMessageComponent):
    type: ComponentType = ComponentType.Unknown
    text: str


def _sanitize_file_component_name(name: str | None) -> str:
    if not name:
        return "file"
    normalized = str(name).replace("\\", "/")
    basename = PurePosixPath(normalized).name.replace("\x00", "").strip()
    for char in ':*?"<>|':
        basename = basename.replace(char, "_")
    if basename in {"", ".", ".."}:
        return "file"
    return basename


class File(BaseMessageComponent):
    type: ComponentType = ComponentType.File
    name: str | None = ""
    file_: str | None = ""
    url: str | None = ""

    def __init__(self, name: str, file: str = "", url: str = "") -> None:
        super().__init__(name=name, file_=file, url=url)

    @property
    def file(self) -> str:
        if self.file_:
            path = _cached_file_uri_to_path(self.file_) if is_file_uri(self.file_) else self.file_
            if os.path.exists(path):
                return os.path.abspath(path)
        if self.url:
            try:
                asyncio.get_running_loop()
                logger.warning(
                    "不可以在异步上下文中同步等待下载! "
                    "请使用 await get_file() 代替直接获取 <File>.file 字段"
                )
                return ""
            except RuntimeError:
                try:
                    asyncio.run(self._download_file())
                except Exception:
                    logger.exception("文件下载失败")
                if self.file_ and os.path.exists(self.file_):
                    return os.path.abspath(self.file_)
        return ""

    @file.setter
    def file(self, value: str) -> None:
        if value.startswith(("http://", "https://")):
            self.url = value
        else:
            self.file_ = value

    async def get_file(self, allow_return_url: bool = False) -> str:
        if allow_return_url and self.url:
            return self.url
        if self.file_:
            path = _cached_file_uri_to_path(self.file_) if is_file_uri(self.file_) else self.file_
            if os.path.exists(path):
                return os.path.abspath(path)
        if self.url:
            await self._download_file()
            if self.file_:
                path = _cached_file_uri_to_path(self.file_) if is_file_uri(self.file_) else self.file_
                return os.path.abspath(path)
        return ""

    async def _download_file(self) -> None:
        if not self.url:
            raise ValueError("Download failed: No URL provided in File component.")
        download_dir = Path(get_astrbot_temp_path())
        download_dir.mkdir(parents=True, exist_ok=True)
        if self.name:
            safe_name = _sanitize_file_component_name(self.name)
            name = Path(safe_name).stem
            ext = Path(safe_name).suffix
            filename = f"fileseg_{name}_{uuid.uuid4().hex[:8]}{ext}"
        else:
            filename = f"fileseg_{uuid.uuid4().hex}"
        file_path = download_dir / filename
        await download_file(self.url, str(file_path))
        self.file_ = str(file_path.resolve())

    async def register_to_file_service(self) -> str:
        callback_host = astrbot_config.get("callback_api_base")
        if not callback_host:
            raise Exception("未配置 callback_api_base，文件服务不可用")
        file_path = await self.get_file()
        token = await file_token_service.register_file(file_path)
        logger.debug(f"已注册：{callback_host}/api/file/{token}")
        return f"{callback_host}/api/file/{token}"

    async def to_dict(self):
        url_or_path = await self.get_file(allow_return_url=True)
        if url_or_path.startswith("http"):
            payload_file = url_or_path
        elif callback_host := astrbot_config.get("callback_api_base"):
            callback_host = str(callback_host).removesuffix("/")
            token = await file_token_service.register_file(url_or_path)
            payload_file = f"{callback_host}/api/file/{token}"
            logger.debug(f"Generated file callback link: {payload_file}")
        else:
            payload_file = url_or_path
        return {
            "type": "file",
            "data": {
                "name": self.name,
                "file": payload_file,
            },
        }


ComponentTypes = {
    "plain": Plain,
    "text": Plain,
    "image": Image,
    "record": Record,
    "video": Video,
    "file": File,
    "face": Face,
    "at": At,
    "rps": RPS,
    "dice": Dice,
    "shake": Shake,
    "share": Share,
    "contact": Contact,
    "location": Location,
    "music": Music,
    "reply": Reply,
    "poke": Poke,
    "forward": Forward,
    "node": Node,
    "nodes": Nodes,
    "json": Json,
    "unknown": Unknown,
}
