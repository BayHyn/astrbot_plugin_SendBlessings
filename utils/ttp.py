import random
import aiohttp
import asyncio
import aiofiles
import base64
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from astrbot.api import logger

# --- 模型特定配置 ---
MODEL_CONFIGS = {
    "nano-banana": {
        "endpoint": "/v1/images/generations",
        "payload_builder": lambda prompt, model, size, **_: {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": size
        }
    },
    "default": {
        "endpoint": "/v1/chat/completions",
        "payload_builder": lambda prompt, model, max_tokens, temperature, content, **_: {
            "model": model,
            "messages": [{"role": "user", "content": content or prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature
        }
    }
}

class ImageGeneratorState:
    """
    图像生成器的状态管理类，用于在异步环境中安全地处理共享状态。
    """
    def __init__(self):
        self.last_saved_image = {"url": None, "path": None}
        self.api_key_index = 0
        self._lock = asyncio.Lock()
    
    async def get_next_api_key(self, api_keys: list) -> str:
        async with self._lock:
            if not api_keys or not isinstance(api_keys, list):
                raise ValueError("API密钥列表不能为空")
            return api_keys[self.api_key_index % len(api_keys)]
    
    async def rotate_to_next_api_key(self, api_keys: list):
        async with self._lock:
            if api_keys and isinstance(api_keys, list) and len(api_keys) > 1:
                self.api_key_index = (self.api_key_index + 1) % len(api_keys)
                logger.info(f"已轮换到下一个API密钥，当前索引: {self.api_key_index}")
    
    async def update_saved_image(self, url: str, path: str):
        async with self._lock:
            self.last_saved_image = {"url": url, "path": path}
    
    async def get_saved_image_info(self) -> tuple[str | None, str | None]:
        async with self._lock:
            return self.last_saved_image["url"], self.last_saved_image["path"]

_state = ImageGeneratorState()

async def cleanup_old_images(images_dir: Path):
    """清理指定目录下超过15分钟的旧图像文件。"""
    try:
        if not images_dir.exists():
            return
        current_time = datetime.now()
        cutoff_time = current_time - timedelta(minutes=15)
        image_patterns = ["blessing_image_*.png", "blessing_image_*.jpg", "blessing_image_*.jpeg"]
        for pattern in image_patterns:
            for file_path in images_dir.glob(pattern):
                try:
                    file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                    if file_mtime < cutoff_time:
                        file_path.unlink()
                        logger.info(f"已清理过期图像: {file_path}")
                except Exception as e:
                    logger.warning(f"清理文件 {file_path} 时出错: {e}")
    except Exception as e:
        logger.error(f"图像清理过程出错: {e}")

async def save_base64_image(base64_string: str, image_format: str, data_dir: Path) -> tuple[str | None, str | None]:
    """将base64编码的图像数据解码并保存到本地。"""
    try:
        images_dir = data_dir / "images"
        images_dir.mkdir(exist_ok=True)
        await cleanup_old_images(images_dir)
        image_data = base64.b64decode(base64_string)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid.uuid4())[:8]
        image_path = images_dir / f"blessing_image_{timestamp}_{unique_id}.{image_format}"
        async with aiofiles.open(image_path, "wb") as f:
            await f.write(image_data)
        abs_path = str(image_path.absolute())
        file_url = f"file://{abs_path}"
        await _state.update_saved_image(file_url, str(image_path))
        logger.info(f"图像已保存到: {abs_path}")
        return file_url, str(image_path)
    except (base64.binascii.Error, Exception) as e:
        logger.error(f"保存图像文件失败: {e}")
        return None, None

def _get_model_config(model_name: str) -> dict:
    """根据模型名称获取其特定配置。"""
    for key, config in MODEL_CONFIGS.items():
        if key in model_name.lower():
            return config
    return MODEL_CONFIGS["default"]

def _build_request_payload(prompt: str, model: str, input_images: list[str], max_tokens: int, temperature: float) -> dict:
    """构建API请求体。"""
    model_config = _get_model_config(model)
    
    message_content = [{"type": "text", "text": prompt}]
    if input_images:
        for base64_image in input_images:
            if not base64_image.startswith('data:image/'):
                base64_image = f"data:image/png;base64,{base64_image}"
            message_content.append({"type": "image_url", "image_url": {"url": base64_image}})

    return model_config["payload_builder"](
        prompt=prompt,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        content=message_content,
        size="1024x1024"  # For DALL-E like models
    )

async def _send_api_request(session: aiohttp.ClientSession, url: str, headers: dict, payload: dict) -> dict:
    """发送API请求并返回JSON响应。"""
    async with session.post(url, json=payload, headers=headers) as response:
        response.raise_for_status()  # Will raise an exception for 4xx/5xx status
        return await response.json()

async def _parse_response(data: dict, data_dir: Path) -> tuple[str | None, str | None]:
    """解析API响应以提取图像数据。"""
    # 1. DALL-E / nano-banana 格式
    if "data" in data and data["data"]:
        image_item = data["data"][0]
        if "b64_json" in image_item:
            return await save_base64_image(image_item["b64_json"], "png", data_dir)
        # URL aituprc ...
    
    # 2. Gemini / Chat-based 格式
    elif "choices" in data and data["choices"]:
        content = data["choices"][0].get("message", {}).get("content", "")
        if isinstance(content, str):
            base64_pattern = r"data:image/([^;]+);base64,([A-Za-z0-9+/=]+)"
            match = re.search(base64_pattern, content)
            if match:
                return await save_base64_image(match.group(2), match.group(1), data_dir)
    
    logger.warning("API响应中未找到可识别的图像数据。")
    return None, None

async def generate_image_openrouter(
    prompt: str,
    api_keys: list[str],
    model: str,
    data_dir: Path,
    max_tokens: int = 1024,
    input_images: list[str] = None,
    api_base: str = None,
    max_retry_attempts: int = 3,
    temperature: float = 0.7
) -> tuple[str | None, str | None]:
    """
    使用支持OpenAI格式的API生成图像，具有重试和密钥轮换功能。
    """
    if isinstance(api_keys, str):
        api_keys = [api_keys]
    if not api_keys:
        logger.error("未提供API密钥，无法生成图像。")
        return None, None

    model_config = _get_model_config(model)
    base_url = (api_base or "https://openrouter.ai/api").rstrip('/')
    url = f"{base_url}{model_config['endpoint']}"
    
    payload = _build_request_payload(prompt, model, input_images, max_tokens, temperature)

    for api_attempt in range(len(api_keys)):
        current_api_key = await _state.get_next_api_key(api_keys)
        headers = {
            "Authorization": f"Bearer {current_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/astrbot",
            "X-Title": "AstrBot SendBlessings"
        }

        for retry_attempt in range(max_retry_attempts):
            try:
                timeout = aiohttp.ClientTimeout(total=120)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    logger.info(f"尝试生成图像 (密钥: {api_attempt+1}, 重试: {retry_attempt+1})")
                    response_data = await _send_api_request(session, url, headers, payload)
                    return await _parse_response(response_data, data_dir)
            
            except aiohttp.ClientResponseError as e:
                if e.status == 429 or e.status == 402:
                    logger.warning(f"密钥 #{api_attempt+1} 额度耗尽或速率限制 (HTTP {e.status})。切换到下一个密钥。")
                    break  # Stop retrying with this key
                logger.warning(f"API请求失败 (HTTP {e.status})，重试中... ({retry_attempt+1}/{max_retry_attempts})")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"网络请求失败: {e}，重试中... ({retry_attempt+1}/{max_retry_attempts})")
            except Exception as e:
                logger.error(f"生成图像时发生未知错误: {e}", exc_info=True)
                break # Stop retrying on unknown errors

            if retry_attempt < max_retry_attempts - 1:
                await asyncio.sleep(2 ** retry_attempt) # Exponential backoff
        
        await _state.rotate_to_next_api_key(api_keys)

    logger.error("所有API密钥和重试次数均已耗尽，图像生成失败。")
    return None, None

if __name__ == "__main__":
    async def main():
        """测试脚本主函数，用于验证图像生成功能。"""
        logger.info("测试图像生成功能...")
        
        # 从环境变量中获取API密钥
        openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
        if not openrouter_api_key:
            logger.error("请设置环境变量 OPENROUTER_API_KEY 以进行测试。")
            return

        # 规范化数据目录
        test_data_dir = Path(__file__).parent.parent / "test_data"
        test_data_dir.mkdir(exist_ok=True)

        logger.info("\n--- 测试 1: 文本到图像生成 ---")
        prompt = "一只可爱的红色小熊猫，数字艺术风格"
        
        image_url, image_path = await generate_image_openrouter(
            prompt=prompt,
            api_keys=[openrouter_api_key],
            model="google/gemini-pro-vision", # 使用一个常见的模型
            data_dir=test_data_dir
        )
        
        if image_url and image_path:
            logger.info(f"测试 1 成功! 图像路径: {image_path}")
        else:
            logger.error("测试 1 失败。")

    asyncio.run(main())