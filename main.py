from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.platform import MessageSession, MessageType
import astrbot.api.message_components as Comp
import asyncio
import aiohttp
import aiofiles
import json
import os
import base64
import binascii
from datetime import datetime, date, timedelta
from chinese_calendar import is_holiday, is_workday
import chinese_calendar as ch_calendar
from cn_bing_translator import Translator
from .utils.ttp import generate_image_openrouter
from .utils.file_send_server import send_file



def translate_holiday_name(holiday_name: str) -> str:
    """
    使用必应翻译将英文节假日名称翻译为中文。

    Args:
        holiday_name (str): 英文节假日名称。

    Returns:
        str: 翻译后的中文名称，失败时返回原名称。
    """
    if not holiday_name:
        return ''
    try:
        translator = Translator(toLang='zh-Hans')
        result = translator.process(holiday_name)
        return result if result and result != holiday_name else holiday_name
    except Exception as e:
        logger.warning(f"翻译节日名称 '{holiday_name}' 失败: {e}")
        return holiday_name


def load_holidays_from_json(json_file: str) -> tuple[int | None, list]:
    """
    从JSON文件加载缓存的节假日数据。

    Args:
        json_file (str): 缓存文件的路径。

    Returns:
        tuple[int | None, list]: 包含年份和节假日列表的元组，失败则返回 (None, [])。
    """
    if json_file is None:
        json_file = 'holidays.json'
    if os.path.exists(json_file):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('year'), data.get('holidays', [])
        except Exception as e:
            logger.error(f"从 {json_file} 加载节假日数据失败: {e}")
            return None, []
    return None, []


def save_holidays_to_json(year: int, holidays: list, json_file: str):
    """
    将节假日数据保存到JSON文件。

    Args:
        year (int): 数据对应的年份。
        holidays (list): 全年的节假日信息列表。
        json_file (str): 目标JSON文件的路径。
    """
    if json_file is None:
        json_file = 'holidays.json'
    data = {'year': year, 'holidays': holidays}
    try:
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"节假日数据已保存到 {json_file}")
    except Exception as e:
        logger.error(f"保存节假日数据到 {json_file} 失败: {e}")


def get_year_holidays(year: int, json_file: str = None) -> list:
    """
    获取指定年份的完整节假日信息。

    遍历该年的每一天，使用 `chinese_calendar` 库确定日期类型，
    并标记出每个连续假期的第一天。

    Args:
        year (int): 要查询的年份。
        json_file (str, optional): 仅用于传递，无实际作用。

    Returns:
        list: 包含全年每一天详细信息的字典列表。
    """
    start_date = date(year, 1, 1)
    end_date = date(year, 12, 31)
    holidays = []
    current_date = start_date
    prev_holiday_name = None

    logger.info(f"正在获取 {year} 年的节假日信息...")
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
                'is_first_day': False
            }
            
            if on_holiday and holiday_name:
                translated_name = translate_holiday_name(holiday_name)
                holiday_info['holiday_name'] = translated_name
                
                # 检测是否为连续假期的第一天
                if current_date == start_date or not holidays or not holidays[-1]['is_holiday'] or holidays[-1]['holiday_name'] != translated_name:
                    holiday_info['is_first_day'] = True
                
                # 优化日志输出，仅在假期变化时打印
                if translated_name != prev_holiday_name:
                    logger.info(f"{current_date} 是节假日: {translated_name}")
                    if is_lieu:
                        logger.info(f"  -> {current_date} 是调休日")
                    prev_holiday_name = translated_name
            
            holidays.append(holiday_info)
            
        except Exception as e:
            logger.warning(f"处理日期 {current_date} 时出错: {e}")
            # 出错时添加默认记录以保证数据完整性
            holidays.append({
                'date': current_date.isoformat(), 'holiday_name': '', 'is_holiday': False,
                'is_workday': True, 'is_in_lieu': False, 'is_first_day': False
            })
        
        current_date += timedelta(days=1)
    
    return holidays


def get_current_year_holidays(json_file: str = None) -> list:
    """
    获取当前年份的节假日数据，优先从缓存加载。

    Args:
        json_file (str, optional): 缓存文件的路径。

    Returns:
        list: 当前年份的节假日数据列表。
    """
    current_year = datetime.now().year
    saved_year, saved_holidays = load_holidays_from_json(json_file)

    if saved_year == current_year and saved_holidays:
        logger.info(f"已从缓存加载 {current_year} 年节假日数据，共 {len(saved_holidays)} 条记录。")
        return saved_holidays
    else:
        logger.info(f"未找到 {current_year} 年的缓存或数据已过时，正在重新获取...")
        holidays = get_year_holidays(current_year, json_file)
        save_holidays_to_json(current_year, holidays, json_file)
        return holidays


def print_holidays_summary(holidays: list, year: int):
    """
    在日志中输出指定年份节假日数据的统计摘要。

    Args:
        holidays (list): 节假日数据列表。
        year (int): 对应的年份。
    """
    logger.info(f"--- {year} 年节假日摘要 ---")
    total_days = len(holidays)
    holiday_count = sum(1 for h in holidays if h['is_holiday'])
    workday_count = sum(1 for h in holidays if h['is_workday'])
    lieu_count = sum(1 for h in holidays if h['is_in_lieu'])
    first_day_count = sum(1 for h in holidays if h['is_first_day'])
    logger.info(f"总天数: {total_days}")
    logger.info(f"总节假日天数: {holiday_count}")
    logger.info(f"总工作日天数: {workday_count}")
    logger.info(f"其中调休日数: {lieu_count}")
    logger.info(f"假期第一天总数: {first_day_count}")
    logger.info("--------------------------")


def check_single_date(date_input: date, holidays: list):
    """
    在日志中打印单个日期的节假日状态（主要用于调试）。

    Args:
        date_input (date): 要查询的日期。
        holidays (list): 已加载的节假日数据列表。
    """
    for h in holidays:
        if h['date'] == date_input.isoformat():
            if h['is_holiday']:
                logger.info(f"查询结果: {date_input} 是假期 - {h['holiday_name']}")
            else:
                logger.info(f"查询结果: {date_input} 是工作日")
            if h['is_in_lieu']:
                logger.info(f"  -> (调休)")
            return
    logger.info(f"查询结果: 在 {date_input.year} 年的记录中未找到 {date_input}。")


@register("SendBlessings", "Cheng-MaoMao", "在节假日自动送上祝福并配图", "1.0.5")
class SendBlessingsPlugin(Star):
    """
    自动发送节假日祝福插件。

    继承自 `astrbot.api.star.Star`。
    """
    def __init__(self, context: Context, config):
        """
        插件初始化。

        Args:
            context (Context): AstrBot 框架提供的上下文对象，用于访问核心功能。
            config: 插件的配置对象，由 `_conf_schema.json` 定义。
        """
        super().__init__(context)
        self.config = config
        
        # 确保插件数据目录存在
        data_dir = self.context.get_config().get('data_dir', 'data')
        os.makedirs(data_dir, exist_ok=True)
        
        self.json_file = os.path.join(data_dir, self.config.get('holidays_file', 'holidays.json'))
        
        # 加载图像生成 (OpenRouter) 相关配置
        self.openrouter_api_keys = config.get("openrouter_api_keys", [])
        self.model_name = config.get("model_name", "google/gemini-2.5-flash-image-preview:free")
        self.max_retry_attempts = config.get("max_retry_attempts", 3)
        self.custom_api_base = config.get("custom_api_base", "").strip()
        
        # 加载文件传输服务器 (NAP) 相关配置
        self.nap_server_address = config.get("nap_server_address", "localhost")
        self.nap_server_port = config.get("nap_server_port", 3658)
        
        # 加载参考图相关配置
        self.reference_images_config = config.get("reference_images", {})
        self.reference_images_enabled = self.reference_images_config.get("enabled", False)
        self.reference_image_paths = self.reference_images_config.get("image_paths", [])
        self.max_reference_images = self.reference_images_config.get("max_images", 3)
        
        self.holidays = []
        self.logger = logger
        
        # 在后台启动异步初始化任务
        asyncio.create_task(self.initialize())

    async def initialize(self):
        """
        异步初始化插件，加载数据并启动后台任务。
        """
        try:
            if not self.config.get('enabled', True):
                self.logger.info("插件已在配置中禁用，跳过初始化。")
                return
            
            # 加载或获取当前年份的节假日数据
            self.holidays = get_current_year_holidays(self.json_file)
            print_holidays_summary(self.holidays, datetime.now().year)
            
            # 启动每日祝福检查的后台循环任务
            asyncio.create_task(self.daily_blessing_checker())
            self.logger.info("节假日祝福插件初始化完成。")
        except Exception as e:
            self.logger.error(f"插件初始化失败: {e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("blessings reload")
    async def reload_holidays(self, event: AstrMessageEvent):
        """
        [管理员指令] 重新加载节假日数据。
        """
        try:
            self.holidays = get_current_year_holidays(self.json_file)
            yield event.plain_result(f"节假日数据已重新加载，共 {len(self.holidays)} 条记录。")
        except Exception as e:
            self.logger.error(f"重新加载节假日数据失败: {e}")
            yield event.plain_result(f"重新加载失败: {str(e)}")
    
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("blessings check")
    async def check_today(self, event: AstrMessageEvent):
        """
        [管理员指令] 检查今天的日期状态。
        """
        try:
            today = datetime.now().date()
            today_info = next((h for h in self.holidays if h['date'] == today.isoformat()), None)
            
            if today_info:
                if today_info['is_first_day'] and today_info['is_holiday']:
                    yield event.plain_result(f"今天是 {today_info['holiday_name']} 的第一天！")
                elif today_info['is_holiday']:
                    yield event.plain_result(f"今天是假期，但不是第一天：{today_info['holiday_name']}")
                else:
                    yield event.plain_result("今天不是假期。")
            else:
                yield event.plain_result("未在数据中找到今天，请尝试使用 'blessings reload' 指令。")
        except Exception as e:
            self.logger.error(f"检查今天节假日状态失败: {e}")
            yield event.plain_result(f"检查失败: {str(e)}")
    
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("blessings manual")
    async def manual_bless(self, event: AstrMessageEvent, holiday_name: str = None):
        """
        [管理员指令] 手动触发一次祝福生成和发送流程（仅用于测试）。

        如果今天不是假期，此指令将无法触发。
        """
        try:
            today = datetime.now().date()
            today_info = next((h for h in self.holidays if h['date'] == today.isoformat()), None)
            if not today_info or not today_info['is_holiday']:
                yield event.plain_result("今天不是假期，无法手动触发祝福。")
                return
            
            if holiday_name is None:
                holiday_name = today_info['holiday_name']
            
            # 1. 生成祝福语
            blessing = await self.generate_blessing(holiday_name)
            if not blessing:
                yield event.plain_result("祝福语生成失败。")
                return
            
            # 2. 生成图片
            image_url, image_path = await self.generate_image(blessing, holiday_name)
            if not image_url:
                yield event.plain_result("图片生成失败。")
                return
            
            # 3. 发送到当前会话
            chain = [
                Comp.Plain(blessing),
                Comp.Image.fromFileSystem(image_path) if image_path else Comp.Plain("图片生成失败")
            ]
            yield event.chain_result(chain)
            yield event.plain_result("手动祝福已发送到当前会话！")
        except Exception as e:
            self.logger.error(f"手动祝福失败: {e}")
            yield event.plain_result(f"手动祝福失败: {str(e)}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("blessings test")
    async def test_target_sessions(self, event: AstrMessageEvent):
        """
        [管理员指令] 向所有已连接的群组和好友发送一条测试消息，以验证广播功能。
        """
        try:
            yield event.plain_result("正在开始广播功能测试... 此功能仅为 NapCatQQ 设计，请确保您正在使用 aiocqhttp 适配器。")
            
            platform = self.context.get_platform(filter.PlatformAdapterType.AIOCQHTTP)
            if not platform or not hasattr(platform, "get_client"):
                yield event.plain_result("错误：无法获取 aiocqhttp 平台适配器。测试中止。")
                return

            client = platform.get_client()
            if not client:
                yield event.plain_result("无法获取 aiocqhttp 客户端实例。")
                return

            # 获取好友和群组列表
            friend_list = await client.api.call_action("get_friend_list")
            group_list = await client.api.call_action("get_group_list")

            if not friend_list and not group_list:
                yield event.plain_result("未能获取到任何好友或群组列表。")
                return

            test_blessing = "🎉 这是一条广播功能测试消息。如果您收到此消息，说明插件可以正常向您发送祝福！"
            
            test_image_url, test_image_path = None, None
            if self.openrouter_api_keys:
                try:
                    test_image_url, test_image_path = await self.generate_image(test_blessing, "测试")
                except Exception as e:
                    self.logger.warning(f"生成测试图片失败: {e}")

            test_chain = [Comp.Plain(test_blessing)]
            if test_image_path:
                test_chain.append(Comp.Image.fromFileSystem(test_image_path))

            success_count = 0
            failed_sessions_info = []
            
            # 发送到所有好友
            for friend in friend_list:
                user_id = friend.get('user_id')
                if not user_id: continue
                session = MessageSession(
                    platform_name=\"aiocqhttp\",
                    message_type=MessageType.FRIEND_MESSAGE,
                    session_id=str(user_id)
                )
                try:
                    await self.context.send_message(session, test_chain)
                    success_count += 1
                    self.logger.info(f"测试消息已发送到用户 {user_id}")
                    await asyncio.sleep(1) # 避免发送过快
                except Exception as e:
                    failed_sessions_info.append(f"用户 {user_id} (原因: {e})")
                    self.logger.error(f"发送测试消息到用户 {user_id} 失败: {e}")

            # 发送到所有群组
            for group in group_list:
                group_id = group.get('group_id')
                if not group_id: continue
                session = MessageSession(
                    platform_name=\"aiocqhttp\",
                    message_type=MessageType.GROUP_MESSAGE,
                    session_id=str(group_id)
                )
                try:
                    await self.context.send_message(session, test_chain)
                    success_count += 1
                    self.logger.info(f"测试消息已发送到群组 {group_id}")
                    await asyncio.sleep(1) # 避免发送过快
                except Exception as e:
                    failed_sessions_info.append(f"群组 {group_id} (原因: {e})")
                    self.logger.error(f"发送测试消息到群组 {group_id} 失败: {e}")

            total_targets = len(friend_list) + len(group_list)
            result_message = f"测试完成！共扫描到 {total_targets} 个目标。\n✅ 成功发送: {success_count} 个会话\n"
            if failed_sessions_info:
                result_message += f"❌ 发送失败: {len(failed_sessions_info)} 个会话\n"
                result_message += f"失败详情: {', '.join(failed_sessions_info[:3])}"
                if len(failed_sessions_info) > 3:
                    result_message += "..."

            yield event.plain_result(result_message)

        except Exception as e:
            self.logger.error(f"测试目标会话失败: {e}")
            yield event.plain_result(f"测试失败: {str(e)}")

    async def load_reference_images(self) -> list[str]:
        """
        加载并转换配置文件中指定的参考图片为base64格式。

        Returns:
            list[str]: base64编码的图像数据URI列表。
        """
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

    def validate_image_paths(self) -> list[str]:
        """
        验证参考图片路径的有效性，支持相对和绝对路径。

        Returns:
            list[str]: 有效的图片绝对路径列表。
        """
        valid_paths = []
        for path in self.reference_image_paths:
            full_path = path if os.path.isabs(path) else os.path.join(os.path.dirname(__file__), path)
            
            if os.path.exists(full_path) and os.path.isfile(full_path):
                valid_paths.append(full_path)
            else:
                self.logger.warning(f"配置的参考图路径不存在: {path}")
        return valid_paths

    async def convert_image_to_base64(self, image_path: str) -> str | None:
        """
        将单个图片文件转换为base64编码的data URI。

        Args:
            image_path (str): 图片文件的路径。

        Returns:
            str | None: 成功时返回data URI字符串，失败时返回None。
        """
        try:
            async with aiofiles.open(image_path, 'rb') as f:
                image_data = await f.read()
            
            if len(image_data) > 5 * 1024 * 1024:  # 5MB
                self.logger.warning(f"参考图 {image_path} 过大 ({len(image_data)/1024/1024:.1f}MB)，可能导致API请求失败。")
            
            base64_data = base64.b64encode(image_data).decode('utf-8')
            
            # 根据文件扩展名确定MIME类型
            ext = os.path.splitext(image_path)[1].lower()
            mime_map = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp'}
            mime_type = mime_map.get(ext, 'image/png')
            if ext not in mime_map:
                self.logger.warning(f"未知的参考图格式 '{ext}'，将使用默认的 'image/png' MIME类型。")
            
            return f"data:{mime_type};base64,{base64_data}"
            
        except Exception as e:
            self.logger.error(f"转换图片 {image_path} 为base64失败: {e}")
            return None

    def build_reference_prompt(self, blessing: str, holiday_name: str, has_reference: bool) -> str:
        """
        构建用于图像生成的最终提示词。

        - 包含核心主题、风格和节日元素。
        - 强制加入负面提示词，以避免生成任何文字、旗帜或宗教符号。
        - 如果有参考图，会调整提示词以指导模型在参考图基础上创作。

        Args:
            blessing (str): 生成的祝福语（当前版本未使用，但保留以备将来扩展）。
            holiday_name (str): 节日名称。
            has_reference (bool): 是否有参考图。

        Returns:
            str: 构建好的最终提示词。
        """
        base_prompt = f"{holiday_name} festival celebration, warm and festive style, cartoon illustration. Incorporate holiday elements like lanterns, flowers, or snowflakes. High quality, rich festive atmosphere."
        negative_prompt = "IMPORTANT: Do NOT generate any text, words, letters, characters, flags, national emblems, or religious symbols. The image must be purely visual and contain no writing."

        if has_reference:
            return (
                f"Based on the provided reference image(s), create a new artwork with the following theme: '{base_prompt}'. "
                f"Maintain the characters, scenes, and elements from the reference image, integrating them into the festive scene. "
                f"Ensure the final image is harmonious and full of festive spirit. "
                f"{negative_prompt}"
            )
        else:
            return f"{base_prompt} {negative_prompt}"

    async def terminate(self):
        """
        插件终止时调用的清理方法。
        """
        self.logger.info("节假日祝福插件已销毁。")
    
    async def daily_blessing_checker(self):
        """
        每日检查并向所有群组和好友发送祝福的核心后台任务。
        """
        while True:
            try:
                # 每天检查一次
                await asyncio.sleep(3600 * 24)
                today = datetime.now().date()
                today_info = next((h for h in self.holidays if h['date'] == today.isoformat()), None)
                
                if today_info and today_info['is_first_day'] and today_info['is_holiday'] and self.config.get('enabled', True):
                    holiday_name = today_info['holiday_name']
                    self.logger.info(f"检测到假期第一天：{holiday_name}，开始发送祝福...")
                    
                    # 1. 生成祝福语
                    blessing = await self.generate_blessing(holiday_name)
                    if not blessing:
                        self.logger.error("祝福语生成失败，跳过本次发送。")
                        continue
                    
                    # 2. 生成图片
                    image_url, image_path = await self.generate_image(blessing, holiday_name)
                    if not image_url:
                        self.logger.error("图片生成失败，跳过本次发送。")
                        continue
                    
                    # 3. 构建消息链
                    chain = [
                        Comp.Plain(blessing),
                        Comp.Image.fromFileSystem(image_path) if image_path else Comp.Plain("(图片生成失败)")
                    ]
                    
                    # 4. 发送到所有目标会话
                    platform = self.context.get_platform(filter.PlatformAdapterType.AIOCQHTTP)
                    if not platform or not hasattr(platform, "get_client"):
                        self.logger.error("无法获取 aiocqhttp 平台实例（可能未使用 NapCatQQ），无法发送广播祝福。")
                        continue

                    client = platform.get_client()
                    if not client:
                        self.logger.error("无法获取 aiocqhttp 客户端实例，无法发送广播祝福。")
                        continue
                    
                    friend_list = await client.api.call_action("get_friend_list")
                    group_list = await client.api.call_action("get_group_list")

                    sent_count = 0
                    # 发送到好友
                    for friend in friend_list:
                        user_id = friend.get('user_id')
                        if not user_id: continue
                        session = MessageSession(
                            platform_name=\"aiocqhttp\",
                            message_type=MessageType.FRIEND_MESSAGE,
                            session_id=str(user_id)
                        )
                        try:
                            await self.context.send_message(session, chain)
                            sent_count += 1
                            self.logger.info(f"祝福消息已发送到用户 {user_id}")
                            await asyncio.sleep(5) # 减慢发送速度
                        except Exception as e:
                            self.logger.error(f"发送祝福到用户 {user_id} 失败: {e}")
                    
                    # 发送到群组
                    for group in group_list:
                        group_id = group.get('group_id')
                        if not group_id: continue
                        session = MessageSession(
                            platform_name=\"aiocqhttp\",
                            message_type=MessageType.GROUP_MESSAGE,
                            session_id=str(group_id)
                        )
                        try:
                            await self.context.send_message(session, chain)
                            sent_count += 1
                            self.logger.info(f"祝福消息已发送到群组 {group_id}")
                            await asyncio.sleep(5) # 减慢发送速度
                        except Exception as e:
                            self.logger.error(f"发送祝福到群组 {group_id} 失败: {e}")

                    if sent_count > 0:
                        self.logger.info(f"今日({holiday_name})祝福已成功发送到 {sent_count} 个会话。")
                    else:
                        self.logger.warning("未能获取到任何好友或群组，今日祝福未发送。")
                
                # 在每年年底预加载下一年的数据
                if today.month == 12 and today.day == 31:
                    next_year = today.year + 1
                    self.logger.info(f"正在预加载 {next_year} 年的节假日数据...")
                    self.holidays = get_year_holidays(next_year, self.json_file)
                    save_holidays_to_json(next_year, self.holidays, self.json_file)
                
            except Exception as e:
                self.logger.error(f"每日祝福检查任务发生严重错误: {e}")
                await asyncio.sleep(3600)  # 出错时等待1小时后重试
    
    async def generate_blessing(self, holiday_name: str) -> str:
        """
        生成节日祝福语。

        优先尝试使用配置的LLM提供商生成个性化祝福。如果失败，则回退到
        预设的模板祝福语。

        Args:
            holiday_name (str): 节日名称。

        Returns:
            str: 生成的祝福语。
        """
        try:
            # 尝试使用LLM生成
            try:
                provider = self.context.get_using_provider()
                if provider:
                    prompt = f"请为“{holiday_name}”这个节日生成一段温暖、简短的中文祝福语（50-100字），要体现节日特色和美好祝愿。"
                    
                    resp = await provider.text_chat(
                        prompt=prompt,
                        system_prompt="你是一个专业的节日祝福生成器，你的回答应该只包含祝福语文本本身，不要添加任何额外的解释或引言。"
                    )
                    
                    if resp and resp.completion_text:
                        blessing = resp.completion_text.strip()
                        if blessing and len(blessing) > 10:
                            self.logger.info(f"成功使用LLM为 {holiday_name} 生成祝福语。")
                            return blessing
            except Exception as e:
                self.logger.warning(f"LLM生成祝福语失败，将使用预设模板: {e}")
            
            # LLM失败或未配置，回退到模板
            blessing_templates = {
                "春节": "新春快乐！祝您在新的一年里龙马精神，万事如意，阖家幸福！",
                "元旦": "元旦快乐！新年新气象，愿您在新的一年里梦想成真，步步高升！",
                "中秋节": "中秋节快乐！月圆人团圆，祝您和家人幸福美满，共享天伦之乐！",
                "国庆节": "国庆节快乐！祝愿我们伟大的祖国繁荣昌盛，祝您节日愉快，笑口常开！",
                "劳动节": "劳动节快乐！向所有辛勤的劳动者致敬，祝您度过一个轻松愉快的假期！",
                "端午节": "端午安康！愿粽叶的清香带给您好运，祝您身体健康，平安吉祥！",
                "清明节": "清明时节，缅怀先人，珍惜当下。愿逝者安息，生者奋发。",
                "元宵节": "元宵节快乐！愿您人圆事圆花好月圆，甜甜蜜蜜，幸福团圆！"
            }
            for key in blessing_templates:
                if key in holiday_name:
                    return blessing_templates[key]
            
            # 通用回退
            return f"祝您{holiday_name}快乐，万事顺心，阖家安康！"
            
        except Exception as e:
            self.logger.error(f"生成祝福语时发生未知错误: {e}")
            return f"祝您{holiday_name}快乐！"
    
    async def generate_image(self, blessing: str, holiday_name: str) -> tuple[str | None, str | None]:
        """
        生成并保存节日祝福图片。

        调用 `utils.ttp.generate_image_openrouter` 函数执行生成，并处理后续的
        文件传输（如果配置了NAP服务器）。

        Args:
            blessing (str): 生成的祝福语，用于构建提示词。
            holiday_name (str): 节日名称，用于构建提示词。

        Returns:
            tuple[str | None, str | None]: 成功时返回(图片URL, 图片本地/远程路径)，失败时返回(None, None)。
        """
        try:
            if not self.openrouter_api_keys:
                self.logger.warning("未配置OpenRouter API密钥，跳过图片生成。")
                return None, None
            
            # 1. 加载参考图（如果已配置）
            reference_images = await self.load_reference_images()
            
            # 2. 构建最终的图像生成提示词
            prompt = self.build_reference_prompt(blessing, holiday_name, bool(reference_images))
            
            # 3. 调用图像生成函数
            image_url, image_path = await generate_image_openrouter(
                prompt=prompt,
                api_keys=self.openrouter_api_keys,
                model=self.model_name,
                input_images=reference_images,
                max_retry_attempts=self.max_retry_attempts,
                api_base=self.custom_api_base if self.custom_api_base else None
            )
            
            if not image_url or not image_path:
                self.logger.error("图片生成失败。")
                return None, None
            
            # 4. 如果配置了NAP服务器，则将文件传输到远程
            if self.nap_server_address and self.nap_server_address != "localhost":
                try:
                    transferred_path = await send_file(image_path, host=self.nap_server_address, port=self.nap_server_port)
                    if transferred_path:
                        image_path = transferred_path  # 更新为服务器上的路径
                        self.logger.info(f"图片成功传输到NAP服务器: {image_path}")
                    else:
                        self.logger.warning("NAP服务器未返回有效路径，将使用本地路径。")
                except Exception as e:
                    self.logger.warning(f"NAP文件传输失败，将使用本地路径: {e}")
            
            self.logger.info(f"节日图片已准备就绪: {image_path}")
            return image_url, image_path
            
        except Exception as e:
            self.logger.error(f"生成图片过程中发生未知错误: {e}")
            return None, None
