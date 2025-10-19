import asyncio
import json
import os
from datetime import datetime, date, timedelta
from chinese_calendar import is_holiday, is_workday
import chinese_calendar as ch_calendar
from cn_bing_translator import Translator
from astrbot.api import logger

class HolidayManager:
    """
    管理节假日数据的获取、缓存和查询。
    """
    def __init__(self, json_file: str):
        """
        初始化 HolidayManager。

        Args:
            json_file (str): 缓存文件的路径。
        """
        self.json_file = json_file
        self.holidays = []
        self.year = None

    async def get_holidays_for_year(self, year: int) -> list:
        """
        获取指定年份的节假日数据，优先从缓存加载。

        Args:
            year (int): 要获取数据的年份。

        Returns:
            list: 指定年份的节假日数据列表。
        """
        if self.year == year and self.holidays:
            logger.info(f"已从内存缓存加载 {year} 年节假日数据。")
            return self.holidays

        saved_year, saved_holidays = self._load_from_json()
        if saved_year == year and saved_holidays:
            logger.info(f"已从文件缓存加载 {year} 年节假日数据。")
            self.year = saved_year
            self.holidays = saved_holidays
            return self.holidays
        
        logger.info(f"未找到 {year} 年的缓存或数据已过时，正在重新获取...")
        new_holidays = await self._fetch_from_source(year)
        self._save_to_json(year, new_holidays)
        self.year = year
        self.holidays = new_holidays
        print_holidays_summary(self.holidays, year)
        return self.holidays

    def _load_from_json(self) -> tuple[int | None, list]:
        """从JSON文件加载缓存的节假日数据。"""
        if os.path.exists(self.json_file):
            try:
                with open(self.json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data.get('year'), data.get('holidays', [])
            except Exception as e:
                logger.error(f"从 {self.json_file} 加载节假日数据失败: {e}")
        return None, []

    def _save_to_json(self, year: int, holidays: list):
        """将节假日数据保存到JSON文件。"""
        data = {'year': year, 'holidays': holidays}
        try:
            with open(self.json_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"节假日数据已保存到 {self.json_file}")
        except Exception as e:
            logger.error(f"保存节假日数据到 {self.json_file} 失败: {e}")

    async def _fetch_from_source(self, year: int) -> list:
        """从 `chinese_calendar` 库获取原始节假日数据。"""
        start_date = date(year, 1, 1)
        end_date = date(year, 12, 31)
        holidays_data = []
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
                    'is_first_day': False,
                    'is_last_day': False
                }
                
                if on_holiday and holiday_name:
                    translated_name = await self._translate_holiday_name(holiday_name)
                    holiday_info['holiday_name'] = translated_name
                    
                    if not holidays_data or not holidays_data[-1]['is_holiday'] or holidays_data[-1]['holiday_name'] != translated_name:
                        holiday_info['is_first_day'] = True
                    
                    if translated_name != prev_holiday_name:
                        logger.info(f"{current_date} 是节假日: {translated_name}")
                        if is_lieu:
                            logger.info(f"  -> {current_date} 是调休日")
                        prev_holiday_name = translated_name
                
                holidays_data.append(holiday_info)
                
            except Exception as e:
                logger.warning(f"处理日期 {current_date} 时出错: {e}")
                holidays_data.append({
                    'date': current_date.isoformat(), 'holiday_name': '', 'is_holiday': False,
                    'is_workday': True, 'is_in_lieu': False, 'is_first_day': False, 'is_last_day': False
                })
            
            current_date += timedelta(days=1)
        
        for i in range(len(holidays_data) - 1, -1, -1):
            if holidays_data[i]['is_holiday'] and (i == len(holidays_data) - 1 or not holidays_data[i+1]['is_holiday']):
                holidays_data[i]['is_last_day'] = True
                
        return holidays_data

    async def _translate_holiday_name(self, holiday_name: str) -> str:
        """使用必应翻译将英文节假日名称翻译为中文。"""
        if not holiday_name:
            return ''
        try:
            translator = Translator(toLang='zh-Hans')
            result = await asyncio.to_thread(translator.process, holiday_name)
            return result if result else holiday_name
        except Exception as e:
            logger.warning(f"翻译节日名称 '{holiday_name}' 失败: {e}")
            return holiday_name

def print_holidays_summary(holidays: list, year: int):
    """在日志中输出指定年份节假日数据的统计摘要。"""
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