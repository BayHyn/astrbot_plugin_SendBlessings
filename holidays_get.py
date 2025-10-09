import datetime
import json
import os
import asyncio
from chinese_calendar import is_holiday, is_workday
import chinese_calendar as calendar
from cn_bing_translator import Translator
from astrbot.api import logger

# JSON 文件路径，将在调用时动态设置
JSON_FILE = None

async def translate_holiday_name(holiday_name: str) -> str:
    """
    使用必应翻译将英文的节假日名称翻译成中文。

    Args:
        holiday_name (str): 英文节假日名称 (例如, 'New Year''s Day')。

    Returns:
        str: 翻译后的中文名称，如果翻译失败则返回原名称。
    """
    if not holiday_name:
        return ''
    try:
        translator = Translator(toLang='zh-Hans')
        # 在单独的线程中运行同步IO操作
        result = await asyncio.to_thread(translator.process, holiday_name)
        return result if result and result != holiday_name else holiday_name
    except Exception as e:
        logger.warning(f"警告: 翻译节日名称 '{holiday_name}' 失败: {e}")
        return holiday_name

def load_holidays_from_json(json_file: str) -> tuple[int | None, list]:
    """
    从指定的 JSON 文件中加载缓存的节假日数据。

    Args:
        json_file (str): 节假日数据JSON文件的路径。

    Returns:
        tuple[int | None, list]: 包含年份和节假日列表的元组。如果文件不存在或解析失败，返回 (None, [])。
    """
    if json_file is None:
        json_file = 'holidays.json'  # 默认文件名
    if os.path.exists(json_file):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('year'), data.get('holidays', [])
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"错误: 加载节假日数据失败: {e}")
            return None, []
    return None, []

def save_holidays_to_json(year: int, holidays: list, json_file: str):
    """
    将节假日数据保存到指定的 JSON 文件中。

    Args:
        year (int): 数据的年份。
        holidays (list): 包含全年节假日信息的列表。
        json_file (str): 要保存到的JSON文件的路径。
    """
    if json_file is None:
        json_file = 'holidays.json'  # 默认文件名
    data = {
        'year': year,
        'holidays': holidays
    }
    try:
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"节假日数据已成功保存到 {json_file}")
    except IOError as e:
        logger.error(f"错误: 保存节假日数据失败: {e}")

async def get_year_holidays(year: int, json_file: str = None) -> list:
    """
    获取指定年份全年的节假日详细信息。

    遍历该年的每一天，使用 `chinese_calendar` 判断其状态，并进行翻译和格式化。
    新增了 `is_first_day` 字段，用于标记一个连续假期的第一天。

    Args:
        year (int): 要获取数据的年份。
        json_file (str, optional): 用于保存的JSON文件路径。此参数在此函数中主要用于传递。

    Returns:
        list: 一个包含全年365/366天详细信息的字典列表。
    """
    start_date = datetime.date(year, 1, 1)
    end_date = datetime.date(year, 12, 31)
    holidays = []
    current_date = start_date
    prev_holiday_name = None

    logger.info(f"\n正在获取 {year} 年的节假日信息...")
    while current_date <= end_date:
        try:
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
                'is_first_day': False  # 标记是否为假期的第一天
            }
            
            if on_holiday and holiday_name:
                translated_name = await translate_holiday_name(holiday_name)
                holiday_info['holiday_name'] = translated_name
                
                # 检测是否为连续假期的第一天
                if not holidays or not holidays[-1]['is_holiday'] or holidays[-1]['holiday_name'] != translated_name:
                    holiday_info['is_first_day'] = True
                
                # 优化控制台输出，只在假期第一天打印完整信息
                if translated_name != prev_holiday_name:
                    logger.info(f"{current_date}: 是节假日 - {translated_name}")
                    if is_lieu:
                        logger.info(f"  -> (调休)")
                    prev_holiday_name = translated_name
            
            holidays.append(holiday_info)
        except Exception as e:
            logger.warning(f"警告: 处理日期 {current_date} 时出错: {e}")
        
        current_date += datetime.timedelta(days=1)
    
    return holidays

async def get_current_year_holidays(json_file: str = None) -> list:
    """
    获取当前年份的节假日数据，优先从缓存加载。

    如果缓存文件存在且年份匹配，则直接返回数据。
    否则，调用 `get_year_holidays` 重新获取并保存到缓存。

    Args:
        json_file (str, optional): 缓存文件的路径。

    Returns:
        list: 当前年份的节假日数据列表。
    """
    current_year = datetime.date.today().year
    saved_year, saved_holidays = load_holidays_from_json(json_file)

    if saved_year == current_year and saved_holidays:
        logger.info(f"已从缓存加载 {current_year} 年节假日数据，共 {len(saved_holidays)} 条记录。")
        return saved_holidays
    else:
        logger.info(f"未找到 {current_year} 年的缓存数据或数据已过时，正在重新获取...")
        holidays = await get_year_holidays(current_year, json_file)
        save_holidays_to_json(current_year, holidays, json_file)
        return holidays

def print_holidays_summary(holidays: list, year: int):
    """
    打印指定年份节假日数据的统计摘要。

    Args:
        holidays (list): 节假日数据列表。
        year (int): 对应的年份。
    """
    logger.info(f"\n--- {year} 年节假日摘要 ---")
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

async def check_single_date(date_input: datetime.date, json_file: str = None):
    """
    检查并打印单个日期的节假日状态（用于示例和调试）。

    Args:
        date_input (datetime.date): 要查询的日期。
        json_file (str, optional): 缓存文件的路径。
    """
    # 此函数主要用于演示，直接加载数据
    holidays = await get_current_year_holidays(json_file)
    
    for h in holidays:
        if h['date'] == date_input.isoformat():
            if h['is_holiday']:
                logger.info(f"\n查询结果: {date_input} 是假期 - {h['holiday_name']}")
            else:
                logger.info(f"\n查询结果: {date_input} 是工作日")
            
            if h['is_in_lieu']:
                logger.info(f"  -> (调休)")
            return
            
    logger.info(f"\n查询结果: 在 {date_input.year} 年的记录中未找到 {date_input}。")

async def main():
    """异步主函数，用于执行脚本逻辑。"""
    # 将JSON文件路径设置为脚本所在目录下的 'holidays.json'
    global JSON_FILE
    JSON_FILE = os.path.join(os.path.dirname(__file__), 'holidays.json')
    
    # 获取当前年份的节假日数据
    current_holidays = await get_current_year_holidays(JSON_FILE)
    
    # 打印摘要
    print_holidays_summary(current_holidays, datetime.date.today().year)
    
    # 示例：检查今天的日期
    await check_single_date(datetime.date.today(), JSON_FILE)
    
    # 示例：检查一个指定的日期
    await check_single_date(datetime.date(datetime.date.today().year, 10, 1), JSON_FILE)

# 主执行块，当直接运行此脚本时触发
if __name__ == "__main__":
    asyncio.run(main())
