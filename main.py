"""
Pixiv图床下载插件

从Pixiv第三方图床(pixiv.re/pixiv.cat/i.pixiv.re)下载作品。
单图直发JPG，多图可逐张发送或打包PDF/ZIP发送。
"""
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register
import httpx


@register(
    "astrbot_plugin_pixiv_re",
    "haexiao",
    "从Pixiv第三方图床下载作品，单图直发JPG，多图可逐张发送或打包PDF/ZIP发送",
    "1.1.0",
    "https://github.com/haexiao/astrbot_plugin_pixiv_re",
)
class PixivRePlugin(Star):
    """Pixiv图床下载插件"""

    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config
        self.client: httpx.AsyncClient | None = None
        logger.info("Pixiv图床下载插件已初始化")

    async def initialize(self):
        """初始化：创建复用的 httpx 异步客户端"""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            headers=headers,
            follow_redirects=True,
        )
        logger.info("Pixiv图床下载插件初始化完成")

    async def terminate(self):
        """销毁：清理 httpx 客户端"""
        if self.client and not self.client.is_closed:
            try:
                await self.client.aclose()
                logger.info("已关闭 httpx 异步客户端")
            except Exception as e:
                logger.error(f"关闭客户端失败：{str(e)}")

    # ==================== 核心功能 ====================

    async def _detect_pages(self, host: str, illust_id: str) -> int:
        """
        通过 {host}/{id}-999.jpg 的响应判断作品的图片页数。

        - 单图返回："only one page" → 返回 1
        - 多图返回："only has N pages" → 提取 N
        """
        url = f"{host}/{illust_id}-999.jpg"
        try:
            resp = await self.client.get(url, timeout=httpx.Timeout(10.0))
            body = resp.text
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:
                raise
            body = e.response.text

        if "only one page" in body.lower():
            return 1

        match = re.search(r"only has (\d+) pages?", body.lower())
        if match:
            return int(match.group(1))

        raise Exception("无法识别图床返回的页数信息，请检查作品ID")

    def _pack_pdf(self, image_paths: list[Path], output_path: Path) -> Path:
        """将图片列表打包为 PDF"""
        import img2pdf

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
                "打包格式可选：pdf（默认）、zip\n"
                "💡 多图时指定格式可强制打包，否则低于免打包数量时自动逐张发送"
            )
            return

        illust_id = args[1]
        if not illust_id.isdigit():
            yield event.plain_result("❌ 作品ID必须是数字")
            return

        # 判断用户是否在指令中指定了打包格式
        has_pack_arg = len(args) >= 3 and args[2].lower() in ("pdf", "zip")

        # 读取打包格式（仅在强制打包时使用）
        pack_format = self.config.get("pack_format", "pdf")
        if has_pack_arg:
            pack_format = args[2].lower()

        max_free = self.config.get("max_free_pack_count", 10)
        host = self.config.get("download_host", "https://pixiv.re").rstrip("/")

        try:
            # ---- 第1步：探测页数 ----
            page_count = await self._detect_pages(host, illust_id)

            if page_count <= 1:
                # ---- 单图：直接发送 JPG ----
                img_url = f"{host}/{illust_id}.jpg"
                resp = await self.client.get(img_url)
                resp.raise_for_status()

                if self.config.get("save_local", False):
                    save_dir = self._get_save_dir()
                    (save_dir / f"{illust_id}.jpg").write_bytes(resp.content)

                yield event.image_result(img_url)

            elif has_pack_arg or page_count > max_free:
                # ---- 强制打包 / 超出免打包数量 → 打包发送 ----
                if has_pack_arg:
                    yield event.plain_result(
                        f"⏳ 共 {page_count} 页，正在打包为 {pack_format.upper()}..."
                    )
                else:
                    yield event.plain_result(
                        f"⏳ 共 {page_count} 页，超出免打包数量({max_free})，正在打包..."
                    )

                temp_dir = Path(tempfile.mkdtemp(prefix=f"pixiv_{illust_id}_"))
                image_paths: list[Path] = []
                save_local = self.config.get("save_local", False)
                save_dir = self._get_save_dir()

                try:
                    for i in range(1, page_count + 1):
                        logger.info(f"正在下载第 {i} 张图片，共 {page_count} 张")
                        img_url = f"{host}/{illust_id}-{i}.jpg"
                        resp = await self.client.get(img_url)
                        resp.raise_for_status()
                        img_data = resp.content

                        if save_local:
                            (save_dir / f"{illust_id}-{i}.jpg").write_bytes(img_data)

                        img_path = temp_dir / f"{illust_id}-{i}.jpg"
                        img_path.write_bytes(img_data)
                        image_paths.append(img_path)

                    # 打包
                    pack_path = temp_dir / f"{illust_id}.{pack_format}"

                    if pack_format == "pdf":
                        self._pack_pdf(image_paths, pack_path)
                    else:
                        self._pack_zip(image_paths, pack_path)

                    if save_local:
                        shutil.copy2(
                            pack_path, save_dir / f"{illust_id}.{pack_format}"
                        )

                    # 发送文件
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

            else:
                # ---- 低于免打包数量 → 逐张发送 ----
                yield event.plain_result(
                    f"🖼️ 共 {page_count} 页，低于免打包数量({max_free})，逐张发送..."
                )
                save_local = self.config.get("save_local", False)
                save_dir = self._get_save_dir()

                for i in range(1, page_count + 1):
                    logger.info(f"正在下载第 {i} 张图片，共 {page_count} 张")
                    img_url = f"{host}/{illust_id}-{i}.jpg"
                    resp = await self.client.get(img_url)
                    resp.raise_for_status()
                    img_data = resp.content

                    if save_local:
                        (save_dir / f"{illust_id}-{i}.jpg").write_bytes(img_data)

                    yield event.image_result(img_url)

        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status == 404:
                yield event.plain_result("❌ 作品不存在，请检查ID是否正确")
            elif status == 403:
                yield event.plain_result("❌ 访问被拒绝，图床可能限制了该作品")
            else:
                yield event.plain_result(
                    f"❌ 请求失败（状态码：{status}），请稍后再试"
                )

        except httpx.TimeoutException:
            yield event.plain_result("❌ 请求超时，请检查网络或稍后再试")

        except httpx.ConnectError:
            yield event.plain_result("❌ 连接失败，请检查图床地址是否可访问")

        except Exception as e:
            logger.error(f"处理失败：{str(e)}", exc_info=True)
            yield event.plain_result(f"❌ 处理失败：{str(e)[:80]}...")

    def _get_save_dir(self) -> Path:
        """获取本地保存目录"""
        dir_str = self.config.get("save_dir", "./downloads")
        if not os.path.isabs(dir_str):
            dir_str = str(Path(__file__).parent / dir_str)
        path = Path(dir_str)
        path.mkdir(parents=True, exist_ok=True)
        return path
