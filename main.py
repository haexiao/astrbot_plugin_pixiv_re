"""
Pixiv图床下载插件

先调用图床接口(pixiv.cat 的 generate 接口)获取作品信息与原图地址，
再从反代域名(i.pixiv.re 等)下载图片。
单图直发图片；多图可逐张发送或打包 PDF/ZIP；指令指定 pdf/zip 时强制打包。
"""
import asyncio
import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools, register

DEFAULT_API_ENDPOINT = "https://api.pixiv.cat/v1/generate"
DEFAULT_IMAGE_PROXY = "https://i.pixiv.re"
INFO_CACHE_MAX_ENTRIES = 100
TEMP_SEND_MAX_AGE_SECONDS = 600


class PixivApiError(Exception):
    """图床接口故障（HTTP 非 200、返回非 JSON、网络失败），与作品ID无关。"""


class IllustNotFoundError(Exception):
    """作品不存在或作品ID无效。"""


class PageDownloadError(Exception):
    """某一页图片下载失败。"""


class PackDependencyError(Exception):
    """打包所需依赖缺失（如 img2pdf）。"""


@register(
    "astrbot_plugin_pixiv_re",
    "haexiao",
    "从Pixiv第三方图床下载作品，单图直发JPG，多图可逐张发送或打包PDF/ZIP发送",
    "1.2.0",
    "https://github.com/haexiao/astrbot_plugin_pixiv_re",
)
class PixivRePlugin(Star):
    """Pixiv图床下载插件"""

    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config
        self.client: httpx.AsyncClient | None = None
        # 获取插件数据目录（位于 data/plugin_data/ 下，重载插件不会丢失）
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_pixiv_re")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # 作品信息缓存：illust_id -> (缓存时间, 作品信息)
        self._info_cache: dict[str, tuple[float, dict]] = {}
        logger.info(f"Pixiv图床下载插件已初始化，数据目录: {self.data_dir}")

    async def initialize(self):
        """初始化：创建复用的 httpx 异步客户端（含代理设置）"""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }
        client_kwargs = {
            "timeout": httpx.Timeout(30.0),
            "headers": headers,
            "follow_redirects": True,
            # 不读取 HTTP_PROXY/HTTPS_PROXY 环境变量，避免“没开代理却走了系统代理”
            "trust_env": False,
        }

        proxy = self._get_proxy_url()
        if proxy:
            client_kwargs["proxy"] = proxy
            logger.info(f"Pixiv图床下载插件已启用代理：{proxy}")

        self.client = httpx.AsyncClient(**client_kwargs)
        logger.info("Pixiv图床下载插件初始化完成")

    async def terminate(self):
        """销毁：清理 httpx 客户端"""
        if self.client and not self.client.is_closed:
            try:
                await self.client.aclose()
                logger.info("已关闭 httpx 异步客户端")
            except Exception as e:
                logger.error(f"关闭客户端失败：{str(e)}")

    # ==================== 配置读取 ====================

    def _get_proxy_url(self) -> str | None:
        """读取代理配置；未启用或地址为空时返回 None"""
        if not self.config.get("use_proxy", False):
            return None
        proxy_url = str(self.config.get("proxy_url", "") or "").strip()
        if not proxy_url:
            logger.warning("配置开启了代理（use_proxy）但 proxy_url 为空，本次不使用代理")
            return None
        return proxy_url

    def _get_int_config(self, key: str, default: int) -> int:
        """读取整数配置，非法值回退到默认值"""
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError):
            logger.warning(f"配置项 {key} 不是整数，已按默认值 {default} 处理")
            return default

    def _get_api_endpoint(self) -> str:
        return (
            str(self.config.get("api_endpoint", DEFAULT_API_ENDPOINT) or DEFAULT_API_ENDPOINT)
            .strip()
            or DEFAULT_API_ENDPOINT
        )

    def _get_image_proxy(self) -> str:
        return (
            str(self.config.get("image_proxy", DEFAULT_IMAGE_PROXY) or DEFAULT_IMAGE_PROXY)
            .strip()
            .rstrip("/")
            or DEFAULT_IMAGE_PROXY
        )

    @staticmethod
    def _normalize_pack_format(value) -> str:
        """校验打包格式，非法值回退为 pdf（避免出现 .rar 之类莫名其妙的文件名）"""
        fmt = str(value or "").strip().lower()
        if fmt in ("pdf", "zip"):
            return fmt
        if fmt:
            logger.warning(f"打包格式 {fmt!r} 不受支持，已回退为 pdf")
        return "pdf"

    @staticmethod
    def _page_ext(raw_url: str) -> str:
        """从原图地址推断扩展名（图床会返回 png/webp 等，不能一律写 .jpg）"""
        suffix = Path(urlsplit(raw_url).path).suffix.lower()
        return suffix if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"} else ".jpg"

    @staticmethod
    def _track_temp_file(event: AstrMessageEvent, path: Path) -> None:
        """把待发送的临时文件登记给 AstrBot（事件处理结束时自动删除）；旧版本无此方法则跳过"""
        tracker = getattr(event, "track_temporary_local_file", None)
        if callable(tracker):
            try:
                tracker(str(path))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"登记临时文件失败（将依赖过期清理兜底）：{e}")

    # ==================== 核心功能 ====================

    async def _fetch_illust_info(self, illust_id: str) -> dict:
        """
        调用图床接口获取作品信息。

        返回：{"id": str, "title": str, "artist": str, "pages": [原图地址, ...]}
        失败时抛 PixivApiError（图床故障）或 IllustNotFoundError（作品ID无效）。
        """
        ttl = self._get_int_config("info_cache_ttl", 600)
        cached = self._info_cache.get(illust_id)
        if cached and ttl > 0 and time.time() - cached[0] < ttl:
            logger.info(f"命中作品信息缓存：{illust_id}")
            return cached[1]

        api_endpoint = self._get_api_endpoint()
        payload = None
        error_detail = ""

        # 接口域名偶发解析失败，失败后重试一次
        for attempt in (1, 2):
            try:
                resp = await self.client.post(
                    api_endpoint,
                    data={"p": illust_id},
                    timeout=httpx.Timeout(15.0),
                )
                if resp.status_code != 200:
                    error_detail = f"HTTP {resp.status_code}"
                else:
                    payload = resp.json()
            except ValueError:
                error_detail = "返回内容不是 JSON"
            except httpx.HTTPError as e:
                error_detail = f"{type(e).__name__}: {e}"

            if payload is not None:
                break
            if attempt == 1:
                logger.warning(f"获取作品信息失败（{error_detail}），1 秒后重试")
                await asyncio.sleep(1.0)

        if payload is None:
            raise PixivApiError(f"图床接口故障（{error_detail}）")

        if not payload.get("success"):
            raise IllustNotFoundError(str(payload.get("error") or "接口未返回作品信息"))

        multiple = bool(payload.get("multiple"))
        raw_urls = (
            payload.get("original_urls_proxy") if multiple else [payload.get("original_url_proxy")]
        )
        pages = [u for u in (raw_urls or []) if u]
        if not pages:
            raise PixivApiError("图床接口未返回图片地址")

        info = {
            "id": str(payload.get("id") or illust_id),
            "title": str(payload.get("title") or ""),
            "artist": str((payload.get("artist") or {}).get("name") or ""),
            "pages": pages,
        }

        if ttl > 0:
            if len(self._info_cache) >= INFO_CACHE_MAX_ENTRIES:
                self._info_cache.clear()
            self._info_cache[illust_id] = (time.time(), info)

        logger.info(f"作品 {illust_id}：共 {len(pages)} 页，标题「{info['title']}」")
        return info

    def _to_proxy_url(self, raw_url: str) -> str:
        """把接口返回的图片地址换成配置的下载域名（保留路径与查询串）"""
        parts = urlsplit(raw_url)
        query = f"?{parts.query}" if parts.query else ""
        return f"{self._get_image_proxy()}{parts.path}{query}"

    async def _fetch_image(
        self, url: str, page_no: int, page_total: int, attempts: int = 3
    ) -> bytes:
        """下载单页图片；传输中断会自动重试，失败时抛 PageDownloadError"""
        last_error = ""
        for attempt in range(1, attempts + 1):
            try:
                resp = await self.client.get(url)
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                # 429(限速) 与 5xx(服务端临时故障) 值得重试；其他 4xx 直接放弃
                if status == 429 or status >= 500:
                    last_error = f"HTTP {status}"
                    if attempt < attempts:
                        logger.warning(
                            f"第 {page_no}/{page_total} 页返回 HTTP {status}，"
                            f"第 {attempt + 1} 次重试"
                        )
                        await asyncio.sleep(float(attempt))
                        continue
                    break
                raise PageDownloadError(
                    f"第 {page_no}/{page_total} 页下载失败（HTTP {status}），"
                    "请稍后重试或切换下载域名"
                ) from e
            except httpx.HTTPError as e:
                # 连接中断/超时等：可能是大图传输被打断，重试
                last_error = f"{type(e).__name__}: {e}"
                if attempt < attempts:
                    logger.warning(
                        f"第 {page_no}/{page_total} 页下载中断（{last_error}），"
                        f"第 {attempt + 1} 次重试"
                    )
                    await asyncio.sleep(float(attempt))
                    continue
            else:
                content_type = resp.headers.get("Content-Type", "")
                if "image" not in content_type.lower():
                    raise PageDownloadError(
                        f"第 {page_no}/{page_total} 页返回的不是图片"
                        f"（{content_type or '未知类型'}），图床可能故障"
                    )
                return resp.content

        raise PageDownloadError(
            f"第 {page_no}/{page_total} 页下载失败（{last_error}），已重试 {attempts} 次"
        )

    def _pack_pdf(self, image_paths: list[Path], output_path: Path) -> Path:
        """将图片列表打包为 PDF"""
        try:
            import img2pdf
        except ImportError as e:
            raise PackDependencyError(
                "PDF 打包依赖 img2pdf 未安装，请改用 zip 或安装该依赖"
            ) from e

        with open(output_path, "wb") as f:
            f.write(img2pdf.convert([str(p) for p in image_paths]))
        logger.info(f"PDF 打包完成：{output_path}")
        return output_path

    def _pack_zip(self, image_paths: list[Path], output_path: Path) -> Path:
        """将图片列表打包为 ZIP"""
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for img_path in image_paths:
                zf.write(img_path, img_path.name)
        logger.info(f"ZIP 打包完成：{output_path}")
        return output_path

    # ==================== 本地缓存复用 ====================

    IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")

    def _find_local_pages(
        self, album_dir: Path, illust_id: str, page_count: int
    ) -> list[Path] | None:
        """
        在归档目录里找齐整部作品的本地文件（命中即不再联网下载）。

        - 单图按「作品ID + 任意图片扩展名」匹配，多图按「页码 + 任意图片扩展名」匹配
          （兼容旧版本一律存成 .jpg 的历史文件）
        - 必须每一页都有、且文件非空；找不齐返回 None（调用方老老实实重新下载）
        - 只读，不改动用户归档目录
        """
        if not album_dir.is_dir():
            return None

        def pick(pattern: str) -> Path | None:
            for path in sorted(album_dir.glob(pattern)):
                try:
                    if path.is_file() and path.suffix.lower() in self.IMAGE_EXTS and path.stat().st_size > 0:
                        return path
                except OSError:
                    continue
            return None

        if page_count == 1:
            hit = pick(f"{illust_id}.*")
            return [hit] if hit else None

        found: list[Path] = []
        for i in range(1, page_count + 1):
            hit = pick(f"{i}.*")
            if hit is None:
                return None
            found.append(hit)
        return found

    # ==================== 待发送图片的临时目录 ====================

    def _new_temp_dir(self, illust_id: str) -> Path:
        """创建本次发送用的临时目录，并清理上次遗留的过期目录"""
        root = self.data_dir / "temp_send"
        root.mkdir(parents=True, exist_ok=True)
        self._cleanup_stale_temp(root)
        temp_dir = root / f"{illust_id}_{int(time.time())}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        return temp_dir

    @staticmethod
    def _cleanup_stale_temp(root: Path, max_age_seconds: int = TEMP_SEND_MAX_AGE_SECONDS) -> None:
        """清理过期临时图片（发送后立刻删除可能影响平台读取，故延迟到下次调用时清理）"""
        now = time.time()
        try:
            entries = list(root.iterdir())
        except OSError:
            return
        for path in entries:
            try:
                if now - path.stat().st_mtime <= max_age_seconds:
                    continue
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
            except OSError:
                continue

    # ==================== 主指令 ====================

    @filter.command("pixiv")
    async def pixiv_download(self, event: AstrMessageEvent):
        """
        从 Pixiv 图床下载作品。

        用法: /pixiv <作品ID> [打包格式]
        """
        message_str = event.message_str.strip()
        args = message_str.split()

        # ---- 参数校验 ----
        if len(args) < 2:
            yield event.plain_result(
                "请按格式使用：\n"
                "/pixiv <作品ID> [打包格式]\n"
                "示例：/pixiv 118908797\n"
                "示例：/pixiv 118908797 zip\n"
                "打包格式可选：pdf、zip（指定后强制打包，单图也会打包）\n"
                "💡 不指定时：单图直发；多图低于免打包数量逐张发，超过则自动打包"
            )
            return

        illust_id = args[1]
        if not illust_id.isdigit():
            yield event.plain_result("❌ 作品ID必须是数字")
            return

        # 判断用户是否在指令中指定了打包格式
        has_pack_arg = len(args) >= 3 and args[2].lower() in ("pdf", "zip")

        # 读取打包格式（仅在打包时使用），非法值回退 pdf
        pack_format = self._normalize_pack_format(
            args[2] if has_pack_arg else self.config.get("pack_format", "pdf")
        )

        max_free = self._get_int_config("max_free_pack_count", 10)
        max_pages = self._get_int_config("max_pages", 200)
        save_local = self.config.get("save_local", False)
        # 本地已有整部作品时直接复用（仅在开启本地保存时才有意义）
        reuse_local = bool(save_local) and bool(self.config.get("reuse_local_files", True))

        try:
            # ---- 第1步：向图床接口获取作品信息（页数与原图地址）----
            info = await self._fetch_illust_info(illust_id)
            pages = info["pages"]
            page_count = len(pages)

            if max_pages > 0 and page_count > max_pages:
                yield event.plain_result(
                    f"⚠️ 该作品共 {page_count} 页，超过配置上限 {max_pages} 页，已取消下载\n"
                    "💡 可在插件配置中调整 max_pages"
                )
                return

            # 是否需要打包：指令显式指定格式（单图也打包），或多图超过免打包数量
            need_pack = has_pack_arg or (page_count > 1 and page_count > max_free)
            format_text = pack_format.upper() if need_pack else "JPG"

            # ---- 本地缓存复用：归档目录里已存齐就不联网 ----
            album_dir: Path | None = None
            local_pages: list[Path] | None = None
            if save_local:
                # 这里只算路径不建目录；真正写入时各分支会按需创建
                album_dir = self._get_save_dir() / illust_id
                if reuse_local:
                    local_pages = self._find_local_pages(album_dir, illust_id, page_count)
                    if local_pages:
                        logger.info(f"复用本地已保存的 {page_count} 个文件（跳过下载）：{album_dir}")

            # 复用与否只在后台日志体现，聊天里保持和正常下载一致的文案（不暴露内部缓存逻辑）
            yield event.plain_result(
                f"⏳ 正在下载图片 {illust_id} ...\n"
                f"📖 作品标题: {info['title'] or '未知'}\n"
                f"✍️ 画师: {info['artist'] or '未知'}\n"
                f"🖼️ 页数: {page_count}\n"
                f"📦 格式: {format_text}"
            )

            if need_pack:
                # ---- 打包：下载全部（或复用本地）→ 打包 → 发送文件 ----
                temp_dir = Path(tempfile.mkdtemp(prefix=f"pixiv_{illust_id}_"))
                image_paths: list[Path] = []

                try:
                    if local_pages:
                        image_paths = list(local_pages)
                    else:
                        for i, raw_url in enumerate(pages, start=1):
                            logger.info(f"正在下载第 {i} 张图片，共 {page_count} 张")
                            img_url = self._to_proxy_url(raw_url)
                            img_data = await self._fetch_image(img_url, i, page_count)
                            ext = self._page_ext(raw_url)

                            if save_local:
                                album_dir.mkdir(parents=True, exist_ok=True)
                                (album_dir / f"{i}{ext}").write_bytes(img_data)

                            img_path = temp_dir / f"{illust_id}-{i}{ext}"
                            img_path.write_bytes(img_data)
                            image_paths.append(img_path)

                    # 打包
                    pack_path = temp_dir / f"{illust_id}.{pack_format}"

                    if pack_format == "pdf":
                        self._pack_pdf(image_paths, pack_path)
                    else:
                        self._pack_zip(image_paths, pack_path)

                    if save_local:
                        album_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(pack_path, album_dir / f"{illust_id}.{pack_format}")

                    # 发送文件
                    # 注：AstrBot 是洋葱模型（scheduler.py），yield 后先跑完 respond 阶段真正发送，
                    # 才会回到这里的 finally，所以发完再删临时目录是安全的
                    file_chain = MessageChain(
                        [
                            Comp.Plain(
                                f"📦 {illust_id} · {page_count}页 · {pack_format.upper()}"
                            ),
                            Comp.File(
                                name=f"{illust_id}.{pack_format}",
                                file=str(pack_path),
                            ),
                        ]
                    )
                    yield event.chain_result(file_chain.chain)

                finally:
                    shutil.rmtree(temp_dir, ignore_errors=True)

            elif page_count == 1:
                # ---- 单图：直接发送（已有本地文件则直接复用，不下载）----
                if local_pages:
                    # 注意：发送的是用户归档里的原文件，不能登记为临时文件（否则会被框架删掉）
                    yield event.image_result(str(local_pages[0]))
                else:
                    logger.info("正在下载第 1 张图片，共 1 张")
                    img_url = self._to_proxy_url(pages[0])
                    img_data = await self._fetch_image(img_url, 1, 1)
                    ext = self._page_ext(pages[0])

                    if save_local:
                        album_dir.mkdir(parents=True, exist_ok=True)
                        (album_dir / f"{illust_id}{ext}").write_bytes(img_data)

                    temp_dir = self._new_temp_dir(illust_id)
                    img_path = temp_dir / f"{illust_id}{ext}"
                    img_path.write_bytes(img_data)
                    self._track_temp_file(event, img_path)
                    yield event.image_result(str(img_path))

            else:
                # ---- 低于免打包数量 → 逐张发送（已有本地文件则直接复用）----
                if local_pages:
                    for path in local_pages:
                        yield event.image_result(str(path))
                else:
                    temp_dir = self._new_temp_dir(illust_id)

                    for i, raw_url in enumerate(pages, start=1):
                        logger.info(f"正在下载第 {i} 张图片，共 {page_count} 张")
                        img_url = self._to_proxy_url(raw_url)
                        img_data = await self._fetch_image(img_url, i, page_count)
                        ext = self._page_ext(raw_url)

                        if save_local:
                            album_dir.mkdir(parents=True, exist_ok=True)
                            (album_dir / f"{i}{ext}").write_bytes(img_data)

                        img_path = temp_dir / f"{illust_id}-{i}{ext}"
                        img_path.write_bytes(img_data)
                        self._track_temp_file(event, img_path)
                        yield event.image_result(str(img_path))

        except IllustNotFoundError as e:
            yield event.plain_result(f"❌ 作品不存在或ID无效：{e}")

        except PixivApiError as e:
            yield event.plain_result(f"❌ {e}，与作品ID无关，请稍后再试")

        except PageDownloadError as e:
            yield event.plain_result(f"❌ {e}")

        except PackDependencyError as e:
            yield event.plain_result(f"❌ {e}")

        except httpx.TimeoutException:
            yield event.plain_result("❌ 请求超时，请检查网络、代理设置后再试")

        except httpx.ConnectError:
            yield event.plain_result("❌ 连接失败，请检查网络、代理设置或图床地址是否可访问")

        except Exception as e:
            logger.error(f"处理失败：{str(e)}", exc_info=True)
            yield event.plain_result(f"❌ 处理失败：{str(e)[:80]}...")

    def _get_save_dir(self) -> Path:
        """获取本地保存目录（不创建，真正写入时再建）"""
        dir_str = self.config.get("save_dir", "./downloads")
        if not os.path.isabs(dir_str):
            # 相对路径 → 存到 plugin_data 安全区（重载插件不会丢失）
            dir_str = str(self.data_dir / dir_str)
        return Path(dir_str)
