from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Comp
import asyncio
import aiohttp
import json
import os
import datetime
from holidays_get import get_current_year_holidays, print_holidays_summary, check_single_date, translate_holiday_name
from astrbot.api.provider import ProviderRequest

@register("SendBlessings", "Cheng-MaoMao", "在节假日送上祝福的插件", "1.0.0")
class SendBlessingsPlugin(Star):
    def __init__(self, context: Context, config):
        super().__init__(context)
        self.config = config
        self.json_file = os.path.join(self.context.get_config().get('data_dir', 'data'), self.config.get('holidays_file', 'holidays.json'))
        self.holidays = []
        self.target_sessions = self.config.get('target_sessions', [])
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
            # 使用MCP工具查询习俗和祝福语
            customs = await self.query_holiday_customs(holiday_name)
            if not customs:
                customs = f"{holiday_name}传统节日"
            
            # 调用LLM生成祝福
            provider = self.context.get_using_provider()
            if not provider:
                self.logger.error("未找到LLM提供商")
                return None
            
            prompt = f"你是一个温暖的AI助手。请基于以下节日信息生成一段简短、积极的中文祝福语（50-100字），适合发送给朋友或群聊。节日：{holiday_name}，习俗/背景：{customs}。祝福语要真挚、节日氛围浓厚。"
            
            resp = await provider.text_chat(
                prompt=prompt,
                system_prompt="你是一个专业的节日祝福生成器，输出仅为祝福语文本，不要添加额外解释。",
                model=self.config.get('llm_model', 'gpt-3.5-turbo')
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
        """使用MCP工具查询节日习俗"""
        try:
            # 调用tavily-search工具（假设远程MCP可用）
            tool_call = {
                "server_name": self.config.get('mcp_server_name', 'tavily-mcp'),
                "tool_name": "tavily-search",
                "arguments": {
                    "query": f"{holiday_name} 节日习俗 传统祝福语",
                    "max_results": 3,
                    "search_depth": "basic"
                }
            }
            
            # 注意：在实际AstrBot环境中，需要通过适当方式调用MCP；这里模拟为占位
            # 实际实现中，可能需要self.context或其他方式触发use_mcp_tool
            # 为兼容，假设返回示例结果
            # 真实环境中，使用use_mcp_tool并处理响应
            customs = "这是一个传统节日，人们会聚在一起庆祝，交换祝福，享受美食和家庭时光。"  # 模拟响应
            
            self.logger.info(f"查询 {holiday_name} 习俗: {customs[:100]}...")
            return customs
            
        except Exception as e:
            self.logger.error(f"MCP查询失败: {e}")
            return f"{holiday_name}传统节日"
    
    async def generate_image(self, blessing: str, holiday_name: str) -> str:
        """生成节日祝福图片"""
        try:
            prompt = f"{holiday_name} 节日祝福海报，温暖喜庆风格，包含文字：{blessing[:50]}...，节日元素如灯笼/花朵/雪花等，高质量，卡通插画"
            
            if self.config.get('reference_image_path'):
                # 如果有参考图，包含在prompt中（假设API支持image-to-image或prompt描述）
                prompt += f"，参考风格：{self.config['reference_image_path']}"
                # 实际中，如果API支持上传参考图，需要额外处理文件上传
            
            url = self.config.get('image_gen_url')
            api_key = self.config.get('image_gen_api_key')
            if not url or not api_key:
                self.logger.error("图片生成配置不完整")
                return None
            
            headers = {
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            }
            data = {
                'prompt': prompt,
                'model': self.config.get('llm_model', 'gemini-1.5-pro'),  # 假设使用相同模型或自定义
                'width': 800,
                'height': 600,
                'n': 1
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data, headers=headers) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        # 假设API返回image_url在result['data'][0]['url']
                        image_url = result.get('data', [{}])[0].get('url', '')
                        if image_url:
                            self.logger.info(f"图片生成成功: {image_url}")
                            return image_url
                        else:
                            self.logger.error("API响应中无图片URL")
                    else:
                        self.logger.error(f"图片生成API错误: {resp.status} - {await resp.text()}")
                        
        except Exception as e:
            self.logger.error(f"生成图片失败: {e}")
        return None
