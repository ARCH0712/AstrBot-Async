import base64
import inspect
import logging
import os
import shutil
import socket
import ssl
import time
import uuid
from pathlib import Path
from typing import cast
from urllib.parse import unquote, urlparse

import aiohttp
import certifi
import psutil
from PIL import Image

from .astrbot_path import get_astrbot_temp_path

logger = logging.getLogger("astrbot")

# ======================== 优化点1：复用 SSL 上下文 ========================
# 避免每次下载都重新创建 SSL 上下文，减少开销
_SSL_CONTEXT = None
_INSECURE_SSL_CONTEXT = None


def _get_ssl_context(allow_insecure: bool = False) -> ssl.SSLContext:
    """获取 SSL 上下文（带缓存）。"""
    global _SSL_CONTEXT, _INSECURE_SSL_CONTEXT
    if allow_insecure:
        if _INSECURE_SSL_CONTEXT is None:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            _INSECURE_SSL_CONTEXT = ctx
        return _INSECURE_SSL_CONTEXT
    else:
        if _SSL_CONTEXT is None:
            _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
        return _SSL_CONTEXT


def _safe_url_for_log(url: str) -> str:
    """Return a URL summary that omits query strings and fragments.

    Args:
        url: URL that may contain signed query parameters.

    Returns:
        A short description suitable for logs.
    """
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"}:
        filename = Path(unquote(parsed.path or "")).name
        suffix = f" file={filename!r}" if filename else ""
        return f"{parsed.scheme} URL host={parsed.netloc!r}{suffix} len={len(url)}"
    return f"URL len={len(url)}"


def on_error(func, path, exc_info) -> None:
    """A callback of the rmtree function."""
    import stat

    if not os.access(path, os.W_OK):
        os.chmod(path, stat.S_IWUSR)
        func(path)
    else:
        raise exc_info[1]


def remove_dir(file_path: str) -> bool:
    if not os.path.lexists(file_path):
        return True
    if os.path.isfile(file_path) or os.path.islink(file_path):
        os.remove(file_path)
    else:
        shutil.rmtree(file_path, onerror=on_error)
    return True


# ======================== 优化点2：ensure_dir 减少不必要的检查 ========================
def ensure_dir(dir_path: str | Path) -> None:
    """确保目录存在。如果路径处存在非目录的文件或损坏的符号链接，则先将其删除。"""
    p = Path(dir_path)
    # 如果已存在且是目录，直接返回（最快路径）
    if p.is_dir():
        return
    # 如果存在但不是目录，则尝试删除
    if p.exists() or p.is_symlink():
        logger.warning(
            f"Path {p} exists but is not a directory; removing it before creating "
            "the directory."
        )
        try:
            if p.is_dir():
                shutil.rmtree(p, onerror=on_error)
            else:
                p.unlink()
        except Exception as e:
            logger.error(f"Failed to remove conflicting path {p}: {e!s}")
            raise RuntimeError(f"Could not remove conflicting path {p}: {e!s}") from e

    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"Failed to create directory {p}: {e!s}")
        raise RuntimeError(f"Could not create directory {p}: {e!s}") from e


def port_checker(port: int, host: str = "localhost") -> bool:
    sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sk.settimeout(1)
    try:
        sk.connect((host, port))
        sk.close()
        return True
    except Exception:
        sk.close()
        return False


# ======================== 优化点3：save_temp_img 简化类型处理 ========================
def save_temp_img(img: Image.Image | bytes) -> str:
    temp_dir = get_astrbot_temp_path()
    timestamp = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
    p = os.path.join(temp_dir, f"io_temp_img_{timestamp}.jpg")

    if isinstance(img, Image.Image):
        img.save(p)
    else:
        with open(p, "wb") as f:
            f.write(img)
    return p


# ======================== 优化点4：复用 download_file 逻辑，减少重复代码 ========================
async def download_image_by_url(
    url: str,
    post: bool = False,
    post_data: dict | None = None,
    path: str | None = None,
) -> str:
    """下载图片, 返回 path"""
    # 如果未指定 path，则生成临时路径
    if path is None:
        temp_dir = get_astrbot_temp_path()
        timestamp = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
        path = os.path.join(temp_dir, f"io_temp_img_{timestamp}.jpg")

    # 使用 download_file 的异步下载功能（复用其 SSL 处理和进度回调）
    # 但 download_file 不直接支持 POST，这里需要特殊处理
    if post and post_data:
        # 使用 POST 方式下载
        ssl_context = _get_ssl_context(allow_insecure=False)
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(trust_env=True, connector=connector) as session:
            try:
                async with session.post(url, json=post_data, timeout=1800) as resp:
                    resp.raise_for_status()
                    with open(path, "wb") as f:
                        f.write(await resp.read())
            except (aiohttp.ClientConnectorSSLError, aiohttp.ClientConnectorCertificateError):
                # 降级到不安全 SSL
                logger.warning(
                    f"SSL certificate verification failed for {_safe_url_for_log(url)}. "
                    "Falling back to unverified connection for POST."
                )
                insecure_ctx = _get_ssl_context(allow_insecure=True)
                async with aiohttp.ClientSession() as session2:
                    async with session2.post(url, json=post_data, ssl=insecure_ctx, timeout=1800) as resp2:
                        resp2.raise_for_status()
                        with open(path, "wb") as f:
                            f.write(await resp2.read())
    else:
        # GET 方式：直接使用 download_file（它会处理 SSL fallback）
        await download_file(url, path, show_progress=False, allow_insecure_ssl_fallback=True)

    return path


# ======================== 优化点5：下载进度回调合并，减少 emit 次数 ========================
async def _emit_download_progress(progress_callback, payload: dict) -> None:
    if not progress_callback:
        return
    result = progress_callback(payload)
    if inspect.isawaitable(result):
        await result


class DownloadFileHTTPError(RuntimeError):
    """Raised when a file download returns an unsuccessful HTTP status."""


def _raise_for_download_status(resp, url: str) -> None:
    if resp.status == 200:
        return
    logger.error(
        "Failed to download file from %s. HTTP status code: %s",
        _safe_url_for_log(url),
        resp.status,
    )
    raise DownloadFileHTTPError(
        "Failed to download file from "
        f"{_safe_url_for_log(url)}. HTTP status code: {resp.status}"
    )


# ======================== 优化点6：分块写入优化，增加缓冲 ========================
async def _download_response_to_file(
    resp,
    file_obj,
    url: str,
    show_progress: bool,
    progress_callback,
    show_downloading_label: bool = True,
) -> None:
    """Write a successful download response to a local file with buffered writes."""
    total_size = int(resp.headers.get("content-length", 0))
    downloaded_size = 0
    start_time = time.time()
    if show_progress:
        if show_downloading_label:
            print(
                f"Downloading: {_safe_url_for_log(url)} | "
                f"Size: {total_size / 1024:.2f} KB"
            )
        else:
            print(f"Size: {total_size / 1024:.2f} KB | URL: {_safe_url_for_log(url)}")

    # 初始进度
    await _emit_download_progress(
        progress_callback,
        {
            "url": url,
            "downloaded": 0,
            "total": total_size,
            "percent": 0,
            "speed": 0,
        },
    )

    # 使用 64KB 缓冲区，减少系统调用次数
    buffer_size = 64 * 1024  # 64KB
    buffer = bytearray(buffer_size)
    buffer_pos = 0

    try:
        async for chunk in resp.content.iter_chunked(8192):
            # 将 chunk 加入缓冲区
            chunk_len = len(chunk)
            if chunk_len >= buffer_size - buffer_pos:
                # 缓冲区满，写入
                remaining = chunk_len
                offset = 0
                while remaining > 0:
                    space = buffer_size - buffer_pos
                    copy_len = min(space, remaining)
                    buffer[buffer_pos:buffer_pos + copy_len] = chunk[offset:offset + copy_len]
                    buffer_pos += copy_len
                    remaining -= copy_len
                    offset += copy_len
                    if buffer_pos == buffer_size:
                        file_obj.write(buffer)
                        buffer_pos = 0
                # 如果 chunk 恰好填满，继续循环
            else:
                buffer[buffer_pos:buffer_pos + chunk_len] = chunk
                buffer_pos += chunk_len

            downloaded_size += chunk_len
            elapsed_time = time.time() - start_time
            if elapsed_time < 0.001:
                elapsed_time = 0.001
            speed = downloaded_size / 1024 / elapsed_time  # KB/s
            percent = downloaded_size / total_size if total_size > 0 else 0

            # 每 4% 或 10% 触发一次进度回调，避免过多回调开销
            if int(percent * 100) % 4 == 0 or percent >= 1:
                await _emit_download_progress(
                    progress_callback,
                    {
                        "url": url,
                        "downloaded": downloaded_size,
                        "total": total_size,
                        "percent": percent,
                        "speed": speed,
                    },
                )

            if show_progress:
                print(
                    f"\rProgress: {percent:.2%} Speed: {speed:.2f} KB/s",
                    end="",
                )

        # 写入剩余缓冲区
        if buffer_pos > 0:
            file_obj.write(buffer[:buffer_pos])

    finally:
        # 最终进度事件
        await _emit_download_progress(
            progress_callback,
            {
                "url": url,
                "downloaded": downloaded_size,
                "total": total_size,
                "percent": 1,
                "speed": 0,
            },
        )
        if show_progress:
            print()


# ======================== 优化点7：download_file 复用 SSL 上下文，减少重复创建 ========================
async def download_file(
    url: str,
    path: str,
    show_progress: bool = False,
    progress_callback=None,
    allow_insecure_ssl_fallback: bool = True,
) -> None:
    """Download a remote file to a local path."""
    try:
        ssl_context = _get_ssl_context(allow_insecure=False)
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(trust_env=True, connector=connector) as session:
            async with session.get(url, timeout=1800) as resp:
                _raise_for_download_status(resp, url)
                with open(path, "wb") as f:
                    await _download_response_to_file(
                        resp,
                        f,
                        url,
                        show_progress,
                        progress_callback,
                    )
    except (aiohttp.ClientConnectorSSLError, aiohttp.ClientConnectorCertificateError):
        if not allow_insecure_ssl_fallback:
            raise
        logger.warning(
            f"SSL certificate verification failed for {_safe_url_for_log(url)}. "
            "Falling back to unverified connection (CERT_NONE). "
            "This is insecure and exposes the application to man-in-the-middle attacks."
        )
        insecure_ctx = _get_ssl_context(allow_insecure=True)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, ssl=insecure_ctx, timeout=120) as resp:
                _raise_for_download_status(resp, url)
                with open(path, "wb") as f:
                    await _download_response_to_file(
                        resp,
                        f,
                        url,
                        show_progress,
                        progress_callback,
                        show_downloading_label=False,
                    )
    if show_progress:
        print()


def file_to_base64(file_path: str) -> str:
    with open(file_path, "rb") as f:
        data_bytes = f.read()
        base64_str = base64.b64encode(data_bytes).decode()
    return "base64://" + base64_str


def get_local_ip_addresses():
    net_interfaces = psutil.net_if_addrs()
    network_ips = []

    for interface, addrs in net_interfaces.items():
        for addr in addrs:
            if addr.family == socket.AF_INET:
                network_ips.append(addr.address)

    return network_ips
