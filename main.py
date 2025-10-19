from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api.platform import MessageType
from astrbot.api import logger
import astrbot.api.message_components as Comp
import asyncio
import aiofiles
import json
import os
import base64
from datetime import datetime, date, timedelta
from pathlib import Path
from .utils.ttp import generate_image_openrouter
from .utils.file_send_server import send_file
from .utils.holiday_manager import HolidayManager


@register("SendBlessings", "Cheng-MaoMao", "在节假日自动送上祝福并配图", "1.0.9")
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
        
        # 手动构建插件数据目录以实现数据隔离
        base_data_dir = Path(self.context.get_config().get('data_dir', 'data'))
        plugin_name = self.context.get_registered_star("SendBlessings").name
        self.plugin_data_dir = base_data_dir / "plugin_data" / plugin_name
        self.plugin_data_dir.mkdir(parents=True, exist_ok=True)
        
        self.json_file = self.plugin_data_dir / self.config.get('holidays_file', 'holidays.json')
        self.holiday_manager = HolidayManager(self.json_file)
        
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
        self.test_targets = config.get("test_targets", {})
        self.reference_images_enabled = self.reference_images_config.get("enabled", False)
        self.reference_image_paths = self.reference_images_config.get("image_paths", [])
        self.max_reference_images = self.reference_images_config.get("max_images", 3)
        
        self.holidays = []
        self.logger = logger

        # 加载假期结束提醒配置
        self.end_of_holiday_config = config.get("end_of_holiday_blessing", {})
        
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
            self.holidays = await self.holiday_manager.get_holidays_for_year(datetime.now().year)
            
            # 启动每日祝福检查的后台循环任务
            asyncio.create_task(self.daily_blessing_checker())
            
            # 启动假期结束提醒的后台任务
            if self.end_of_holiday_config.get("enabled", False):
                asyncio.create_task(self.end_of_holiday_checker())

            self.logger.info("节假日祝福插件初始化完成。")
        except Exception as e:
            self.logger.error(f"插件初始化失败: {e}")

    @filter.command_group("blessings")
    def blessings(self):
        """节假日祝福插件管理指令"""
        pass

    @blessings.command("reload")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def reload_holidays(self, event: AstrMessageEvent):
        """
        [管理员指令] 重新加载节假日数据。
        """
        try:
            self.holidays = await self.holiday_manager.get_holidays_for_year(datetime.now().year)
            yield event.plain_result(f"节假日数据已重新加载，共 {len(self.holidays)} 条记录。")
        except Exception as e:
            self.logger.error(f"重新加载节假日数据失败: {e}")
            yield event.plain_result(f"重新加载失败: {str(e)}")
    
    @blessings.command("check")
    @filter.permission_type(filter.PermissionType.ADMIN)
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
    
    @blessings.command("manual")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def manual_bless(self, event: AstrMessageEvent, holiday_name: str = "手动测试"):
        """
        [管理员指令] 手动触发一次祝福生成和发送流程（仅用于测试）。
        
        该指令不再受节假日限制，可随时用于测试。
        """
        try:
            # 1. 生成祝福语
            blessing = await self.generate_blessing(holiday_name)
            if not blessing:
                yield event.plain_result("祝福语生成失败。")
                return
            
            # 2. 根据配置决定是否生成图片
            if self.config.get('generate_images_enabled', True):
                image_url, image_path = await self.generate_image(blessing, holiday_name)
                if image_path:
                    yield event.chain_result([Comp.Plain(blessing), Comp.Image.fromFileSystem(image_path)])
                else:
                    # 图片生成失败，只发送文字
                    yield event.plain_result(f"{blessing}\n(图片生成失败)")
            else:
                # 只发送祝福语
                yield event.plain_result(blessing)
            yield event.plain_result("手动祝福已发送到当前会话！")
        except Exception as e:
            self.logger.error(f"手动祝福失败: {e}")
            yield event.plain_result(f"手动祝福失败: {str(e)}")

    @blessings.command("test")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def test_blessings(self, event: AstrMessageEvent, holiday_name: str = "手动测试"):
        """
        [管理员指令] 测试向指定群组和个人发送祝福。
        
        Args:
            holiday_name (str, optional): 要测试的节日名称。默认为 "手动测试"。
        """
        try:
            group_ids = self.test_targets.get("group_ids", [])
            user_ids = self.test_targets.get("user_ids", [])

            if not group_ids and not user_ids:
                yield event.plain_result("测试目标未配置。请在插件配置中设置 'test_targets'。")
                return

            yield event.plain_result(f"开始向 {len(group_ids)} 个群组和 {len(user_ids)} 个用户发送测试祝福...")

            # 1. 生成祝福语和图片
            blessing = await self.generate_blessing(holiday_name)
            if not blessing:
                yield event.plain_result("祝福语生成失败，测试中止。")
                return
            
            # 2. 根据配置构建消息链
            chain = [Comp.Plain(blessing)]
            if self.config.get('generate_images_enabled', True):
                image_url, image_path = await self.generate_image(blessing, holiday_name)
                if image_path:
                    chain.append(Comp.Image.fromFileSystem(image_path))
                else:
                    chain.append(Comp.Plain("\n(图片生成失败)"))

            # 3. 发送消息
            success_count = 0
            fail_count = 0

            # 发送到群组
            for group_id in group_ids:
                sent_on_any_platform = False
                for platform in self.context.platform_manager.get_insts():
                    session_str = f"{platform.meta.name}:{MessageType.GROUP_MESSAGE.value}:{group_id}"
                    try:
                        if await self.context.send_message(session_str, chain):
                            success_count += 1
                            self.logger.info(f"测试祝福已发送到群组 {group_id} (平台: {platform.meta.name})")
                            await asyncio.sleep(2)
                            sent_on_any_platform = True
                            break
                    except Exception as e:
                        self.logger.warning(f"尝试通过平台 {platform.meta.name} 发送测试祝福到群组 {group_id} 失败: {e}")
                if not sent_on_any_platform:
                    fail_count += 1
                    self.logger.error(f"发送测试祝福到群组 {group_id} 失败: 所有平台都无法发送。")

            # 发送到用户
            for user_id in user_ids:
                sent_on_any_platform = False
                for platform in self.context.platform_manager.get_insts():
                    session_str = f"{platform.meta.name}:{MessageType.FRIEND_MESSAGE.value}:{user_id}"
                    try:
                        if await self.context.send_message(session_str, chain):
                            success_count += 1
                            self.logger.info(f"测试祝福已发送到用户 {user_id} (平台: {platform.meta.name})")
                            await asyncio.sleep(2)
                            sent_on_any_platform = True
                            break
                    except Exception as e:
                        self.logger.warning(f"尝试通过平台 {platform.meta.name} 发送测试祝福到用户 {user_id} 失败: {e}")
                if not sent_on_any_platform:
                    fail_count += 1
                    self.logger.error(f"发送测试祝福到用户 {user_id} 失败: 所有平台都无法发送。")

            # 4. 报告结果
            yield event.plain_result(f"测试完成！\n成功发送: {success_count} 个\n失败: {fail_count} 个")

        except Exception as e:
            self.logger.error(f"测试祝福指令失败: {e}")
            yield event.plain_result(f"测试指令执行失败: {str(e)}")

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
                f"Based on the provided reference image(s), create a new artwork with the theme of '{holiday_name}'. "
                f"**Task**: Identify the main character(s) in the reference image(s) and place them into a new, festive scene that matches the '{holiday_name}' theme. "
                f"**Character Modifications**: While preserving the core identity (face, hairstyle) of the character(s), you MUST **change their clothing** to festive attire suitable for '{holiday_name}' (e.g., traditional Chinese outfits for Spring Festival, modern festive wear for New Year). "
                f"**Actions and Props**: The character(s) should be performing a festive action (e.g., lighting lanterns, setting off fireworks, holding festive items). Add relevant props like red envelopes, lanterns, or holiday food. "
                f"**Scene**: The background should be a rich, festive environment related to '{holiday_name}'. "
                f"**Style**: The final image should be a high-quality, harmonious cartoon illustration with a warm and joyful atmosphere. "
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
        self.logger.info("每日祝福检查任务已启动。")
        while True:
            try:
                # --- 精确计算到第二天凌晨的时间 ---
                now = datetime.now()
                # 设置目标时间为第二天的 00:05
                tomorrow = now.date() + timedelta(days=1)
                target_time = datetime.combine(tomorrow, datetime.min.time()).replace(hour=0, minute=5)
                wait_seconds = (target_time - now).total_seconds()
                
                self.logger.info(f"下一次每日祝福检查将在 {target_time.strftime('%Y-%m-%d %H:%M:%S')} 进行，等待 {wait_seconds:.0f} 秒。")
                await asyncio.sleep(wait_seconds)
                # --- --------------------------- ---

                today = datetime.now().date()
                today_info = next((h for h in self.holidays if h['date'] == today.isoformat()), None)
                
                if today_info and today_info['is_first_day'] and today_info['is_holiday'] and self.config.get('enabled', True):
                    holiday_name = today_info['holiday_name']
                    self.logger.info(f"检测到假期第一天：{holiday_name}，开始发送祝福...")
                    
                    blessing = await self.generate_blessing(holiday_name)
                    if not blessing:
                        self.logger.error("祝福语生成失败，跳过本次发送。")
                        continue
                    
                    chain = [Comp.Plain(blessing)]
                    if self.config.get('generate_images_enabled', True):
                        image_url, image_path = await self.generate_image(blessing, holiday_name)
                        if image_path:
                            chain.append(Comp.Image.fromFileSystem(image_path))
                        else:
                            self.logger.error("图片生成失败，将只发送文字。")
                            chain.append(Comp.Plain("\n(图片生成失败)"))
                    
                    # --- 平台无关的广播逻辑 ---
                    sent_count = 0
                    all_platforms = self.context.platform_manager.get_insts()
                    for platform in all_platforms:
                        # 仅针对支持 get_client 和 call_action 的平台 (如 aiocqhttp)
                        if not hasattr(platform, "get_client") or not platform.get_client() or not hasattr(platform.get_client().api, "call_action"):
                            continue
                        
                        self.logger.info(f"正在通过平台 '{platform.meta.name}' 进行广播...")
                        client = platform.get_client()
                        
                        try:
                            friend_list = await client.api.call_action("get_friend_list")
                            group_list = await client.api.call_action("get_group_list")

                            # 发送到好友
                            for friend in friend_list:
                                user_id = friend.get('user_id')
                                if not user_id: continue
                                session_str = f"{platform.meta.name}:{MessageType.FRIEND_MESSAGE.value}:{user_id}"
                                try:
                                    await self.context.send_message(session_str, chain)
                                    sent_count += 1
                                    self.logger.info(f"祝福消息已发送到用户 {user_id}")
                                    await asyncio.sleep(5)
                                except Exception as e:
                                    self.logger.error(f"发送祝福到用户 {user_id} 失败: {e}")
                            
                            # 发送到群组
                            for group in group_list:
                                group_id = group.get('group_id')
                                if not group_id: continue
                                session_str = f"{platform.meta.name}:{MessageType.GROUP_MESSAGE.value}:{group_id}"
                                try:
                                    await self.context.send_message(session_str, chain)
                                    sent_count += 1
                                    self.logger.info(f"祝福消息已发送到群组 {group_id}")
                                    await asyncio.sleep(5)
                                except Exception as e:
                                    self.logger.error(f"发送祝福到群组 {group_id} 失败: {e}")
                        except Exception as e:
                            self.logger.error(f"从平台 '{platform.meta.name}' 获取好友/群组列表失败: {e}")

                    if sent_count > 0:
                        self.logger.info(f"今日({holiday_name})祝福已成功发送到 {sent_count} 个会话。")
                    else:
                        self.logger.warning("未能获取到任何好友或群组，今日祝福未发送。")
                
                if today.month == 12 and today.day == 31:
                    next_year = today.year + 1
                    self.logger.info(f"正在预加载 {next_year} 年的节假日数据...")
                    self.holidays = await self.holiday_manager.get_holidays_for_year(next_year)
                
            except asyncio.CancelledError:
                self.logger.info("每日祝福检查任务被取消。")
                break
            except Exception as e:
                self.logger.error(f"每日祝福检查任务发生严重错误: {e}")
                await asyncio.sleep(3600)
    
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
    
    async def generate_image(self, blessing: str, holiday_name: str, cmd_ref_images: list[str] = None) -> tuple[str | None, str | None]:
        """
        生成并保存节日祝福图片。

        调用 `utils.ttp.generate_image_openrouter` 函数执行生成，并处理后续的
        文件传输（如果配置了NAP服务器）。

        Args:
            blessing (str): 生成的祝福语，用于构建提示词。
            holiday_name (str): 节日名称，用于构建提示词。
            cmd_ref_images (list[str], optional): 从指令中直接提供的参考图 (base64-encoded). Defaults to None.

        Returns:
            tuple[str | None, str | None]: 成功时返回(图片URL, 图片本地/远程路径)，失败时返回(None, None)。
        """
        try:
            if not self.openrouter_api_keys:
                self.logger.warning("未配置OpenRouter API密钥，跳过图片生成。")
                return None, None
            
            # 1. 确定要使用的参考图
            # 优先使用从指令传入的参考图
            if cmd_ref_images:
                reference_images = cmd_ref_images
                self.logger.info(f"使用指令中提供的 {len(reference_images)} 张参考图。")
            else:
                # 否则，加载配置文件中指定的参考图
                reference_images = await self.load_reference_images()
            
            # 2. 构建最终的图像生成提示词
            prompt = self.build_reference_prompt(blessing, holiday_name, bool(reference_images))
            
            # 3. 调用图像生成函数
            image_url, image_path = await generate_image_openrouter(
                prompt=prompt,
                api_keys=self.openrouter_api_keys,
                model=self.model_name,
                data_dir=self.plugin_data_dir,
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

    async def generate_end_of_holiday_blessing(self, holiday_name: str) -> str:
        """
        生成假期结束的祝福语。

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
                    prompt = f"为“{holiday_name}”假期的最后一天晚上，生成一段简短温馨的中文祝福语（50-100字）。内容应包含对假期的回顾，并鼓励大家以饱满的热情迎接接下来的工作和生活。"
                    
                    resp = await provider.text_chat(
                        prompt=prompt,
                        system_prompt="你是一个善于鼓励和给予温暖祝福的AI助手。你的回答应该只包含祝福语文本本身，不要添加任何额外的解释或引言。"
                    )
                    
                    if resp and resp.completion_text:
                        blessing = resp.completion_text.strip()
                        if blessing and len(blessing) > 10:
                            self.logger.info(f"成功使用LLM为 {holiday_name} 假期结束生成祝福语。")
                            return blessing
            except Exception as e:
                self.logger.warning(f"LLM生成假期结束祝福语失败，将使用预设模板: {e}")

            # LLM失败或未配置，回退到模板
            return f"{holiday_name}假期即将结束，希望您度过了一个愉快而充实的时光！让我们整理好心情，带着满满的能量和美好的回忆，迎接新的挑战。祝您在未来的工作和生活中一切顺利，天天开心！"

        except Exception as e:
            self.logger.error(f"生成假期结束祝福语时发生未知错误: {e}")
            return "假期即将结束，祝您未来一切顺利！"

    async def end_of_holiday_checker(self):
        """
        每日在指定时间检查是否为假期最后一天，并发送祝福。
        """
        self.logger.info("假期结束提醒任务已启动。")
        while True:
            try:
                now = datetime.now()
                send_time_str = self.end_of_holiday_config.get("send_time", "22:00")
                hour, minute = map(int, send_time_str.split(':'))
                
                send_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                
                if now > send_time:
                    send_time += timedelta(days=1)
                
                wait_seconds = (send_time - now).total_seconds()
                self.logger.info(f"下一次假期结束检查将在 {send_time.strftime('%Y-%m-%d %H:%M:%S')} 进行，等待 {wait_seconds:.0f} 秒。")
                await asyncio.sleep(wait_seconds)

                today = datetime.now().date()
                today_info = next((h for h in self.holidays if h['date'] == today.isoformat()), None)

                if today_info and today_info['is_last_day']:
                    holiday_name = today_info['holiday_name']
                    self.logger.info(f"检测到假期最后一天：{holiday_name}，准备发送结束提醒...")

                    # 1. 生成祝福语
                    blessing = await self.generate_end_of_holiday_blessing(holiday_name)
                    
                    # 2. 生成图片
                    # 3. 构建消息链
                    chain = [Comp.Plain(blessing)]
                    if self.config.get('generate_images_enabled', True):
                        image_url, image_path = await self.generate_image(blessing, holiday_name)
                        if image_path:
                            chain.append(Comp.Image.fromFileSystem(image_path))
                        else:
                            self.logger.error("假期结束提醒的图片生成失败，将只发送文字。")
                            chain.append(Comp.Plain("\n(图片生成失败)"))

                    # 4. 发送到所有目标会话
                    sent_count = 0
                    all_platforms = self.context.platform_manager.get_insts()
                    for platform in all_platforms:
                        if not hasattr(platform, "get_client") or not platform.get_client() or not hasattr(platform.get_client().api, "call_action"):
                            continue
                        
                        self.logger.info(f"正在通过平台 '{platform.meta.name}' 发送假期结束提醒...")
                        client = platform.get_client()
                        
                        try:
                            friend_list = await client.api.call_action("get_friend_list")
                            group_list = await client.api.call_action("get_group_list")

                            # 发送到好友
                            for friend in friend_list:
                                user_id = friend.get('user_id')
                                if not user_id: continue
                                session_str = f"{platform.meta.name}:{MessageType.FRIEND_MESSAGE.value}:{user_id}"
                                try:
                                    await self.context.send_message(session_str, chain)
                                    sent_count += 1
                                    self.logger.info(f"假期结束提醒已发送到用户 {user_id}")
                                    await asyncio.sleep(3)
                                except Exception as e:
                                    self.logger.error(f"发送假期结束提醒到用户 {user_id} 失败: {e}")
                            
                            # 发送到群组
                            for group in group_list:
                                group_id = group.get('group_id')
                                if not group_id: continue
                                session_str = f"{platform.meta.name}:{MessageType.GROUP_MESSAGE.value}:{group_id}"
                                try:
                                    await self.context.send_message(session_str, chain)
                                    sent_count += 1
                                    self.logger.info(f"假期结束提醒已发送到群组 {group_id}")
                                    await asyncio.sleep(3)
                                except Exception as e:
                                    self.logger.error(f"发送假期结束提醒到群组 {group_id} 失败: {e}")
                        except Exception as e:
                            self.logger.error(f"从平台 '{platform.meta.name}' 获取好友/群组列表失败: {e}")

                    if sent_count > 0:
                        self.logger.info(f"假期结束提醒已成功发送到 {sent_count} 个会话。")
                    else:
                        self.logger.warning("未能获取到任何好友或群组，假期结束提醒未发送。")

            except asyncio.CancelledError:
                self.logger.info("假期结束提醒任务被取消。")
                break
            except Exception as e:
                self.logger.error(f"假期结束提醒任务发生严重错误: {e}")
                await asyncio.sleep(3600) # 出错时等待1小时后重试
