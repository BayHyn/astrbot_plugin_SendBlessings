# 🤖 AstrBot Festival Blessings Plugin (SendBlessings)

[[简体中文]](./README.md) [English]

---

## 📖 Introduction

**SendBlessings** is an automated festival blessings plugin designed for [AstrBot](https://github.com/AstrBotDevs/AstrBot). It can automatically detect Chinese statutory holidays and, on the first day of each holiday, send festive blessings with beautifully generated images to specified QQ groups and friends.

The plugin features powerful image generation capabilities, dynamically creating blessing images based on the holiday theme, and supports using LLMs (Large Language Models) to generate personalized blessing messages.

## ✨ Features

-   **Automatic Holiday Detection**: Automatically fetches and caches statutory holiday information for the current year, requiring no manual intervention.
-   **Scheduled Blessing Sending**: Automatically triggers and sends blessings in the early morning of each statutory holiday.
-   **Dynamic Image Generation**: Utilizes the [OpenRouter](https://openrouter.ai/) API and configurable image generation models (like `Google Gemini`) to dynamically generate unique blessing images for each holiday.
-   **Intelligent Blessing Messages**: Optionally uses the configured LLM to generate more personalized and human-like blessing messages.
-   **Flexible Target Configuration**: Supports configuring separate lists of QQ groups and QQ friends to receive blessings.
-   **Reference Image Support**: Supports using local images as style or content references when generating images.
-   **Administrator Tools**: Provides a rich set of administrator commands for testing, reloading data, and manually sending blessings.

## ⚙️ Installation & Configuration

1. **Download Plugin**: Recommended installation via AstrBot's plugin manager. Alternatively, place the plugin folder into AstrBot's `data/plugins` directory and manually install dependencies.
2. **Configure Plugin**: In AstrBot's WebUI, navigate to "Plugin Management", find the "SendBlessings" plugin, and click the "Configure" button for visual configuration.

### 🔧 Configuration Items Explanation

-   `enabled`: Whether to enable the plugin (Boolean, default: `true`).
-   `openrouter_api_keys`: List of API keys for the large model (List, required). Supports multiple keys for automatic rotation.
-   `custom_api_base`: Custom API Base URL (String, optional). Used to specify a proxy address compatible with the OpenRouter API.
-   `model_name`: Model name used for image generation (String, default: `google/gemini-2.5-flash-image-preview:free`).
-   `max_retry_attempts`: Maximum number of retry attempts per API key (Integer, default: `3`).
-   `holidays_file`: Holiday data cache filename (String, default: `holidays.json`).
-   **`user_limits`**: **List of user QQ numbers who should receive private blessings** (List, core configuration). Add QQ numbers here for users who should receive blessings.
-   **`group_limits`**: **List of QQ group numbers that should receive group blessings** (List, core configuration). Add QQ group numbers here for groups that should receive blessings.
-   `nap_server_address`: NAP cat server address (String, default: `localhost`). If the bot and NapCat are not on the same server, fill in the NapCat server's IP address.
-   `nap_server_port`: NAP cat file receiving port (Integer, default: `3658`).
-   `reference_images`: Reference image related configuration (Object).
    -   `enabled`: Whether to enable the reference image feature (Boolean, default: `false`).
    -   `image_paths`: List of reference image file paths (List). Paths are relative to the plugin directory.
    -   `max_images`: Maximum number of reference images to use (Integer, default: `3`).

## 🚀 Usage

The core functionality of the plugin is fully automatic; blessings will be sent automatically on holidays once configured. Additionally, the plugin provides some convenient management commands.

### 👨‍💻 Administrator Commands

-   `/blessings reload`: Re-fetches and reloads the current year's holiday data from the network.
-   `/blessings check`: Checks if today is the first day of a holiday and returns the result.
-   `/blessings manual [holiday_name]`: Manually triggers the blessing generation and sending process once. If `holiday_name` is provided, it uses that name; otherwise, it uses the day's holiday name. This command sends the blessing to the **current session** and is mainly used for testing.
-   `/blessings test`: Sends a test message to all targets listed in the `user_limits` and `group_limits` configuration, used to verify if the configuration is correct.

## 🛠️ Technical Implementation

-   **Holiday Data**: Uses the `chinese-calendar` library to obtain Chinese statutory holiday and adjustment information.
-   **Blessing Message Generation**: Prioritizes using the LLM provider configured in AstrBot to generate blessing messages. Falls back to built-in template messages if generation fails.
-   **Image Generation**: Makes asynchronous requests via `aiohttp` to the OpenRouter API, calling the specified model to generate images. Supports multi-key rotation and exponential backoff retry mechanisms to improve success rates.
-   **File Handling**: Generated images are saved in the `data/SendBlessings/images` directory, and expired files are automatically cleaned up. If NAP service is configured, images are sent to the protocol client server for processing.

## 📦 Third-Party Libraries Used
- The powerful and flexible bot platform provided by [AstrBot](https://github.com/AstrBotDevs/AstrBot)
- Translation functionality provided by [cn_bing_translator](https://github.com/minibear2021/cn_bing_translator)
- Chinese holiday acquisition functionality provided by [chinese-calendar](https://github.com/LKI/chinese-calendar)

## 📄 License
This project is licensed under the AGPL-3.0 license. Please see the [LICENSE](https://github.com/Cheng-MaoMao/astrbot_plugin_SendBlessings?tab=AGPL-3.0-1-ov-file#readme) file for details.
