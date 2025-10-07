# 🤖 AstrBot 节日祝福插件 (SendBlessings)

[简体中文] [[English]](./README_en.md)

---

## 📖 简介

**SendBlessings** 是一款为 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 设计的自动化节日祝福插件。它能够自动检测中国的法定节假日，并在假期的第一天，向指定的QQ群和好友发送包含精美配图的节日祝福。

插件内置了强大的图像生成功能，可以根据节日主题动态生成祝福图片，并支持使用LLM（大型语言模型）生成个性化的祝福语。

## ✨ 功能特点

-   **自动检测节假日**: 自动获取并缓存当前年份的法定节假日信息，无需手动干预。
-   **定时发送祝福**: 在每个法定节假日的凌晨自动触发，发送祝福。
-   **动态图片生成**: 利用 [OpenRouter](https://openrouter.ai/) API 和可配置的图像生成模型（如 `Google Gemini`），为每个节日动态生成独一无二的祝福图片。
-   **智能祝福语**: 可选地使用已配置的LLM生成更具人情味的个性化祝福语。
-   **灵活的目标配置**: 支持分别配置需要发送祝福的QQ群和QQ好友列表。
-   **参考图支持**: 支持在生成图片时使用本地图片作为风格或内容参考。
-   **管理员工具**: 提供丰富的管理员命令，方便进行测试、重载数据和手动发送祝福。

## ⚙️ 安装与配置

1. **下载插件**: 推荐使用AstrBot的插件管理器安装。或者将插件文件夹放置于 AstrBot 的 `data/plugins` 目录下，并手动安装依赖。
2. **配置插件**: 在 AstrBot 的 WebUI 中，进入“插件管理”，找到“SendBlessings”插件，点击“配置”按钮进行可视化配置。

### 🔧 配置项说明

-   `enabled`: 是否启用插件 (布尔型, 默认: `true`)。
-   `openrouter_api_keys`: 大模型的 API 密钥列表 (列表, 必需)。支持填写多个密钥以实现自动轮换。
-   `custom_api_base`: 自定义 API Base URL (字符串, 可选)。用于指定兼容OpenRouter API的代理地址。
-   `model_name`: 用于生成图片的模型名称 (字符串, 默认: `google/gemini-2.5-flash-image-preview:free`)。
-   `max_retry_attempts`: 每个API密钥的最大重试次数 (整数, 默认: `3`)。
-   `holidays_file`: 节假日数据缓存文件名 (字符串, 默认: `holidays.json`)。
-   `nap_server_address`: NAP cat 服务地址 (字符串, 默认: `localhost`)。如果机器人和NapCat不在同一台服务器，请填写NapCat服务器的IP地址。
-   `nap_server_port`: NAP cat 文件接收端口 (整数, 默认: `3658`)。
-   `reference_images`: 参考图相关配置 (对象)。
    -   `enabled`: 是否启用参考图功能 (布尔型, 默认: `false`)。
    -   `image_paths`: 参考图文件路径列表 (列表)。路径是相对于插件目录的。
    -   `max_images`: 最多使用的参考图数量 (整数, 默认: `3`)。

## 🚀 使用方法

插件的核心功能是全自动的，配置完成后即可在节假日自动发送祝福。此外，插件还提供了一些方便管理的命令。

### 👨‍💻 管理员命令

-   `/blessings reload`: 重新从网络获取并加载当前年份的节假日数据。
-   `/blessings check`: 检查今天是否是节假日的第一天，并返回检查结果。
-   `/blessings manual [holiday_name]`: 手动触发一次祝福生成和发送流程。如果提供了 `holiday_name`，则使用该名称，否则使用当天的节日名称。该命令会将祝福发送到**当前会话**，主要用于测试。
-   `/blessings test`: 向所有群组/用户发送节日祝福，用于测试。

## 🛠️ 技术实现

-   **节假日数据**: 使用 `chinese-calendar` 库获取中国的法定节假日和调休信息。
-   **祝福语生成**: 优先尝试使用 AstrBot 中配置的LLM提供商生成祝福语，如果失败则回退到内置的模板祝福语。
-   **图片生成**: 通过 `aiohttp` 异步请求 OpenRouter API，调用指定的模型生成图片。支持多密钥轮换和指数退避重试机制，以提高成功率。
-   **文件处理**: 生成的图片保存在 `data/SendBlessings/images` 目录下，并会自动清理过期文件。如果配置了NAP服务，则会将图片发送到协议端服务器再进行处理。

## 🗺️ 未来规划
- [ ] 自定义发送的群组/好友的白名单
- [ ] 支持更多节日（如西方节日）
- [ ] 支持更多图片生成模型

## 📦 使用的第三方库
- [AstrBot](https://github.com/AstrBotDevs/AstrBot) 提供的强大、灵活的机器人平台
- [cn_bing_translator](https://github.com/minibear2021/cn_bing_translator) 提供的翻译功能
- [chinese-calendar](https://github.com/LKI/chinese-calendar) 提供的中国节假日获取功能

## 📄 许可证
本项目采用 AGPL-3.0 许可证，详情请参阅 [LICENSE](https://github.com/Cheng-MaoMao/astrbot_plugin_SendBlessings?tab=AGPL-3.0-1-ov-file#readme) 文件。