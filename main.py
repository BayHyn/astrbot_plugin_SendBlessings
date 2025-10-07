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
from .utils.ttp import generate_image_openrouter
from .utils.file_send_server import send_file

from .utils.ttp import generate_image_openrouter
from .utils.file_send_server import send_file


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


@register("SendBlessings", "Cheng-MaoMao", "在节假日自动送上祝福并配图", "1.0.1")
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

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("blessings reload")
    async def reload_holidays(self, event: AstrMessageEvent):
        """重新加载节假日数据"""
        try:
            self.holidays = get_current_year_holidays(self.json_file)
            yield event.plain_result(f"节假日数据已重新加载，共 {len(self.holidays)} 条记录。")
        except Exception as e:
            self.logger.error(f"重新加载节假日数据失败: {e}")
            yield event.plain_result(f"重新加载失败: {str(e)}")
    
    @filter.permission_type(filter.PermissionType.ADMIN)
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
    
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("blessings manual")
    async def manual_bless(self, event: AstrMessageEvent, holiday_name: str = None):
        """手动生成并发送祝福（测试用，仅管理员）"""
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

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("blessings test")
    async def test_target_sessions(self, event: AstrMessageEvent):
        """测试目标会话列表功能（仅管理员）"""
        try:
            if not self.target_sessions:
                yield event.plain_result("未配置目标会话列表，请在配置文件中添加 target_sessions。")
                return

            test_blessing = "🎉 这是一条测试消息，用于验证目标会话配置是否正确。如果您收到此消息，说明配置成功！"
            
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

            for session_info in self.target_sessions:
                if isinstance(session_info, dict) and all(k in session_info for k in ['platform', 'type', 'id']):
                    platform = session_info['platform']
                    session_type = 'friend' if session_info['type'] == 'private' else session_info['type']
                    session_id = session_info['id']
                    
                    # 构造正确的会话字符串
                    session_str = f"{platform}:{session_type}:{session_id}"
                    
                    try:
                        await self.context.send_message(session_str, test_chain)
                        success_count += 1
                        self.logger.info(f"测试消息已发送到 {session_str}")
                    except Exception as e:
                        failed_sessions_info.append(f"{session_str} (原因: {e})")
                        self.logger.error(f"发送测试消息到 {session_str} 失败: {e}")
                else:
                    # 兼容旧的字符串格式
                    session_str = str(session_info)
                    try:
                        await self.context.send_message(session_str, test_chain)
                        success_count += 1
                        self.logger.info(f"测试消息已发送到 {session_str} (旧格式)")
                    except Exception as e:
                        failed_sessions_info.append(f"{session_str} (原因: {e})")
                        self.logger.error(f"发送测试消息到 {session_str} (旧格式) 失败: {e}")

            result_message = f"测试完成！\n✅ 成功发送: {success_count} 个会话\n"
            if failed_sessions_info:
                result_message += f"❌ 发送失败: {len(failed_sessions_info)} 个会话\n"
                result_message += f"失败详情: {', '.join(failed_sessions_info[:3])}"
                if len(failed_sessions_info) > 3:
                    result_message += "..."

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
                    for session_info in self.target_sessions:
                        session_str = None
                        try:
                            if isinstance(session_info, dict) and all(k in session_info for k in ['platform', 'type', 'id']):
                                platform = session_info['platform']
                                session_type = 'friend' if session_info['type'] == 'private' else session_info['type']
                                session_id = session_info['id']
                                session_str = f"{platform}:{session_type}:{session_id}"
                            else:
                                # 兼容旧的字符串格式
                                session_str = str(session_info)

                            await self.context.send_message(session_str, chain)
                            sent_count += 1
                            self.logger.info(f"祝福消息已发送到 {session_str}")
                        except Exception as e:
                            self.logger.error(f"发送到 {session_str or session_info} 失败: {e}")
                    
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
                "端午节": "端午节安康！粽子香，艾叶长，祝您身体健康，平安吉祥！",
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
            return f"{holiday_name}祝您节日愉快，身体健康，工作顺利，阖家幸福！"
            
        except Exception as e:
            self.logger.error(f"生成祝福语失败: {e}")
            return f"{holiday_name}祝您节日愉快！"
    
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
