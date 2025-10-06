from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp
import asyncio
import aiohttp
import json
import os
import datetime
from chinese_calendar import is_holiday, is_workday
import chinese_calendar as calendar
from cn_bing_translator import Translator

# 导入 utils 中的函数
from utils.ttp import generate_image_openrouter, cleanup_old_images
from utils.file_send_server import send_file


def translate_holiday_name(holiday_name):
    """翻译节日名称，失败时返回原名"""
    if not holiday_name:
        return ''
    try:
        translator = Translator(toLang='zh-Hans')
        result = translator.process(holiday_name)
        return result if result and result != holiday_name else holiday_name
    except:
        return holiday_name


def load_holidays_from_json(json_file):
    """从 JSON 文件加载节假日数据"""
    if json_file is None:
        json_file = 'holidays.json'  # 默认
    if os.path.exists(json_file):
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get('year'), data.get('holidays', [])
    return None, []


def save_holidays_to_json(year, holidays, json_file):
    """保存节假日数据到 JSON 文件"""
    if json_file is None:
        json_file = 'holidays.json'  # 默认
    data = {
        'year': year,
        'holidays': holidays
    }
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"节假日数据已保存到 {json_file}")


def get_year_holidays(year, json_file=None):
    """获取指定年份的节假日信息"""
    start_date = datetime.date(year, 1, 1)
    end_date = datetime.date(year, 12, 31)
    holidays = []
    current_date = start_date
    prev_holiday_name = None

    print(f"\n获取 {year} 年节假日信息：")
    while current_date <= end_date:
        on_holiday, holiday_name = calendar.get_holiday_detail(current_date)
        is_hol = is_holiday(current_date)
        is_work = is_workday(current_date)
        is_lieu = calendar.is_in_lieu(current_date)
        
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
                print(f"{current_date} 是节假日，{translated_name}快乐")
                if calendar.is_in_lieu(current_date):
                    print(f"{current_date} 是调休")
                prev_holiday_name = translated_name
            else:
                print(f"{current_date} 是{translated_name}假期")
                if calendar.is_in_lieu(current_date):
                    print(f"{current_date} 是调休")
        
        holidays.append(holiday_info)
        
        current_date += datetime.timedelta(days=1)
    
    return holidays


def get_current_year_holidays(json_file=None):
    """获取当前年份节假日"""
    current_year = datetime.date.today().year
    saved_year, saved_holidays = load_holidays_from_json(json_file)

    if saved_year == current_year and saved_holidays:
        print(f"已加载 {current_year} 年节假日数据，共 {len(saved_holidays)} 条记录。")
        return saved_holidays
    else:
        print(f"未找到 {current_year} 年数据或需更新，正在重新获取...")
        holidays = get_year_holidays(current_year, json_file)
        save_holidays_to_json(current_year, holidays, json_file)
        return holidays


def print_holidays_summary(holidays, year):
    """输出节假日摘要"""
    print(f"\n{year} 年节假日摘要：")
    total_days = len(holidays)
    holiday_count = sum(1 for h in holidays if h['is_holiday'])
    workday_count = sum(1 for h in holidays if h['is_workday'])
    lieu_count = sum(1 for h in holidays if h['is_in_lieu'])
    first_day_count = sum(1 for h in holidays if h['is_first_day'])
    print(f"总天数：{total_days}")
    print(f"总节假日数：{holiday_count}")
    print(f"总工作日数：{workday_count}")
    print(f"调休日数：{lieu_count}")
    print(f"假期第一天数：{first_day_count}")


def check_single_date(date_input, holidays):
    """检查单个日期（内联使用）"""
    for h in holidays:
        if h['date'] == date_input.isoformat():
            if h['is_holiday']:
                print(f"{date_input} 是假期，{h['holiday_name']}")
            else:
                print(f"{date_input} 是工作日")
            if h['is_in_lieu']:
                print(f"{date_input} 是调休")
            return
    print(f"{date_input} 未找到记录")


@register("SendBlessings", "Cheng-MaoMao", "在节假日送上祝福的插件", "1.0.0")
class SendBlessingsPlugin(Star):
    def __init__(self, context: Context, config):
        super().__init__(context)
        self.config = config
        self.json_file = os.path.join(self.context.get_config().get('data_dir', 'data'), self.config.get('holidays_file', 'holidays.json'))
        
        # 加载 OpenRouter 配置
        self.openrouter_api_keys = config.get("openrouter_api_keys", [])
        self.model_name = config.get("model_name", "google/gemini-2.5-flash-image-preview:free")
        self.max_retry_attempts = config.get("max_retry_attempts", 3)
        self.custom_api_base = config.get("custom_api_base", "").strip()
        
        # 加载 NAP 配置
        self.nap_server_address = config.get("nap_server_address", "localhost")
        self.nap_server_port = config.get("nap_server_port", 3658)
        
        self.holidays = []
        self.target_sessions = []  # 用户需在此设置目标会话列表，如 ['aiocqhttp:GROUP:123456']
        self.logger = logger

    async def initialize(self):
        """插件初始化：加载节假日数据并启动每日检查任务"""
        if not self.config.get('enabled', True):
            self.logger.info("插件未启用，跳过初始化")
            return
        
        # 确保data目录存在
        data_dir = self.context.get_config().get('data_dir', 'data')
        os.makedirs(data_dir, exist_ok=True)
        
        # 加载或获取当前年节假日
        self.holidays = get_current_year_holidays(self.json_file)
        print_holidays_summary(self.holidays, datetime.date.today().year)
        
        # 启动每日祝福检查任务
        asyncio.create_task(self.daily_blessing_checker())
        self.logger.info("节假日祝福插件初始化完成")

    @filter.command("blessings reload")
    async def reload_holidays(self, event: AstrMessageEvent):
        """重新加载节假日数据"""
        self.holidays = get_current_year_holidays(self.json_file)
        yield event.plain_result(f"节假日数据已重新加载，共 {len(self.holidays)} 条记录。")
    
    @filter.command("blessings check")
    async def check_today(self, event: AstrMessageEvent):
        """检查今天是否为节假日第一天"""
        today = datetime.date.today()
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
    
    @filter.command("blessings manual")
    async def manual_bless(self, event: AstrMessageEvent, holiday_name: str = None):
        """手动生成并发送祝福（测试用，仅管理员）"""
        if not event.is_admin():
            yield event.plain_result("仅管理员可使用。")
            return
        
        today = datetime.date.today()
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
        image_url = await self.generate_image(blessing, holiday_name)
        if not image_url:
            yield event.plain_result("图片生成失败。")
            return
        
        # 发送到当前会话
        chain = [
            Comp.Plain(blessing),
            Comp.Image.fromURL(image_url)
        ]
        yield event.chain_result(chain)
        yield event.plain_result("手动祝福已发送！")

    async def terminate(self):
        """插件销毁：清理资源"""
        self.logger.info("节假日祝福插件已销毁")
    
    async def daily_blessing_checker(self):
        """每日检查是否需要发送祝福"""
        while True:
            try:
                await asyncio.sleep(3600 * 24)  # 每天检查一次（可调整为更精确的时间）
                today = datetime.date.today()
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
                    image_url = await self.generate_image(blessing, holiday_name)
                    if not image_url:
                        self.logger.error("图片生成失败，跳过发送")
                        continue
                    
                    # 构建消息链
                    chain = [
                        Comp.Plain(blessing),
                        Comp.Image.fromURL(image_url)
                    ]
                    
                    # 发送到目标会话
                    sent_count = 0
                    for session in self.target_sessions:
                        try:
                            await self.context.send_message(session, chain)
                            sent_count += 1
                            self.logger.info(f"祝福消息已发送到 {session}")
                        except Exception as e:
                            self.logger.error(f"发送到 {session} 失败: {e}")
                    
                    if sent_count > 0:
                        self.logger.info(f"今日祝福已发送到 {sent_count} 个会话")
                    else:
                        self.logger.warning("无目标会话，祝福未发送")
                
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
            # 使用websearch查询习俗和祝福语
            customs = await self.query_holiday_customs(holiday_name)
            if not customs:
                customs = f"{holiday_name}传统节日"
            
            # 调用AstrBot内置LLM
            provider = self.context.get_using_provider()
            if not provider:
                self.logger.error("未找到LLM提供商")
                return None
            
            prompt = f"你是一个温暖的AI助手。请基于以下节日信息生成一段简短、积极的中文祝福语（50-100字），适合发送给朋友或群聊。节日：{holiday_name}，习俗/背景：{customs}。祝福语要真挚、节日氛围浓厚。"
            
            resp = await provider.text_chat(
                prompt=prompt,
                system_prompt="你是一个专业的节日祝福生成器，输出仅为祝福语文本，不要添加额外解释。",
                model="gpt-3.5-turbo"  # 使用本体默认模型
            )
            
            if resp and resp.completion_text:
                return resp.completion_text.strip()
            else:
                self.logger.error("LLM响应为空")
                return None
        except Exception as e:
            self.logger.error(f"生成祝福语失败: {e}")
            return None
    
    async def query_holiday_customs(self, holiday_name: str) -> str:
        """使用AstrBot内置websearch查询节日习俗"""
        try:
            # 获取websearch提供商
            websearch = self.context.get_websearch()
            if not websearch:
                self.logger.error("未找到websearch提供商")
                return f"{holiday_name}传统节日"
            
            # 执行搜索
            query = f"{holiday_name} 节日习俗 传统祝福语 中国"
            results = await websearch.search(query=query, max_results=3)
            
            if results:
                customs_parts = []
                for result in results:
                    content = result.get('content') or result.get('snippet', '') or result.get('description', '')
                    if content:
                        customs_parts.append(content[:200])
                
                customs = ' '.join(customs_parts)
                if not customs.strip():
                    customs = f"{holiday_name}传统节日，涉及家庭团聚和庆祝习俗。"
            else:
                customs = f"{holiday_name}是中国传统节日，通常包括家庭团聚、祭祖、吃特色食物和互赠祝福，象征团圆与喜庆。"
            
            self.logger.info(f"websearch查询 {holiday_name} 习俗: {customs[:100]}...")
            return customs
            
        except Exception as e:
            self.logger.error(f"websearch查询失败: {e}")
            return f"{holiday_name}传统节日"
    
    async def generate_image(self, blessing: str, holiday_name: str) -> str:
        """生成节日祝福图片，使用 OpenRouter API"""
        try:
            # 构建图像生成提示词
            prompt = f"{holiday_name} 节日祝福海报，温暖喜庆风格，包含文字：{blessing[:50]}...，节日元素如灯笼/花朵/雪花等，高质量，卡通插画风格，节日氛围浓厚，中文文字清晰可见"
            
            # 调用 utils 中的生成函数
            image_url, image_path = await generate_image_openrouter(
                prompt=prompt,
                api_keys=self.openrouter_api_keys,
                model=self.model_name,
                max_retry_attempts=self.max_retry_attempts,
                api_base=self.custom_api_base if self.custom_api_base else None
            )
            
            if not image_url or not image_path:
                self.logger.error("图片生成失败")
                return None
            
            # 处理 NAP 文件传输
            if self.nap_server_address and self.nap_server_address != "localhost":
                try:
                    image_path = await send_file(image_path, host=self.nap_server_address, port=self.nap_server_port)
                    self.logger.info(f"NAP 传输成功: {image_path}")
                except Exception as e:
                    self.logger.warning(f"NAP 传输失败，回退本地路径: {e}")
                    # 回退使用本地路径
                    pass
            
            self.logger.info(f"节日图片生成成功: {image_path}")
            return image_url
            
        except Exception as e:
            self.logger.error(f"生成图片失败: {e}")
            return None
