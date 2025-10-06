import datetime
import json
import os
from chinese_calendar import is_holiday, is_workday
import chinese_calendar as calendar
from cn_bing_translator import Translator
import chinese_calendar

# JSON 文件路径
JSON_FILE = 'holidays.json'

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

def load_holidays_from_json():
    """从 JSON 文件加载节假日数据"""
    if os.path.exists(JSON_FILE):
        with open(JSON_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get('year'), data.get('holidays', [])
    return None, []

def save_holidays_to_json(year, holidays):
    """保存节假日数据到 JSON 文件"""
    data = {
        'year': year,
        'holidays': holidays
    }
    with open(JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"节假日数据已保存到 {JSON_FILE}")

def get_year_holidays(year):
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
            'is_in_lieu': is_lieu
        }
        
        if on_holiday and holiday_name:
            translated_name = translate_holiday_name(holiday_name)
            holiday_info['holiday_name'] = translated_name
            
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

# 主逻辑：检查并获取当前年份节假日
current_year = datetime.date.today().year
saved_year, saved_holidays = load_holidays_from_json()

if saved_year == current_year and saved_holidays:
    print(f"已加载 {current_year} 年节假日数据，共 {len(saved_holidays)} 条记录。")
    holidays = saved_holidays
else:
    print(f"未找到 {current_year} 年数据或需更新，正在重新获取...")
    holidays = get_year_holidays(current_year)
    save_holidays_to_json(current_year, holidays)

# 输出摘要
print(f"\n{current_year} 年节假日摘要：")
total_days = len(holidays)
holiday_count = sum(1 for h in holidays if h['is_holiday'])
workday_count = sum(1 for h in holidays if h['is_workday'])
lieu_count = sum(1 for h in holidays if h['is_in_lieu'])
print(f"总天数：{total_days}")
print(f"总节假日数：{holiday_count}")
print(f"总工作日数：{workday_count}")
print(f"调休日数：{lieu_count}")

# 示例：原有单个日期判断逻辑（保留）
# date_input = datetime.date(2025, 10, 9)
# on_holiday, holiday_name = calendar.get_holiday_detail(date_input)
# is_hol = is_holiday(date_input)
# is_work = is_workday(date_input)
# is_lieu = calendar.is_in_lieu(date_input)
#
# if is_hol:
#     translated_name = translate_holiday_name(holiday_name)
#     print(f"\n示例：{date_input} 是假期，{translated_name}")
# elif is_work:
#     print(f"\n示例：{date_input} 是工作日")
# if is_lieu:
#     print(f"{date_input} 是调休")
# else:
#     print(f"{date_input} 不是调休")
