# SendBlessings 插件

一个在节假日自动发送祝福语和生成节日图片的 AstrBot 插件。插件会在中国传统节假日的第一天自动生成并发送节日祝福消息，包括文字祝福和节日主题图片。

## 功能特点

- 📅 **自动节假日检测**：使用 `chinese_calendar` 库自动检测中国传统节假日
- 🎉 **智能祝福生成**：使用 LLM 生成个性化、节日氛围浓厚的祝福语
- 🖼️ **节日主题图片**：集成 OpenRouter API 生成高质量节日祝福海报
- 🌐 **多平台支持**：兼容 QQ、Telegram 等平台的 NAP 文件传输
- 🔧 **配置灵活**：支持自定义 API 配置、NAP 服务器设置等
- 🛡️ **错误处理**：完善的异常处理和日志记录，确保稳定运行

## 安装配置

### 1. 环境准备

确保您的 AstrBot 环境已安装以下依赖（通过 `requirements.txt`）：

```txt
chinese_calendar
cn_bing_translator
aiohttp
aiofiles
```

### 2. 配置插件

插件配置通过 `_conf_schema.json` 文件进行可视化配置，也可在 WebUI 中直接编辑。

#### 核心配置项

- **enabled** (bool): 启用/禁用插件，默认 `true`
- **openrouter_api_keys** (list): OpenRouter API 密钥列表，支持多个密钥自动轮换
  - 前往 [OpenRouter](https://openrouter.ai/) 注册账号获取 API Key
  - 示例：`["sk-xxx1", "sk-xxx2"]`
- **model_name** (string): 图像生成模型，默认 `google/gemini-2.5-flash-image-preview:free`
- **max_retry_attempts** (int): 每个 API 密钥的最大重试次数，默认 `3`
- **custom_api_base** (string): 自定义 API Base URL（可选，用于代理）
- **nap_server_address** (string): NAP cat 服务地址，默认 `localhost`
- **nap_server_port** (int): NAP cat 服务端口，默认 `3658`
- **holidays_file** (string): 节假日数据文件路径，默认 `data/holidays.json`

#### 配置示例

在插件管理界面配置以下内容：

```json
{
  "enabled": true,
  "openrouter_api_keys": [
    "your_openrouter_api_key_here"
  ],
  "model_name": "google/gemini-2.5-flash-image-preview:free",
  "max_retry_attempts": 3,
  "custom_api_base": "",
  "nap_server_address": "localhost",
  "nap_server_port": 3658,
  "holidays_file": "data/holidays.json"
}
```

### 3. 目标会话配置

插件会自动发送祝福到配置的目标会话。需要在 `main.py` 的 `__init__` 方法中设置 `self.target_sessions` 列表：

```python
self.target_sessions = [
    'aiocqhttp:GROUP:123456789',  # QQ 群
    'aiocqhttp:FRIEND:987654321', # QQ 私聊
    # 添加更多会话 ID
]
```

**注意**：请替换为实际的会话 ID 格式 `platform:TYPE:session_id`。

## 使用方法

### 自动运行

1. 确保插件已启用（`enabled: true`）
2. 配置有效的 OpenRouter API 密钥
3. 设置目标会话列表
4. 插件会在节假日第一天自动检测并发送祝福消息

### 手动命令

插件提供以下管理命令：

- **/blessings reload**：重新加载节假日数据
  - 用例：节假日数据更新后手动刷新

- **/blessings check**：检查今天是否为节假日第一天
  - 用例：快速验证当前日期状态

- **/blessings manual [节日名称]**：手动生成并发送节日祝福（仅管理员）
  - 用例：测试插件功能或临时发送祝福
  - 示例：`/blessings manual`（使用今天节日）或 `/blessings manual 元旦`

### 工作流程

1. **每日检查**：插件每天自动检查当前日期
2. **节日检测**：使用 `chinese_calendar` 库判断是否为节假日第一天
3. **祝福生成**：调用 LLM 生成节日祝福语
4. **图片生成**：使用 OpenRouter API 生成节日主题图片
5. **消息发送**：将祝福语和图片组合成消息链，发送到目标会话

## 技术实现

### 核心组件

- **main.py**：插件主逻辑，包含节假日检测、LLM 祝福生成、图片生成和消息发送
- **utils/ttp.py**：OpenRouter API 图像生成模块，支持多密钥轮换和重试机制
- **utils/file_send_server.py**：NAP 文件传输模块，支持本地和远程服务器传输
- **holidays_get.py**：节假日数据获取和缓存模块（可选导入）

### 关键方法

- **generate_blessing()**：使用 LLM 生成节日祝福语
  - 查询节日习俗作为上下文
  - 生成 50-100 字的中文祝福语

- **query_holiday_customs()**：查询节日习俗信息
  - 使用 AstrBot 内置 websearch 功能
  - 增强祝福语的节日相关性和文化准确性

- **generate_image()**：生成节日主题图片
  - 调用 OpenRouter API（Gemini 模型）
  - 支持 NAP 文件传输
  - 自动清理过期图片文件

- **daily_blessing_checker()**：每日定时检查任务
  - 24 小时周期运行
  - 检测节假日第一天并触发祝福生成
  - 支持多会话并发发送

### 图片生成流程

1. 构建节日主题提示词（包含祝福语和节日元素）
2. 调用 `generate_image_openrouter()` 函数
3. 处理 NAP 传输（如果配置了远程服务器）
4. 返回图片 URL 用于消息链构建

### 文件结构

```
astrbot_plugin_SendBlessings/
├── main.py                 # 插件主文件
├── metadata.yaml          # 插件元数据
├── _conf_schema.json      # 配置模式定义
├── utils/
│   ├── ttp.py            # OpenRouter API 调用和图像处理
│   └── file_send_server.py # NAP 文件传输工具
├── images/               # 生成的图像临时存储目录
├── LICENSE              # 许可证文件
├── README.md           # 项目说明文档
└── requirements.txt    # Python 依赖
```

## 错误处理

插件包含完善的错误处理机制：

- **API 调用失败**：自动重试和密钥轮换
- **网络异常**：超时处理和连接错误恢复
- **文件操作异常**：图片保存和传输失败的回退机制
- **数据加载异常**：节假日数据缺失时的自动更新
- **权限检查**：管理员命令的权限验证

## 测试指南

### 1. 单元测试

使用 `/blessings manual` 命令测试单个功能：

```
/blessings manual 元旦
```

### 2. 完整流程测试

1. 配置 OpenRouter API 密钥
2. 设置目标会话
3. 等待节假日或使用手动命令测试

### 3. 日志检查

查看日志确认：
- 节假日检测是否正确
- LLM 响应是否正常
- 图片生成和传输是否成功

## 常见问题

### Q: 插件没有发送祝福消息

**A**：
1. 检查插件是否启用（`enabled: true`）
2. 确认 OpenRouter API 密钥有效
3. 查看日志确认节假日检测结果
4. 确保目标会话列表不为空

### Q: 图片生成失败

**A**：
1. 检查 API 密钥额度是否耗尽
2. 确认模型名称是否正确
3. 查看网络连接状态
4. 检查 NAP 服务器配置（如果使用远程传输）

### Q: 节假日数据不准确

**A**：
1. 执行 `/blessings reload` 重新加载
2. 检查 `holidays_file` 路径配置
3. 确认网络连接（websearch 查询习俗）

## 贡献指南

欢迎提交 Issue 和 Pull Request 来改进这个插件。

### 开发建议

- **节日扩展**：添加更多国家和地区的节日
- **图片风格**：支持自定义节日图片模板
- **多语言支持**：扩展到英文、日文等语言祝福
- **定时优化**：精确到 UTC 时间进行节日检测

## 许可证

本项目采用开源许可证，详见 LICENSE 文件。

## 联系方式

- **作者**: Cheng-MaoMao
- **版本**: 1.0.0
- **许可证**: 见 LICENSE 文件
- **项目地址**: [GitHub Repository](https://github.com/your-username/astrbot_plugin_SendBlessings)
