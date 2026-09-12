# AstrBot Pixiv 图床下载插件

[![AstrBot Plugin Market](https://img.shields.io/badge/AstrBot-%E6%8F%92%E4%BB%B6%E5%B8%82%E5%9C%BA-blue)](https://docs.astrbot.app/dev/plugin-market/2026-06-27.html)

适用于 AstrBot 的 Pixiv 第三方图床下载插件。支持单图直发和多图打包 PDF/ZIP 发送。

## 功能特性

- 根据作品 ID 从 Pixiv 第三方图床获取作品（无需 Pixiv 账号）
- 先通过图床接口获取作品页数与原图地址，页数识别准确
- 单张图片直接发送图片到聊天
- 多张图片自动打包为 **PDF** 或 **ZIP** 文件发送
- 支持三个下载域名切换：`i.pixiv.re`、`i.pixiv.nl`、`i.pixiv.cat`
- 支持 http/https 与 socks5 代理
- 可选本地保存图片/打包文件
- 简洁输出：不展示作品信息，直接发图

## 安装说明

在插件商店直接下载安装，或在 `astrbot/data/plugins` 目录手动下载解压。

## 使用说明

### 基础指令格式

```
/pixiv <作品ID> [打包格式]
```

### 指令详情

| 指令 | 说明 | 示例 |
|------|------|------|
| `/pixiv <作品ID>` | 下载指定作品，低于免打包数量逐张发图，高于则打包 | `/pixiv 118908797` |
| `/pixiv <作品ID> pdf` | 强制打包为 PDF（单图也会打包） | `/pixiv 118908797 pdf` |
| `/pixiv <作品ID> zip` | 强制打包为 ZIP（单图也会打包） | `/pixiv 118908797 zip` |

### 返回示例

发送 `/pixiv 119550392` 后，先收到一条统一提示：

```
⏳ 正在下载图片 119550392 ...
📖 作品标题: ブルアカまとめ
✍️ 画师: yukko
🖼️ 页数: 173
📦 格式: PDF
```

然后按情况继续：

- **单图** → 直接发送图片
- **多图 ≤ 免打包数量** → 逐张发送图片
- **多图 > 免打包数量**，或用 `/pixiv <ID> pdf`、`/pixiv <ID> zip` 指定 → 打包后发送文件（**指定格式时单图也会打包**）

其中 `📦 格式` 行的取值即最终发送格式：`JPG`（直接发图）或 `PDF` / `ZIP`（打包发文件）。

### 插件配置

在 AstrBot 管理面板的插件配置页面可调整以下选项：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `api_endpoint` | string | `https://api.pixiv.cat/v1/generate` | 作品信息接口地址，可选：api.pixiv.cat / api.pixiv.re / api.pixiv.nl（同一套服务的镜像域名） |
| `image_proxy` | string | `https://i.pixiv.re` | 图片下载域名，可选：i.pixiv.re / i.pixiv.nl / i.pixiv.cat |
| `use_proxy` | bool | `false` | 是否使用代理；开启后接口与图片下载都走该代理 |
| `proxy_url` | string | 空 | 代理地址，格式：`http://host:port` 或 `socks5://host:port` |
| `info_cache_ttl` | int | `600` | 作品信息缓存秒数，0=不缓存 |
| `max_pages` | int | `200` | 单次下载页数上限，超过则取消并提示，0=不限制 |
| `reuse_local_files` | bool | `true` | 本地已保存整部作品时直接复用不再下载（需开启 `save_local`；找不齐则重新下载） |
| `pack_format` | string | `pdf` | 多图打包格式，可选：pdf / zip |
| `save_local` | bool | `false` | 是否在本地保存图片/打包文件 |
| `save_dir` | string | `./downloads` | 本地保存目录 |
| `max_free_pack_count` | int | `10` | 多图最大免打包数量，低于此数逐张发图，高于则自动打包 |

## 常见问题

### 1. 作品不存在或ID无效
- 请检查作品 ID 是否正确
- 部分作品可能已被删除或隐藏

### 2. 提示"图床接口故障"
- 说明图床服务端异常，与作品 ID 无关
- 可稍后重试；长期不可用可更换 `api_endpoint`

### 3. 某页下载失败 / 图片不是图片类型
- 通常是下载域名不稳定，可在配置中切换 `image_proxy`
- `i.pixiv.re` 与官方原图一致；`i.pixiv.nl` 可能被重新压缩；`i.pixiv.cat` 目前不稳定

### 4. 请求超时、连接失败，或提示"图床接口故障"（ConnectError / DNS 解析失败）
- 说明当前网络访问不到图床接口域名，通常是 DNS 或网络环境问题
- 可尝试开启 `use_proxy` 并填写 `proxy_url`（如本机代理 `http://127.0.0.1:7890`），开启后接口与图片下载都走代理

### 5. 作品页数超过上限
- 调整 `max_pages`（默认 200，0 为不限制）

### 6. 页数很多时不希望逐张刷屏
- 把 `max_free_pack_count` 调小（例如 3），页数超过它就会自动打包发送，不再逐张发图

## 免责声明

- 本插件遵循和 AstrBot 相同的许可证
- 图片资源均来自第三方图床，版权归原作者所有
- 使用本插件需遵守相关法律法规及平台规则
