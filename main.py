
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp
import asyncio
import aiohttp
import json
import os
import base64
import binascii
from datetime import datetime, date, timedelta
from chinese_calendar import is_holiday, is_workday
import chinese_calendar as ch_calendar
from cn_bing_translator import Translator

# 内联 utils.ttp.py 的核心逻辑（移除测试部分）
import random
import re
import uuid
from pathlib import Path
import glob
import aiofiles
import struct


class ImageGeneratorState:
    """图像生成器状态管理类，用于处理并发安全"""
    def __init__(self):
        self.last_saved_image = {"url": None, "path": None}
        self.api_key_index = 0
        self._lock = asyncio.Lock()
    
    async def get_next_api_key(self, api_keys):
        """获取下一个可用的API密钥"""
        async with self._lock:
            if not api_keys or not isinstance(api_keys, list):
                raise ValueError("API密钥列表不能为空")
            current_key = api_keys[self.api_key_index % len(api_keys)]
            return current_key
    
    async def rotate_to_next_api_key(self, api_keys):
        """轮换到下一个API密钥"""
        async with self._lock:
            if api_keys and isinstance(api_keys, list) and len(api_keys) > 1:
                self.api_key_index = (self.api_key_index + 1) % len(api_keys)
                logger.info(f"已轮换到下一个API密钥，当前索引: {self.api_key_index}")
    
    async def update_saved_image(self, url, path):
        """更新保存的图像信息"""
        async with self._lock:
            self.last_saved_image = {"url": url, "path": path}
    
    async def get_saved_image_info(self):
        """获取最后保存的图像信息"""
        async with self._lock:
            return self.last_saved_image["url"], self.last_saved_image["path"]


# 全局状态管理实例
_state = ImageGeneratorState()


async def cleanup_old_images(data_dir=None):
    """
    清理超过15分钟的图像文件
    
    Args:
        data_dir (Path): 数据目录路径，如果为None则使用当前脚本目录
    """
    try:
        # 如果没有传入data_dir，使用当前脚本目录
        if data_dir is None:
            script_dir = Path(__file__).parent
            data_dir = script_dir
        
        images_dir = data_dir / "images"

        if not images_dir.exists():
            return

        current_time = datetime.now()
        cutoff_time = current_time - timedelta(minutes=15)

        # 查找images目录下的所有图像文件
        image_patterns = ["blessing_image_*.png", "blessing_image_*.jpg", "blessing_image_*.jpeg"]

        for pattern in image_patterns:
            for file_path in images_dir.glob(pattern):
                try:
                    # 获取文件的修改时间
                    file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime)

                    # 如果文件超过15分钟，删除它
                    if file_mtime < cutoff_time:
                        file_path.unlink()
                        logger.info(f"已清理过期图像: {file_path}")

                except Exception as e:
                    logger.warning(f"清理文件 {file_path} 时出错: {e}")

    except Exception as e:
        logger.error(f"图像清理过程出错: {e}")


async def save_base64_image(base64_string, image_format="png", data_dir=None):
    """
    保存base64图像数据到images文件夹

    Args:
        base64_string (str): base64编码的图像数据
        image_format (str): 图像格式
        data_dir (Path): 数据目录路径，如果为None则使用当前脚本目录

    Returns:
        bool: 是否保存成功
    """
    try:
        # 如果没有传入data_dir，使用当前脚本目录
        if data_dir is None:
            script_dir = Path(__file__).parent
            data_dir = script_dir
        
        images_dir = data_dir / "images"
        # 确保images目录存在
        images_dir.mkdir(exist_ok=True)
        
        # 先清理旧图像
        await cleanup_old_images(data_dir)

        # 解码 base64 数据
        image_data = base64.b64decode(base64_string)

        # 生成唯一文件名（使用时间戳和UUID避免冲突）
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid.uuid4())[:8]
        image_path = images_dir / f"blessing_image_{timestamp}_{unique_id}.{image_format}"

        # 保存图像文件
        async with aiofiles.open(image_path, "wb") as f:
            await f.write(image_data)

        # 获取绝对路径
        abs_path = str(image_path.absolute())
        file_url = f"file://{abs_path}"

        # 更新状态
        await _state.update_saved_image(file_url, str(image_path))

        logger.info(f"图像已保存到: {abs_path}")
        logger.debug(f"文件大小: {len(image_data)} bytes")

        return True

    except binascii.Error as e:
        logger.error(f"Base64 解码失败: {e}")
        return False
    except Exception as e:
        logger.error(f"保存图像文件失败: {e}")
        return False


async def get_next_api_key(api_keys):
    """
    获取下一个可用的API密钥
    
    Args:
        api_keys (list): API密钥列表
        
    Returns:
        str: 当前可用的API密钥
    """
    return await _state.get_next_api_key(api_keys)


async def rotate_to_next_api_key(api_keys):
    """
    轮换到下一个API密钥
    
    Args:
        api_keys (list): API密钥列表
    """
    await _state.rotate_to_next_api_key(api_keys)


async def get_saved_image_info():
    """
    获取最后保存的图像信息

    Returns:
        tuple: (image_url, image_path)
    """
    return await _state.get_saved_image_info()


async def generate_image_openrouter(prompt, api_keys, model="google/gemini-2.5-flash-image-preview:free", max_tokens=1000, input_images=None, api_base=None, max_retry_attempts=3):
    """
    Generate image using OpenRouter API with Gemini model, supports multiple API keys with automatic rotation and retry mechanism

    Args:
        prompt (str): The prompt for image generation
        api_keys (list): List of OpenRouter API keys for rotation
        model (str): Model to use (default: google/gemini-2.5-flash-image-preview:free)
        max_tokens (int): Maximum tokens for the response
        input_images (list): List of base64 encoded input images (optional)
        api_base (str): Custom API base URL (optional, defaults to OpenRouter)
        max_retry_attempts (int): Maximum number of retry attempts per API key (default: 3)

    Returns:
        tuple: (image_url, image_path) or (None, None) if failed
    """
    # 兼容性处理：如果传入单个API密钥字符串，转换为列表
    if isinstance(api_keys, str):
        api_keys = [api_keys]
    
    if not api_keys:
        logger.error("未提供API密钥")
        return None, None
    
    # 支持自定义API base，根据模型类型选择不同的端点
    if api_base:
        if "nano-banana" in model.lower():
            url = f"{api_base.rstrip('/')}/v1/images/generations"
        else:
            url = f"{api_base.rstrip('/')}/v1/chat/completions"
    else:
        url = "https://openrouter.ai/api/v1/chat/completions"
    
    # 尝试每个API密钥，对每个密钥进行重试
    max_api_attempts = len(api_keys)
    
    for api_attempt in range(max_api_attempts):
        try:
            current_api_key = await get_next_api_key(api_keys)
            current_index = (_state.api_key_index % len(api_keys)) + 1
            
            # 对当前API密钥进行多次重试
            for retry_attempt in range(max_retry_attempts):
                try:
                    if retry_attempt > 0:
                        # 重试时的延迟，指数退避
                        delay = min(2 ** retry_attempt, 10)
                        logger.info(f"API密钥 #{current_index} 重试 {retry_attempt + 1}/{max_retry_attempts}，等待 {delay} 秒...")
                        await asyncio.sleep(delay)
                    else:
                        logger.info(f"尝试使用API密钥 #{current_index}")
                    
                    # 构建消息内容，支持输入图片
                    message_content = []
                    
                    # 添加文本内容
                    message_content.append({
                        "type": "text",
                        "text": f"Generate a festival blessing image: {prompt}"
                    })
                    
                    # 如果有输入图片，添加到消息中
                    if input_images:
                        for base64_image in input_images:
                            # 确保base64数据包含正确的data URI格式
                            if not base64_image.startswith('data:image/'):
                                # 假设是PNG格式，添加data URI前缀
                                base64_image = f"data:image/png;base64,{base64_image}"
                            
                            message_content.append({
                                "type": "image_url",
                                "image_url": {
                                    "url": base64_image
                                }
                            })

                    # 根据模型类型构建不同的payload
                    if "nano-banana" in model.lower():
                        # nano-banana使用OpenAI图像生成格式
                        payload = {
                            "model": model,
                            "prompt": prompt,
                            "n": 1,
                            "size": "1024x1024"
                        }
                    else:
                        # Gemini 图像生成构建payload
                        payload = {
                            "model": model,
                            "messages": [
                                {
                                    "role": "user",
                                    "content": message_content if len(message_content) > 1 else f"Generate a festival blessing image: {prompt}"
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

                    # 调试输出：打印请求结构
                    if retry_attempt == 0:  # 只在第一次尝试时打印调试信息
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
                            
                            if retry_attempt == 0:  # 只在第一次尝试时打印详细调试信息
                                logger.debug(f"API响应状态: {response.status}")
                                logger.debug(f"响应数据键: {list(data.keys()) if isinstance(data, dict) else 'Not dict'}")

                            if response.status == 200:
                                # 处理OpenAI格式的图像生成响应 (nano-banana等)
                                if "data" in data and data["data"]:
                                    logger.info(f"收到 {len(data['data'])} 个图像")
                                    
                                    for i, image_item in enumerate(data["data"]):
                                        if "url" in image_item:
                                            # 直接URL格式
                                            image_url = image_item["url"]
                                            
                                            # 下载图像并保存
                                            async with session.get(image_url) as img_response:
                                                if img_response.status == 200:
                                                    # 生成唯一文件名
                                                    script_dir = Path(__file__).parent
                                                    images_dir = script_dir / "images"
                                                    images_dir.mkdir(exist_ok=True)
                                                    
                                                    # 先清理旧图像
                                                    await cleanup_old_images(script_dir)
                                                    
                                                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                                                    unique_id = str(uuid.uuid4())[:8]
                                                    image_path = images_dir / f"blessing_image_{timestamp}_{unique_id}.png"
                                                    
                                                    async with aiofiles.open(image_path, "wb") as f:
                                                        await f.write(await img_response.read())
                                                    
                                                    # 获取绝对路径
                                                    abs_path = str(image_path.absolute())
                                                    file_url = f"file://{abs_path}"
                                                    # 更新状态
                                                    await _state.update_saved_image(file_url, str(image_path))
                                                    
                                                    logger.info(f"API密钥 #{current_index} 成功生成图像: {abs_path}")
                                                    return file_url, str(image_path)
                                                else:
                                                    logger.error(f"下载图像失败: {image_url}")
                                        
                                        elif "b64_json" in image_item:
                                            # Base64格式
                                            base64_data = image_item["b64_json"]
                                            if await save_base64_image(base64_data, "png"):
                                                logger.info(f"API密钥 #{current_index} 成功生成图像 (base64格式)")
                                                return await get_saved_image_info()
                                
                                # 处理Gemini格式的响应
                                elif "choices" in data:
                                    choice = data["choices"][0]
                                    message = choice["message"]
                                    content = message["content"]

                                    # 检查 Gemini 标准的 message.images 字段
                                    if "images" in message and message["images"]:
                                        logger.info(f"Gemini 返回了 {len(message['images'])} 个图像")

                                        for i, image_item in enumerate(message["images"]):
                                            if "image_url" in image_item and "url" in image_item["image_url"]:
                                                image_url = image_item["image_url"]["url"]

                                                # 检查是否是 base64 格式
                                                if image_url.startswith("data:image/"):
                                                    try:
                                                        # 解析 data URI: data:image/png;base64,iVBORw0KGg...
                                                        header, base64_data = image_url.split(",", 1)
                                                        image_format = header.split("/")[1].split(";")[0]

                                                        if await save_base64_image(base64_data, image_format):
                                                            logger.info(f"API密钥 #{current_index} 成功生成图像")
                                                            return await get_saved_image_info()

                                                    except Exception as e:
                                                        logger.warning(f"解析图像 {i+1} 失败: {e}")
                                                        continue

                                    # 如果没有找到标准images字段，尝试在content中查找
                                    elif isinstance(content, str):
                                        # 查找内联的 base64 图像数据
                                        base64_pattern = r"data:image/([^;]+);base64,([A-Za-z0-9+/=]+)"
                                        matches = re.findall(base64_pattern, content)

                                        if matches:
                                            image_format, base64_string = matches[0]
                                            if await save_base64_image(base64_string, image_format):
                                                logger.info(f"API密钥 #{current_index} 成功生成图像")
                                                return await get_saved_image_info()

                                logger.info("API调用成功，但未找到图像数据")
                                return None, None

                            elif response.status == 429 or (response.status == 402 and "insufficient" in str(data).lower()):
                                # 额度耗尽或速率限制，直接尝试下一个密钥，不进行重试
                                error_msg = data.get("error", {}).get("message", f"HTTP {response.status}")
                                logger.warning(f"API密钥 #{current_index} 额度耗尽或速率限制: {error_msg}")
                                break  # 跳出重试循环，尝试下一个API密钥
                            else:
                                # 其他错误，可以重试
                                error_msg = data.get("error", {}).get("message", f"HTTP {response.status}")
                                logger.warning(f"OpenRouter API 错误 (重试 {retry_attempt + 1}/{max_retry_attempts}): {error_msg}")
                                if "error" in data:
                                    logger.debug(f"完整错误信息: {data['error']}")
                                
                                if retry_attempt == max_retry_attempts - 1:
                                    logger.error(f"API密钥 #{current_index} 达到最大重试次数")
                                    break  # 跳出重试循环，尝试下一个API密钥

                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    logger.warning(f"网络请求失败 (密钥 #{current_index}, 重试 {retry_attempt + 1}/{max_retry_attempts}): {str(e)}")
                    if retry_attempt == max_retry_attempts - 1:
                        logger.error(f"API密钥 #{current_index} 网络连接达到最大重试次数")
                        break  # 跳出重试循环，尝试下一个API密钥
                except Exception as e:
                    logger.error(f"调用 OpenRouter API 时发生异常 (密钥 #{current_index}, 重试 {retry_attempt + 1}/{max_retry_attempts}): {str(e)}")
                    if retry_attempt == max_retry_attempts - 1:
                        logger.error(f"API密钥 #{current_index} 异常达到最大重试次数")
                        break  # 跳出重试循环，尝试下一个API密钥
        
        except Exception as e:
            logger.error(f"处理API密钥时发生异常: {str(e)}")
        
        # 尝试下一个API密钥
        if api_attempt < max_api_attempts - 1:
            await rotate_to_next_api_key(api_keys)
            logger.info(f"切换到下一个API密钥")
    
    logger.error("所有API密钥和重试次数已耗尽")
    return None, None


# 内联 utils.file_send_server.py 的逻辑
async def send_file(filename, host, port):
    reader = None
    writer = None
    try:
        reader, writer = await asyncio.open_connection(host, port)
        file_name = os.path.basename(filename)
        file_name_bytes = file_name.encode("utf-8")

        # 发送文件名长度和文件名
        writer.write(struct.pack(">I", len(file_name_bytes)))
        writer.write(file_name_bytes)

        # 发送文件大小
        file_size = os.path.getsize(filename)
        writer.write(struct.pack(">Q", file_size))

        # 发送文件内容
        await writer.drain()
        with open(filename, "rb") as f:
            while True:
                data = f.read(4096)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        logger.info(f"文件 {file_name} 发送成功")

        # 接收接收端发送的文件绝对路径
        try:
            file_abs_path_len_data = await recv_all(reader, 4)
            if not file_abs_path_len_data:
                logger.error("无法接收文件绝对路径长度")
                return None
            file_abs_path_len = struct.unpack(">I", file_abs_path_len_data)[0]

            file_abs_path_data = await recv_all(reader, file_abs_path_len)
            if not file_abs_path_data:
                logger.error("无法接收文件绝对路径")
                return None
            file_abs_path = file_abs_path_data.decode("utf-8")
            logger.info(f"接收端文件绝对路径: {file_abs_path}")
            return file_abs_path
        except (struct.error, UnicodeDecodeError) as e:
            logger.error(f"解析服务器响应失败: {e}")
            return None
        except (ConnectionError, TimeoutError) as e:
            logger.error(f"网络连接错误: {e}")
            return None
            
    except (ConnectionError, TimeoutError) as e:
        logger.error(f"网络连接失败: {e}")
        return None
    except (OSError, IOError) as e:
        logger.error(f"文件操作失败: {e}")
        return None
    except Exception as e:
        logger.error(f"传输失败: {e}")
        return None
    finally:
        # 确保资源被正确释放
        if writer:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception as e:
                logger.warning(f"关闭连接时出错: {e}")


async def recv_all(reader, n):
    """
    安全地接收指定数量的字节
    
    Args:
        reader: AsyncIO stream reader
        n (int): 要接收的字节数
        
    Returns:
        bytes or None: 接收到的数据，失败时返回None
    """
    try:
        data = bytearray()
        while len(data) < n:
            packet = await reader.read(n - len(data))
            if not packet:
                logger.warning(f"连接意外关闭，已接收 {len(data)}/{n} 字节")
                return None
            data.extend(packet)
        return data
    except (ConnectionError, TimeoutError) as e:
        logger.error(f"接收数据时网络错误: {e}")
        return None
    except Exception as e:
        logger.error(f"接收数据时出现未预期的错误: {e}")
        return None


def translate_holiday_name(holiday_name):
    """翻译节日名称，失败时返回原名"""
    if not holiday_name:
        return ''
    try:
        translator = Translator(toLang='zh-Hans')
        result = translator.process(holiday_name)
        return result if result and result != holiday_name else holiday_name
    except Exception as e:
        logger.warning(f"翻译节日名称失败: {e}")
        return holiday_name


def load_holidays_from_json(json_file):
    """从 JSON 文件加载节假日数据"""
    if json_file is None:
        json_file = 'holidays.json'  # 默认
    if os.path.exists(json_file):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('year'), data.get('holidays', [])
        except Exception as e:
            logger.error(f"加载节假日数据失败: {e}")
            return None, []
    return None, []


def save_holidays_to_json(year, holidays, json_file):
    """保存节假日数据到 JSON 文件"""
    if json_file is None:
        json_file = 'holidays.json'  # 默认
    data = {
        'year': year,
        'holidays': holidays
    }
    try:
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"节假日数据已保存到 {json_file}")
    except Exception as e:
        logger.error(f"保存节假日数据失败: {e}")


def get_year_holidays(year, json_file=None):
    """获取指定年份的节假日信息"""
    start_date = date(year, 1, 1)
    end_date = date(year, 12, 31)
    holidays = []
    current_date = start_date
    prev_holiday_name = None

    logger.info(f"获取 {year} 年节假日信息")
    while current_date <= end_date:
        try:
            on_holiday, holiday_name = ch_calendar.get_holiday_detail(current_date)
            is_hol = is_holiday(current_date)
            is_work = is_workday(current_date)
            is_lieu = ch_calendar.is_in_lieu(current_date)
            
            holiday_info = {
                'date': current_date.isoformat(),
                'holiday_name': '',
                'is_holiday': is_hol,
                'is_workday': is_work,
                'is_in_lieu': is_lieu,
                'is_first_day': False  # 新增：标记是否为假期第一天
            }
            
            if on_holiday and holiday_name:
                translated_name = translate_holiday_name(holiday_name)
                holiday_info['holiday_name'] = translated_name
                
                # 连续假期检测：如果前一天不是假期或不同假期，则为第一天
                if current_date == start_date or len(holidays) == 0 or not holidays[-1]['is_holiday'] or holidays[-1]['holiday_name'] != translated_name:
                    holiday_info['is_first_day'] = True
                
                # 输出逻辑：类似原代码，连续假期优化显示
                if translated_name != prev_holiday_name:
                    logger.info(f"{current_date} 是节假日，{translated_name}快乐")
                    if ch_calendar.is_in_lieu(current_date):
                        logger.info(f"{current_date} 是调休")
                    prev_holiday_name = translated_name
                else:
                    logger.debug(f"{current_date} 是{translated_name}假期")
                    if ch_calendar.is_in_lieu(current_date):
                        logger.debug(f"{current_date} 是调休")
            
            holidays.append(holiday_info)
            
        except Exception as e:
            logger.warning(f"处理日期 {current_date} 时出错: {e}")
            # 添加默认记录
            holidays.append({
                'date': current_date.isoformat(),
                'holiday_name': '',
                'is_holiday': False,
                'is_workday': True,
                'is_in_lieu': False,
                'is_first_day': False
            })
        
        current_date += timedelta(days=1)
    
    return holidays


def get_current_year_holidays(json_file=None):
    """获取当前年份节假日"""
    current_year = datetime.now().year
    saved_year, saved_holidays = load_holidays_from_json(json_file)

    if saved_year == current_year and saved_holidays:
        logger.info(f"已加载 {current_year} 年节假日数据，共 {len(saved_holidays)} 条记录。")
        return saved_holidays
    else:
        logger.info(f"未找到 {current_year} 年数据或需更新，正在重新获取...")
        holidays = get_year_holidays(current_year, json_file)
        save_holidays_to_json(current_year, holidays, json_file)
        return holidays


def print_holidays_summary(holidays, year):
    """输出节假日摘要"""
    logger.info(f"{year} 年节假日摘要：")
    total_days = len(holidays)
    holiday_count = sum(1 for h in holidays if h['is_holiday'])
    workday_count = sum(1 for h in holidays if h['is_workday'])
    lieu_count = sum(1 for h in holidays if h['is_in_lieu'])
    first_day_count = sum(1 for h in holidays if h['is_first_day'])
    logger.info(f"总天数：{total_days}")
    logger.info(f"总节假日数：{holiday_count}")
    logger.info(f"总工作日数：{workday_count}")
    logger.info(f"调休日数：{lieu_count}")
    logger.info(f"假期第一天数：{first_day_count}")


def check_single_date(date_input, holidays):
    """检查单个日期（内联使用）"""
    for h in holidays:
        if h['date'] == date_input.isoformat():
            if h['is_holiday']:
                logger.info(f"{date_input} 是假期，{h['holiday_name']}")
            else:
                logger.info(f"{date_input} 是工作日")
            if h['is_in_lieu']:
                logger.info(f"{date_input} 是调休")
            return
    logger.info(f"{date_input} 未找到记录")


@register("SendBlessings", "Cheng-MaoMao", "在节假日送上祝福的插件", "1.0.0")
class SendBlessingsPlugin(Star):
    def __init__(self, context: Context, config):
        super().__init__(context)
        self.config = config
        
        # 确保data目录存在
        data_dir = self.context.get_config().get('data_dir', 'data')
        os.makedirs(data_dir, exist_ok=True)
        
        self.json_file = os.path.join(data_dir, self.config.get('holidays_file', 'holidays.json'))
        
        # 加载 OpenRouter 配置
        self.openrouter_api_keys = config.get("openrouter_api_keys", [])
        self.model_name = config.get("model_name", "google/gemini-2.5-flash-image-preview:free")
        self.max_retry_attempts = config.get("max_retry_attempts", 3)
        self.custom_api_base = config.get("custom_api_base", "").strip()
        
        # 加载 NAP 配置
        self.nap_server_address = config.get("nap_server_address", "localhost")
        self.nap_server_port = config.get("nap_server_port", 3658)
        
        # 加载参考图配置
        self.reference_images_config = config.get("reference_images", {})
        self.reference_images_enabled = self.reference_images_config.get("enabled", False)
        self.reference_image_paths = self.reference_images_config.get("image_paths", [])
        self.max_reference_images = self.reference_images_config.get("max_images", 3)
        
        self.holidays = []
        self.target_sessions = config.get("target_sessions", [])  # 从配置中读取目标会话列表
        self.logger = logger
        
        # 启动初始化任务
        asyncio.create_task(self.initialize())

    async def initialize(self):
        """插件初始化：加载节假日数据并启动每日检查任务"""
        try:
            if not self.config.get('enabled', True):
                self.logger.info("插件未启用，跳过初始化")
                return
            
            # 加载或获取当前年节假日
            self.holidays = get_current_year_holidays(self.json_file)
            print_holidays_summary(self.holidays, datetime.now().year)
            
            # 启动每日祝福检查任务
            asyncio.create_task(self.daily_blessing_checker())
            self.logger.info("节假日祝福插件初始化完成")
        except Exception as e:
            self.logger.error(f"插件初始化失败: {e}")

    @filter.command("blessings reload")
    async def reload_holidays(self, event: AstrMessageEvent):
        """重新加载节假日数据"""
        try:
            self.holidays = get_current_year_holidays(self.json_file)
            yield event.plain_result(f"节假日数据已重新加载，共 {len(self.holidays)} 条记录。")
        except Exception as e:
            self.logger.error(f"重新加载节假日数据失败: {e}")
            yield event.plain_result(f"重新加载失败: {str(e)}")
    
    @filter.command("blessings check")
    async def check_today(self, event: AstrMessageEvent):
        """检查今天是否为节假日第一天"""
        try:
            today = datetime.now().date()
            today_info = None
            for h in self.holidays:
                if h['date'] == today.isoformat():
                    today_info = h
                    break
            if today_info:
                if today_info['is_first_day'] and today_info['is_holiday']:
                    yield event.plain_result(f"今天是 {today_info['holiday_name']} 的第一天！")
                elif today_info['is_holiday']:
                    yield event.plain_result(f"今天是假期，但不是第一天：{today_info['holiday_name']}")
                else:
                    yield event.plain_result("今天不是假期。")
            else:
                yield event.plain_result("未找到今天记录，请重新加载数据。")
        except Exception as e:
            self.logger.error(f"检查今天节假日状态失败: {e}")
            yield event.plain_result(f"检查失败: {str(e)}")
    
    @filter.command("blessings manual")
    async def manual_bless(self, event: AstrMessageEvent, holiday_name: str = None):
        """手动生成并发送祝福（测试用，仅管理员）"""
        if not event.is_admin():
            yield event.plain_result("仅管理员可使用。")
            return
        
        try:
            today = datetime.now().date()
            today_info = next((h for h in self.holidays if h['date'] == today.isoformat()), None)
            if not today_info or not today_info['is_holiday']:
                yield event.plain_result("今天不是假期，无法手动生成。")
                return
            
            if holiday_name is None:
                holiday_name = today_info['holiday_name']
            
            # 生成祝福
            blessing = await self.generate_blessing(holiday_name)
            if not blessing:
                yield event.plain_result("祝福语生成失败。")
                return
            
            # 生成图片
            image_url, image_path = await self.generate_image(blessing, holiday_name)
            if not image_url:
                yield event.plain_result("图片生成失败。")
                return
            
            # 发送到当前会话
            chain = [
                Comp.Plain(blessing),
                Comp.Image.fromFileSystem(image_path) if image_path else Comp.Plain("图片生成失败")
            ]
            yield event.chain_result(chain)
            yield event.plain_result("手动祝福已发送！")
        except Exception as e:
            self.logger.error(f"手动祝福失败: {e}")
            yield event.plain_result(f"手动祝福失败: {str(e)}")

    @filter.command("blessings test")
    async def test_target_sessions(self, event: AstrMessageEvent):
        """测试目标会话列表功能（仅管理员）"""
        if not event.is_admin():
            yield event.plain_result("仅管理员可使用此命令。")
            return
        
        try:
            if not self.target_sessions:
                yield event.plain_result("未配置目标会话列表，请在配置文件中添加 target_sessions。")
                return
            
            # 生成测试祝福
            test_blessing = "🎉 这是一条测试消息，用于验证目标会话配置是否正确。如果您收到此消息，说明配置成功！"
            
            # 生成测试图片（可选）
            test_image_url, test_image_path = None, None
            if self.openrouter_api_keys:
                try:
                    test_image_url, test_image_path = await self.generate_image(test_blessing, "测试")
                except Exception as e:
                    self.logger.warning(f"生成测试图片失败: {e}")
            
            # 构建测试消息链
            if test_image_path:
                test_chain = [
                    Comp.Plain(test_blessing),
                    Comp.Image.fromFileSystem(test_image_path)
                ]
            else:
                test_chain = [Comp.Plain(test_blessing)]
            
            # 发送到所有目标会话
            success_count = 0
            failed_sessions = []
            
            for session in self.target_sessions:
                try:
                    await self.context.send_message(session, test_chain)
                    success_count += 1
                    self.logger.info(f"测试消息已发送到 {session}")
                except Exception as e:
                    failed_sessions.append(session)
                    self.logger.error(f"发送测试消息到 {session} 失败: {e}")
            
            # 返回测试结果
            result_message = f"测试完成！\n"
            result_message += f"✅ 成功发送: {success_count} 个会话\n"
            if failed_sessions:
                result_message += f"❌ 发送失败: {len(failed_sessions)} 个会话\n"
                result_message += f"失败会话: {', '.join(failed_sessions[:3])}"
                if len(failed_sessions) > 3:
                    result_message += f" 等{len(failed_sessions)}个"
            
            yield event.plain_result(result_message)
            
        except Exception as e:
            self.logger.error(f"测试目标会话失败: {e}")
            yield event.plain_result(f"测试失败: {str(e)}")

    async def load_reference_images(self):
        """加载并转换参考图片为base64格式"""
        if not self.reference_images_enabled:
            return []
        
        base64_images = []
        valid_paths = self.validate_image_paths()
        
        for image_path in valid_paths[:self.max_reference_images]:
            try:
                base64_data = await self.convert_image_to_base64(image_path)
                if base64_data:
                    base64_images.append(base64_data)
            except Exception as e:
                self.logger.warning(f"加载参考图 {image_path} 失败: {e}")
        
        if base64_images:
            self.logger.info(f"成功加载 {len(base64_images)} 张参考图")
        
        return base64_images

    def validate_image_paths(self):
        """验证图片路径有效性"""
        valid_paths = []
        for path in self.reference_image_paths:
            # 支持相对路径和绝对路径
            if os.path.isabs(path):
                full_path = path
            else:
                full_path = os.path.join(os.path.dirname(__file__), path)
            
            if os.path.exists(full_path) and os.path.isfile(full_path):
                valid_paths.append(full_path)
            else:
                self.logger.warning(f"参考图路径不存在: {path}")
        return valid_paths

    async def convert_image_to_base64(self, image_path: str):
        """转换图片为base64格式"""
        try:
            async with aiofiles.open(image_path, 'rb') as f:
                image_data = await f.read()
            
            # 检查文件大小，如果太大则给出警告
            if len(image_data) > 5 * 1024 * 1024:  # 5MB
                self.logger.warning(f"图片 {image_path} 过大 ({len(image_data)/1024/1024:.1f}MB)，建议压缩后使用")
            
            base64_data = base64.b64encode(image_data).decode('utf-8')
            
            # 检测图片格式并添加正确的MIME类型
            ext = os.path.splitext(image_path)[1].lower()
            if ext in ['.png']:
                mime_type = 'image/png'
            elif ext in ['.jpg', '.jpeg']:
                mime_type = 'image/jpeg'
            elif ext in ['.gif']:
                mime_type = 'image/gif'
            elif ext in ['.webp']:
                mime_type = 'image/webp'
            else:
                mime_type = 'image/png'  # 默认
                self.logger.warning(f"未知图片格式 {ext}，使用默认PNG格式")
            
            return f"data:{mime_type};base64,{base64_data}"
            
        except Exception as e:
            self.logger.error(f"转换图片 {image_path} 为base64失败: {e}")
            return None

    def build_reference_prompt(self, blessing: str, holiday_name: str, has_reference: bool):
        """构建包含参考图信息的提示词"""
        base_prompt = f"{holiday_name} 节日祝福海报，温暖喜庆风格，包含文字：{blessing[:50]}...，节日元素如灯笼/花朵/雪花等，高质量，卡通插画风格，节日氛围浓厚，中文文字清晰可见"
        
        if has_reference:
            reference_prompt = f"请基于提供的参考图片中的人物、场景和元素，创作{base_prompt}。保持参考图中人物的特征和风格，将其融入到节日场景中，确保画面和谐统一，节日氛围浓厚。如果参考图中有人物，请保持其外观特征；如果有特定场景，请将节日元素自然融入其中。"
            return reference_prompt
        else:
            return base_prompt

    async def terminate(self):
        """插件销毁：清理资源"""
        self.logger.info("节假日祝福插件已销毁")
    
    async def daily_blessing_checker(self):
        """每日检查是否需要发送祝福"""
        while True:
            try:
                await asyncio.sleep(3600 * 24)  # 每天检查一次（可调整为更精确的时间）
                today = datetime.now().date()
                today_info = next((h for h in self.holidays if h['date'] == today.isoformat()), None)
                
                if today_info and today_info['is_first_day'] and today_info['is_holiday'] and self.config.get('enabled', True):
                    holiday_name = today_info['holiday_name']
                    self.logger.info(f"检测到假期第一天：{holiday_name}")
                    
                    # 生成祝福语
                    blessing = await self.generate_blessing(holiday_name)
                    if not blessing:
                        self.logger.error("祝福语生成失败，跳过发送")
                        continue
                    
                    # 生成图片
                    image_url, image_path = await self.generate_image(blessing, holiday_name)
                    if not image_url:
                        self.logger.error("图片生成失败，跳过发送")
                        continue
                    
                    # 构建消息链
                    chain = [
                        Comp.Plain(blessing),
                        Comp.Image.fromFileSystem(image_path) if image_path else Comp.Plain("图片生成失败")
                    ]
                    
                    # 发送到目标会话
                    sent_count = 0
                    for session in self.target_sessions:
                        try:
                            # 直接发送消息链，不需要包装在MessageChain中
                            await self.context.send_message(session, chain)
                            sent_count += 1
                            self.logger.info(f"祝福消息已发送到 {session}")
                        except Exception as e:
                            self.logger.error(f"发送到 {session} 失败: {e}")
                    
                    if sent_count > 0:
                        self.logger.info(f"今日祝福已发送到 {sent_count} 个会话")
                    else:
                        self.logger.warning("无目标会话或发送失败，祝福未发送")
                
                # 每年检查是否需要更新节假日数据（例如12月31日）
                if today.month == 12 and today.day == 31:
                    next_year = today.year + 1
                    self.holidays = get_year_holidays(next_year, self.json_file)
                    save_holidays_to_json(next_year, self.holidays, self.json_file)
                    self.logger.info(f"{next_year}年节假日数据已预加载")
                
            except Exception as e:
                self.logger.error(f"每日检查出错: {e}")
                await asyncio.sleep(3600)  # 出错时1小时后重试
    
    async def generate_blessing(self, holiday_name: str) -> str:
        """生成节日祝福语"""
        try:
            # 使用简单的祝福语模板，避免依赖外部API
            blessing_templates = {
                "春节": "新春快乐！祝您在新的一年里身体健康，工作顺利，阖家幸福！",
                "元旦": "元旦快乐！新年新气象，祝您在新的一年里万事如意，心想事成！",
                "中秋节": "中秋节快乐！月圆人团圆，祝您和家人团团圆圆，幸福美满！",
                "国庆节": "国庆节快乐！祝愿祖国繁荣昌盛，祝您节日愉快，身体健康！",
                "劳动节": "劳动节快乐！向所有辛勤工作的人们致敬，祝您节日愉快！",
                "端午节": "端午节快乐！粽子香，艾叶长，祝您身体健康，平安吉祥！",
                "清明节": "清明时节，缅怀先人，珍惜当下，祝您身体健康，工作顺利！",
                "元宵节": "元宵节快乐！花好月圆人团圆，祝您家庭幸福，事业有成！"
            }
            
            # 尝试使用LLM生成个性化祝福语
            try:
                provider = self.context.get_using_provider()
                if provider:
                    prompt = f"请为{holiday_name}生成一段温暖、简短的中文祝福语（50-100字），要体现节日特色和美好祝愿。"
                    
                    resp = await provider.text_chat(
                        prompt=prompt,
                        system_prompt="你是一个专业的节日祝福生成器，输出仅为祝福语文本，不要添加额外解释。"
                    )
                    
                    if resp and resp.completion_text:
                        blessing = resp.completion_text.strip()
                        if blessing and len(blessing) > 10:  # 确保生成的祝福语有意义
                            return blessing
            except Exception as e:
                self.logger.warning(f"LLM生成祝福语失败，使用模板: {e}")
            
            # 回退到模板祝福语
            for key in blessing_templates:
                if key in holiday_name:
                    return blessing_templates[key]
            
            # 通用祝福语
            return f"{holiday_name}快乐！祝您节日愉快，身体健康，工作顺利，阖家幸福！"
            
        except Exception as e:
            self.logger.error(f"生成祝福语失败: {e}")
            return f"{holiday_name}快乐！祝您节日愉快！"
    
    async def generate_image(self, blessing: str, holiday_name: str) -> tuple:
        """生成节日祝福图片，支持参考图功能"""
        try:
            if not self.openrouter_api_keys:
                self.logger.warning("未配置OpenRouter API密钥，跳过图片生成")
                return None, None
            
            # 加载参考图
            reference_images = await self.load_reference_images()
            
            # 构建图像生成提示词
            prompt = self.build_reference_prompt(blessing, holiday_name, bool(reference_images))
            
            # 调用内联的生成函数
            image_url, image_path = await generate_image_openrouter(
                prompt=prompt,
                api_keys=self.openrouter_api_keys,
                model=self.model_name,
                input_images=reference_images,  # 传入参考图
                max_retry_attempts=self.max_retry_attempts,
                api_base=self.custom_api_base if self.custom_api_base else None
            )
            
            if not image_url or not image_path:
                self.logger.error("图片生成失败")
                return None, None
            
            # 处理 NAP 文件传输
            if self.nap_server_address and self.nap_server_address != "localhost":
                try:
                    transferred_path = await send_file(image_path, host=self.nap_server_address, port=self.nap_server_port)
                    if transferred_path:
                        image_path = transferred_path
                        self.logger.info(f"NAP 传输成功: {image_path}")
                    else:
                        self.logger.warning("NAP 传输失败，使用本地路径")
                except Exception as e:
                    self.logger.warning(f"NAP 传输失败，回退本地路径: {e}")
            
            self.logger.info(f"节日图片生成成功: {image_path}")
            return image_url, image_path
            
        except Exception as e:
            self.logger.error(f"生成图片失败: {e}")
            return None, None
