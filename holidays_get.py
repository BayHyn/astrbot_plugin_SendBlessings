import datetime
import json
import os
from chinese_calendar import is_holiday, is_workday
import chinese_calendar as calendar
from cn_bing_translator import Translator
import chinese_calendar

# JSON 文件路径（动态传入）
JSON_FILE = None  # 将在调用时设置

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
                if chinese_calendar.is_in_lieu(current_date):
                    print(f"{current_date} 是调休")
                prev_holiday_name = translated_name
            else:
                print(f"{current_date} 是{translated_name}假期")
                if chinese_calendar.is_in_lieu(current_date):
                    print(f"{current_date} 是调休")
        
        holidays.append(holiday_info)
        
        current_date += datetime.timedelta(days=1)
    
    return holidays

# 主逻辑：检查并获取当前年份节假日（现在参数化）
def get_current_year_holidays(json_file=None):
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

# 输出摘要（辅助函数）
def print_holidays_summary(holidays, year):
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

# 示例：原有单个日期判断逻辑（保留，作为独立函数）
def check_single_date(date_input, json_file=None):
    if json_file is None:
        holidays = get_current_year_holidays()
    else:
        # 假设已加载
        pass
    # 查找日期
    for h in holidays:
        if h['date'] == date_input.isoformat():
            on_holiday = h['is_holiday']
            holiday_name = h['holiday_name']
            is_hol = on_holiday
            is_work = h['is_workday']
            is_lieu = h['is_in_lieu']
            if is_hol:
                translated_name = holiday_name
                print(f"\n示例：{date_input} 是假期，{translated_name}")
            elif is_work:
                print(f"\n示例：{date_input} 是工作日")
            if is_lieu:
                print(f"{date_input} 是调休")
            else:
                print(f"{date_input} 不是调休")
            return
    print(f"{date_input} 未找到记录")
