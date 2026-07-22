# AstrBot Pixiv 图床下载插件

[![AstrBot Plugin Market](https://img.shields.io/badge/AstrBot-%E6%8F%92%E4%BB%B6%E5%B8%82%E5%9C%BA-blue)](https://docs.astrbot.app/dev/plugin-market/2026-06-27.html)

适用于 AstrBot 的 Pixiv 第三方图床下载插件。支持单图直发和多图打包 PDF/ZIP 发送。

## 功能特性

- 根据作品 ID 从 Pixiv 第三方图床获取作品（无需 Pixiv 账号）
- 单张图片直接发送 JPG 图片到聊天
- 多张图片自动打包为 **PDF** 或 **ZIP** 文件发送
- 支持三个图床源切换：`pixiv.re`、`pixiv.cat`、`i.pixiv.re`
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
| `/pixiv <作品ID> pdf` | 强制打包为 PDF | `/pixiv 118908797 pdf` |
| `/pixiv <作品ID> zip` | 强制打包为 ZIP | `/pixiv 118908797 zip` |

### 返回示例

```
/pixiv 118908797
```
- 单图 → 直接发送 JPG 图片
- 多图 → ⏳ 共 5 页，正在下载...  → 打包 → 发送文件

```
/pixiv 118908797 zip
```
- 强制以 ZIP 格式打包发送（无视默认配置）

### 插件配置

在 AstrBot 管理面板的插件配置页面可调整以下选项：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `download_host` | string | `https://pixiv.re` | 图床地址，可选：pixiv.re / pixiv.cat / i.pixiv.re |
| `pack_format` | string | `pdf` | 多图打包格式，可选：pdf / zip |
| `save_local` | bool | `false` | 是否在本地保存图片/打包文件 |
| `save_dir` | string | `./downloads` | 本地保存目录 |
| `max_free_pack_count` | int | `10` | 多图最大免打包数量，低于此数逐张发图，高于则自动打包 |

## 常见问题

### 1. 作品不存在（404）
- 请检查作品 ID 是否正确
- 部分作品可能已被删除或隐藏

### 2. 访问被拒绝（403）
- 图床可能限制了该地区的访问
- 可尝试切换其他图床地址

### 3. 请求超时
- 网络延迟过高，可等待后重试
- 可尝试切换其他图床地址

## 免责声明

- 本插件遵循和 AstrBot 相同的许可证
- 图片资源均来自第三方图床，版权归原作者所有
- 使用本插件需遵守相关法律法规及平台规则
