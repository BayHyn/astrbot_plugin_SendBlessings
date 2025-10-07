import random
import aiohttp
import asyncio
import aiofiles
import base64
import os
import re
import uuid
from datetime import datetime, timedelta
import glob
from pathlib import Path
from astrbot.api import logger
from astrbot.api.star import StarTools


class ImageGeneratorState:
    """
    图像生成器的状态管理类，用于在异步环境中安全地处理共享状态。

    该类使用 `asyncio.Lock` 来确保对API密钥索引和最后保存图像信息的
    访问是线程安全的（在asyncio中是任务安全的）。

    Attributes:
        last_saved_image (dict): 存储最后一次成功保存的图像信息，包括'url'和'path'。
        api_key_index (int): 当前使用的API密钥在列表中的索引。
        _lock (asyncio.Lock): 用于保护共享状态的锁。
    """
    def __init__(self):
        self.last_saved_image = {"url": None, "path": None}
        self.api_key_index = 0
        self._lock = asyncio.Lock()
    
    async def get_next_api_key(self, api_keys: list) -> str:
        """
        以线程安全的方式获取当前应使用的API密钥。

        Args:
            api_keys (list): API密钥列表。

        Returns:
            str: 当前可用的API密钥。
        
        Raises:
            ValueError: 如果API密钥列表为空。
        """
        async with self._lock:
            if not api_keys or not isinstance(api_keys, list):
                raise ValueError("API密钥列表不能为空")
            # 使用模运算来实现循环获取
            current_key = api_keys[self.api_key_index % len(api_keys)]
            return current_key
    
    async def rotate_to_next_api_key(self, api_keys: list):
        """
        以线程安全的方式将索引轮换到下一个API密钥。

        Args:
            api_keys (list): API密钥列表。
        """
        async with self._lock:
            if api_keys and isinstance(api_keys, list) and len(api_keys) > 1:
                self.api_key_index = (self.api_key_index + 1) % len(api_keys)
                logger.info(f"已轮换到下一个API密钥，当前索引: {self.api_key_index}")
    
    async def update_saved_image(self, url: str, path: str):
        """
        以线程安全的方式更新最后保存的图像信息。

        Args:
            url (str): 图像的URL（通常是file:// URL）。
            path (str): 图像的本地文件路径。
        """
        async with self._lock:
            self.last_saved_image = {"url": url, "path": path}
    
    async def get_saved_image_info(self) -> tuple[str | None, str | None]:
        """
        以线程安全的方式获取最后保存的图像信息。

        Returns:
            tuple: 包含图像URL和路径的元组 (url, path)。
        """
        async with self._lock:
            return self.last_saved_image["url"], self.last_saved_image["path"]


# 全局唯一的生成器状态管理实例
_state = ImageGeneratorState()


async def cleanup_old_images(data_dir: Path = None):
    """
    清理指定目录下超过15分钟的旧图像文件。

    这有助于防止生成的临时图像文件无限期地占用磁盘空间。

    Args:
        data_dir (Path, optional): 插件的数据目录路径。如果为None，则默认为插件根目录。
    """
    try:
        if data_dir is None:
            # 如果未提供data_dir，则假定此脚本位于 utils/ 目录下，父目录是插件根目录
            script_dir = Path(__file__).parent.parent
            data_dir = script_dir
        
        images_dir = data_dir / "images"

        if not images_dir.exists():
            return

        current_time = datetime.now()
        cutoff_time = current_time - timedelta(minutes=15)

        # 定义要清理的文件名模式
        image_patterns = ["blessing_image_*.png", "blessing_image_*.jpg", "blessing_image_*.jpeg"]

        for pattern in image_patterns:
            for file_path in images_dir.glob(pattern):
                try:
                    # 获取文件的最后修改时间
                    file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime)

                    # 如果文件最后修改时间早于15分钟前，则删除
                    if file_mtime < cutoff_time:
                        file_path.unlink()
                        logger.info(f"已清理过期图像: {file_path}")

                except Exception as e:
                    logger.warning(f"清理文件 {file_path} 时出错: {e}")

    except Exception as e:
        logger.error(f"图像清理过程出错: {e}")


async def save_base64_image(base64_string: str, image_format: str = "png", data_dir: Path = None) -> bool:
    """
    将base64编码的图像数据解码并保存到本地的 'images' 文件夹中。

    在保存前会先调用 `cleanup_old_images` 清理旧文件。
    文件名将包含时间戳和UUID以确保唯一性。

    Args:
        base64_string (str): Base64编码的图像数据字符串。
        image_format (str, optional): 图像的格式 (例如, "png", "jpeg")。默认为 "png"。
        data_dir (Path, optional): 插件的数据目录路径。如果为None，则默认为插件根目录。

    Returns:
        bool: 如果保存成功返回True，否则返回False。
    """
    try:
        if data_dir is None:
            script_dir = Path(__file__).parent.parent
            data_dir = script_dir
        
        images_dir = data_dir / "images"
        images_dir.mkdir(exist_ok=True)
        
        # 在保存新图像前，清理旧的图像文件
        await cleanup_old_images(data_dir)

        # 解码base64数据
        image_data = base64.b64decode(base64_string)

        # 生成唯一的文件名以避免冲突
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid.uuid4())[:8]
        image_path = images_dir / f"blessing_image_{timestamp}_{unique_id}.{image_format}"

        # 异步写入文件
        async with aiofiles.open(image_path, "wb") as f:
            await f.write(image_data)

        # 构造可供本地访问的file:// URL
        abs_path = str(image_path.absolute())
        file_url = f"file://{abs_path}"

        # 更新全局状态，记录最后保存的图像信息
        await _state.update_saved_image(file_url, str(image_path))

        logger.info(f"图像已保存到: {abs_path}")
        logger.debug(f"文件大小: {len(image_data)} bytes")

        return True

    except base64.binascii.Error as e:
        logger.error(f"Base64 解码失败: {e}")
        return False
    except Exception as e:
        logger.error(f"保存图像文件失败: {e}")
        return False


async def get_next_api_key(api_keys: list) -> str:
    """
    获取当前可用的API密钥（状态管理器的包装器）。

    Args:
        api_keys (list): API密钥列表。

    Returns:
        str: 当前应使用的API密钥。
    """
    return await _state.get_next_api_key(api_keys)


async def rotate_to_next_api_key(api_keys: list):
    """
    轮换到下一个API密钥（状态管理器的包装器）。

    Args:
        api_keys (list): API密钥列表。
    """
    await _state.rotate_to_next_api_key(api_keys)


async def get_saved_image_info() -> tuple[str | None, str | None]:
    """
    获取最后一次成功保存的图像信息（状态管理器的包装器）。

    Returns:
        tuple: 包含图像URL和本地路径的元组 (image_url, image_path)。
    """
    return await _state.get_saved_image_info()


async def generate_image_openrouter(
    prompt: str,
    api_keys: list[str],
    model: str = "google/gemini-2.5-flash-image-preview:free",
    max_tokens: int = 1000,
    input_images: list[str] = None,
    api_base: str = None,
    max_retry_attempts: int = 3
) -> tuple[str | None, str | None]:
    """
    使用支持OpenAI格式的API（如OpenRouter）生成图像，支持多模态输入。

    该函数非常健壮，实现了以下特性：
    - 支持多个API密钥，并在一个密钥失败（如额度耗尽）时自动轮换到下一个。
    - 对每个API密钥实现基于指数退避的重试机制，以处理临时性网络错误或服务器繁忙。
    - 兼容不同的API响应格式（例如，Gemini的聊天完成格式和标准图像生成格式）。
    - 支持多模态输入，可以将本地图像作为参考图与文本提示一起发送。
    - 自动处理base64编码的图像数据和data URI格式。

    Args:
        prompt (str): 用于图像生成的文本提示。
        api_keys (list[str]): OpenRouter API密钥列表，用于轮换和重试。
        model (str, optional): 要使用的模型名称。默认为 "google/gemini-2.5-flash-image-preview:free"。
        max_tokens (int, optional): 响应的最大token数。默认为1000。
        input_images (list[str], optional): 作为输入的base64编码的图像列表。默认为None。
        api_base (str, optional): 自定义的API基础URL（用于代理或私有部署）。默认为OpenRouter官方URL。
        max_retry_attempts (int, optional): 每个API密钥的最大重试次数。默认为3。

    Returns:
        tuple[str | None, str | None]: 成功时返回包含图像URL和本地路径的元组，失败时返回 (None, None)。
    """
    # 兼容性处理：如果传入的是单个字符串密钥，自动转为列表
    if isinstance(api_keys, str):
        api_keys = [api_keys]
    
    if not api_keys:
        logger.error("未提供API密钥，无法生成图像。")
        return None, None
    
    # 根据模型名称和是否提供api_base确定请求的URL
    if api_base:
        # 某些模型使用不同的API端点
        if "nano-banana" in model.lower():
            url = f"{api_base.rstrip('/')}/v1/images/generations"
        else:
            url = f"{api_base.rstrip('/')}/v1/chat/completions"
    else:
        url = "https://openrouter.ai/api/v1/chat/completions"
    
    # 遍历所有提供的API密钥
    max_api_attempts = len(api_keys)
    for api_attempt in range(max_api_attempts):
        try:
            current_api_key = await get_next_api_key(api_keys)
            current_index = (_state.api_key_index % len(api_keys)) + 1
            
            # 对当前API密钥进行多次重试
            for retry_attempt in range(max_retry_attempts):
                try:
                    if retry_attempt > 0:
                        # 指数退避策略，避免频繁重试导致API封禁
                        delay = min(2 ** retry_attempt, 10)
                        logger.info(f"API密钥 #{current_index} 重试 {retry_attempt + 1}/{max_retry_attempts}，等待 {delay} 秒...")
                        await asyncio.sleep(delay)
                    else:
                        logger.info(f"尝试使用API密钥 #{current_index}")
                    
                    # 构建多模态消息内容
                    message_content = []
                    
                    # 1. 添加文本部分
                    message_content.append({
                        "type": "text",
                        "text": f"Generate a festival blessing image: {prompt}"
                    })
                    
                    # 2. 如果有输入图像，添加图像部分
                    if input_images:
                        for base64_image in input_images:
                            # 确保图像数据是标准的data URI格式
                            if not base64_image.startswith('data:image/'):
                                base64_image = f"data:image/png;base64,{base64_image}"
                            
                            message_content.append({
                                "type": "image_url",
                                "image_url": {"url": base64_image}
                            })

                    # 根据模型类型构建不同的请求体 (payload)
                    if "nano-banana" in model.lower():
                        # nano-banana模型使用类似OpenAI DALL-E的图像生成接口
                        payload = {
                            "model": model,
                            "prompt": prompt,
                            "n": 1,
                            "size": "1024x1024"
                        }
                    else:
                        # Gemini等模型使用聊天完成接口进行图像生成
                        payload = {
                            "model": model,
                            "messages": [
                                {
                                    "role": "user",
                                    # 如果有图片，content是列表；否则是字符串
                                    "content": message_content if input_images else f"Generate a festival blessing image: {prompt}"
                                }
                            ],
                            "max_tokens": max_tokens,
                            "temperature": 0.7
                        }

                    headers = {
                        "Authorization": f"Bearer {current_api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://github.com/astrbot",
                        "X-Title": "AstrBot SendBlessings Image Generator"
                    }

                    # 调试日志，仅在首次尝试时打印，避免日志泛滥
                    if retry_attempt == 0:
                        logger.debug(f"模型: {model}")
                        logger.debug(f"输入图片数量: {len(input_images) if input_images else 0}")
                        if input_images:
                            logger.debug(f"第一张图片base64长度: {len(input_images[0])}")
                        if "messages" in payload:
                            logger.debug(f"消息内容结构: {type(payload['messages'][0]['content'])}")
                            if isinstance(payload['messages'][0]['content'], list):
                                content_types = [item.get('type', 'unknown') for item in payload['messages'][0]['content']]
                                logger.debug(f"消息内容类型: {content_types}")

                    timeout = aiohttp.ClientTimeout(total=60)
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.post(url, json=payload, headers=headers) as response:
                            data = await response.json()
                            
                            if retry_attempt == 0:
                                logger.debug(f"API响应状态: {response.status}")
                                logger.debug(f"响应数据键: {list(data.keys()) if isinstance(data, dict) else 'Not dict'}")

                            if response.status == 200:
                                # 1. 处理标准图像生成API的响应 (如 nano-banana)
                                if "data" in data and data["data"]:
                                    logger.info(f"收到 {len(data['data'])} 个图像")
                                    for image_item in data["data"]:
                                        if "url" in image_item:
                                            # 如果返回的是URL，需要下载
                                            image_url = image_item["url"]
                                            async with session.get(image_url) as img_response:
                                                if img_response.status == 200:
                                                    # 下载并保存为唯一文件名
                                                    script_dir = Path(__file__).parent.parent
                                                    images_dir = script_dir / "images"
                                                    images_dir.mkdir(exist_ok=True)
                                                    await cleanup_old_images(script_dir)
                                                    
                                                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                                                    unique_id = str(uuid.uuid4())[:8]
                                                    image_path = images_dir / f"blessing_image_{timestamp}_{unique_id}.png"
                                                    
                                                    async with aiofiles.open(image_path, "wb") as f:
                                                        await f.write(await img_response.read())
                                                    
                                                    abs_path = str(image_path.absolute())
                                                    file_url = f"file://{abs_path}"
                                                    await _state.update_saved_image(file_url, str(image_path))
                                                    
                                                    logger.info(f"API密钥 #{current_index} 成功生成图像: {abs_path}")
                                                    return file_url, str(image_path)
                                                else:
                                                    logger.error(f"下载图像失败: {image_url}")
                                        
                                        elif "b64_json" in image_item:
                                            # 如果返回的是base64数据
                                            base64_data = image_item["b64_json"]
                                            if await save_base64_image(base64_data, "png"):
                                                logger.info(f"API密钥 #{current_index} 成功生成图像 (base64格式)")
                                                return await get_saved_image_info()
                                
                                # 2. 处理聊天完成API的响应 (如 Gemini)
                                elif "choices" in data:
                                    choice = data["choices"][0]
                                    message = choice["message"]
                                    content = message.get("content")

                                    # 检查 Gemini 标准的 message.images 字段
                                    if "images" in message and message["images"]:
                                        logger.info(f"Gemini 返回了 {len(message['images'])} 个图像")
                                        for image_item in message["images"]:
                                            if "image_url" in image_item and "url" in image_item["image_url"]:
                                                image_url = image_item["image_url"]["url"]
                                                if image_url.startswith("data:image/"):
                                                    try:
                                                        header, base64_data = image_url.split(",", 1)
                                                        image_format = header.split("/")[1].split(";")[0]
                                                        if await save_base64_image(base64_data, image_format):
                                                            logger.info(f"API密钥 #{current_index} 成功生成图像")
                                                            return await get_saved_image_info()
                                                    except Exception as e:
                                                        logger.warning(f"解析图像失败: {e}")
                                                        continue

                                    # 如果标准字段没有，尝试从文本内容中提取
                                    elif isinstance(content, str):
                                        base64_pattern = r"data:image/([^;]+);base64,([A-Za-z0-9+/=]+)"
                                        matches = re.findall(base64_pattern, content)
                                        if matches:
                                            image_format, base64_string = matches[0]
                                            if await save_base64_image(base64_string, image_format):
                                                logger.info(f"API密钥 #{current_index} 成功生成图像")
                                                return await get_saved_image_info()

                                logger.info("API调用成功，但响应中未找到可用的图像数据。")
                                return None, None

                            # 处理特定错误码：额度耗尽或速率限制
                            elif response.status == 429 or (response.status == 402 and "insufficient" in str(data).lower()):
                                error_msg = data.get("error", {}).get("message", f"HTTP {response.status}")
                                logger.warning(f"API密钥 #{current_index} 额度耗尽或速率限制: {error_msg}")
                                break  # 立即停止重试，切换到下一个API密钥

                            # 处理其他可重试的错误
                            else:
                                error_msg = data.get("error", {}).get("message", f"HTTP {response.status}")
                                logger.warning(f"OpenRouter API 错误 (重试 {retry_attempt + 1}/{max_retry_attempts}): {error_msg}")
                                if "error" in data:
                                    logger.debug(f"完整错误信息: {data['error']}")
                                if retry_attempt == max_retry_attempts - 1:
                                    logger.error(f"API密钥 #{current_index} 达到最大重试次数")
                                    break

                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    logger.warning(f"网络请求失败 (密钥 #{current_index}, 重试 {retry_attempt + 1}/{max_retry_attempts}): {str(e)}")
                    if retry_attempt == max_retry_attempts - 1:
                        logger.error(f"API密钥 #{current_index} 网络连接达到最大重试次数")
                        break
                except Exception as e:
                    logger.error(f"调用 OpenRouter API 时发生未知异常 (密钥 #{current_index}, 重试 {retry_attempt + 1}/{max_retry_attempts}): {str(e)}")
                    if retry_attempt == max_retry_attempts - 1:
                        logger.error(f"API密钥 #{current_index} 异常达到最大重试次数")
                        break
        
        except Exception as e:
            logger.error(f"处理API密钥 #{current_index} 时发生异常: {str(e)}")
        
        # 如果当前密钥所有重试都失败了，轮换到下一个密钥
        if api_attempt < max_api_attempts - 1:
            await rotate_to_next_api_key(api_keys)
            logger.info(f"切换到下一个API密钥")
    
    logger.error("所有API密钥和重试次数均已耗尽，图像生成失败。")
    return None, None


async def generate_image(prompt: str, api_key: str, model: str = "stabilityai/stable-diffusion-3-5-large", seed: int = None, image_size: str = "1024x1024") -> tuple[str | None, str | None]:
    """
    使用SiliconFlow API生成图像。

    该函数实现了对特定错误码（系统繁忙）的重试机制。

    Args:
        prompt (str): 图像生成的文本提示。
        api_key (str): SiliconFlow API密钥。
        model (str, optional): 模型名称。默认为 "stabilityai/stable-diffusion-3-5-large"。
        seed (int, optional): 随机种子，用于复现结果。如果为None，则随机生成。
        image_size (str, optional): 图像尺寸。默认为 "1024x1024"。

    Returns:
        tuple[str | None, str | None]: 成功时返回包含图像URL和本地路径的元组，失败时返回 (None, None)。
    """
    url = "https://api.siliconflow.cn/v1/images/generations"

    if seed is None:
        seed = random.randint(0, 9999999999)

    payload = {
        "model": model,
        "prompt": prompt,
        "image_size": image_size,
        "seed": seed
    }
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json"
    }

    max_retries = 10
    retry_count = 0
    
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while retry_count < max_retries:
            try:
                async with session.post(url, json=payload, headers=headers) as response:
                    data = await response.json()

                    # 特定错误码处理：系统繁忙，等待后重试
                    if data.get("code") == 50603:
                        logger.warning("系统繁忙，1秒后重试...")
                        await asyncio.sleep(1)
                        retry_count += 1
                        continue

                    if "images" in data:
                        for image in data["images"]:
                            image_url = image["url"]
                            async with session.get(image_url) as img_response:
                                if img_response.status == 200:
                                    # 下载并保存图像
                                    script_dir = Path(__file__).parent.parent
                                    images_dir = script_dir / "images"
                                    images_dir.mkdir(exist_ok=True)
                                    
                                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                                    unique_id = str(uuid.uuid4())[:8]
                                    image_path = images_dir / f"siliconflow_image_{timestamp}_{unique_id}.jpeg"
                                    
                                    async with aiofiles.open(image_path, "wb") as f:
                                        await f.write(await img_response.read())
                                    
                                    logger.info(f"图像已下载: {image_url} -> {image_path}")
                                    return image_url, str(image_path)
                                else:
                                    logger.error(f"下载图像失败: {image_url}")
                                    return None, None
                    else:
                        logger.warning(f"API响应中未找到'images'字段: {data}")
                        return None, None
                        
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.error(f"网络请求失败 (重试 {retry_count + 1}/{max_retries}): {e}")
                retry_count += 1
                if retry_count < max_retries:
                    await asyncio.sleep(2 ** retry_count)  # 指数退避
                else:
                    return None, None
                    
    logger.error(f"达到最大重试次数 ({max_retries})，生成失败。")
    return None, None


if __name__ == "__main__":
    async def create_test_image_base64() -> str:
        """创建一个用于测试的base64编码的小图片。"""
        import io
        from PIL import Image as PILImage, ImageDraw
        
        img = PILImage.new('RGB', (100, 100), color='red')
        draw = ImageDraw.Draw(img)
        draw.text((10, 40), "TEST", fill='white')
        
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        image_bytes = buffer.getvalue()
        
        return base64.b64encode(image_bytes).decode()

    async def main():
        """测试脚本主函数，用于验证图像生成功能。"""
        logger.info("测试图像生成功能...")
        
        # --- 测试 nano-banana 模型 ---
        logger.info("\n=== 测试 nano-banana 模型 ===")
        # 注意：这是一个示例密钥，可能已失效
        nano_banana_api_key = "sk-6Fr314NILmqthjOw9a1AwdLKH987mOBKqqDfpq1Yb26xlIdK"
        nano_banana_prompt = "一只可爱的小猫咪在花园里玩耍，卡通风格"
        
        try:
            image_url, image_path = await generate_image_openrouter(
                nano_banana_prompt,
                [nano_banana_api_key],
                model="nano-banana",
                api_base="https://newapi502.087654.xyz" # 使用自定义API端点
            )
            
            if image_url and image_path:
                logger.info("nano-banana 图像生成成功!")
                logger.info(f"文件路径: {image_path}")
            else:
                logger.error("nano-banana 图像生成失败")
        except Exception as e:
            logger.error(f"nano-banana 测试过程出错: {e}")
        
        # --- 测试 OpenRouter Gemini 多模态功能 ---
        logger.info("\n=== 测试 OpenRouter Gemini 图像生成 ===")
        openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "")
        
        if not openrouter_api_key:
            logger.warning("未设置环境变量 OPENROUTER_API_KEY，跳过 OpenRouter Gemini 测试。")
            return

        logger.info("\n--- 测试 1: 文本到图像生成 ---")
        initial_prompt = "一只可爱的红色小熊猫，数字艺术风格"
        
        image_url, image_path = await generate_image_openrouter(
            initial_prompt,
            [openrouter_api_key],
            model="google/gemini-2.5-flash-image-preview:free"
        )
        
        if image_url and image_path:
            logger.info("初始图像生成成功!")
            logger.info(f"文件路径: {image_path}")
            
            logger.info("\n--- 测试 2: 图像到图像修改 ---")
            try:
                # 读取刚生成的图片并作为下一次请求的输入
                async with aiofiles.open(image_path, 'rb') as f:
                    image_bytes = await f.read()
                generated_image_base64 = base64.b64encode(image_bytes).decode()
                
                logger.info(f"已加载生成图片的base64数据，长度: {len(generated_image_base64)}")
                
                modify_prompt = "将这张图片修改为蓝色主题，并添加一些闪亮的星星装饰"
                input_images = [generated_image_base64]
                
                logger.info("正在使用生成的图片进行修改...")
                modified_url, modified_path = await generate_image_openrouter(
                    modify_prompt,
                    [openrouter_api_key],
                    model="google/gemini-2.5-flash-image-preview:free",
                    input_images=input_images
                )
                
                if modified_url and modified_path:
                    logger.info("图像修改成功!")
                    logger.info(f"修改后文件路径: {modified_path}")
                else:
                    logger.error("图像修改失败")
                    
            except Exception as e:
                logger.error(f"图像修改测试过程出错: {e}")
        else:
            logger.error("初始图像生成失败，无法进行后续修改测试。")

        logger.info("\n--- 测试 3: 检查多模态请求的构造格式 ---")
        try:
            test_image_base64 = await create_test_image_base64()
            
            # 模拟构造多模态请求体
            message_content = [
                {"type": "text", "text": f"Generate a festival blessing image: {initial_prompt}"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{test_image_base64}"}}
            ]
            
            payload = {
                "model": "google/gemini-2.5-flash-image-preview:free",
                "messages": [{"role": "user", "content": message_content}],
                "max_tokens": 1000,
                "temperature": 0.7
            }
            
            logger.info("多模态请求格式构造检查成功。")
            logger.info(f"消息内容项数量: {len(message_content)}")
            logger.info(f"包含文本部分: {any(item['type'] == 'text' for item in message_content)}")
            logger.info(f"包含图片部分: {any(item['type'] == 'image_url' for item in message_content)}")
            logger.info(f"图片URL前缀: {message_content[1]['image_url']['url'][:50]}...")
            
        except Exception as e:
            logger.error(f"请求格式检查出错: {e}")

    asyncio.run(main())