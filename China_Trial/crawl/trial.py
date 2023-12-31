# -*- coding: utf-8 -*-
import os
import re
import sys
import time
import random
import datetime
import argparse
import pandas as pd
from queue import Queue
from typing import Generator, Union
from random import choice
from concurrent.futures import ThreadPoolExecutor

import parsel
import requests
from rich.progress import Progress, SpinnerColumn, TextColumn, TaskID
from rich.console import Console
from rich.table import Table
from rich import box
from loguru import logger
from terminal_layout import Fore
from terminal_layout.extensions.choice import Choice, StringStyle

sys.path.append(os.path.dirname(os.path.dirname(os.getcwd())))
from China_Trial import settings
from China_Trial.db import SqliteDB


class TrialCrawl:
    def __init__(self, area_code: str = None, save_path: str = None) -> None:
        self.area_code = area_code
        self.save_path = save_path
        self.session = requests.Session()

        self.sqlite = SqliteDB()

        self.thread_pool = ThreadPoolExecutor(max_workers=15)
        self.console = Console()

    @staticmethod
    def _check_the_folder_exists(path: str) -> bool:
        """ 查看文件夹是否存在 """
        return os.path.exists(path)

    @staticmethod
    def _create_folder(path: str) -> None:
        """ 创建文件夹 """
        os.mkdir(path)
        logger.debug(f"创建文件夹 {path}")

    @staticmethod
    def _encapsulate_headers() -> dict:
        """ 封装 headers, 获取配置文件中的 cookie 传入 """
        return {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Cache-Control": "no-cache",
            "Cookie": settings.COOKIE,
            "Pragma": "no-cache",
            "Proxy-Connection": "close",
            "Referer": "http://tingshen.court.gov.cn/",
            "User-Agent": choice(settings.USER_AGENT_LIST),
            "X-Requested-With": "XMLHttpRequest"
        }

    @staticmethod
    def _response_to_dom(response: requests.Response) -> parsel.Selector:
        """ 将响应的 response 转换成 dom 树 """
        return parsel.Selector(response.text)

    @staticmethod
    def _progress() -> Progress:
        """ 进度条 """
        progress = Progress(
            SpinnerColumn(spinner_name='dots12', style='gray74'),
            TextColumn("[gray74]{task.fields[filename]}", justify="right"),
            "•",
            "[gray74]{task.completed}/{task.total} ts files"
            " •",
            "[gray74]{task.percentage:>3.0f}%",
        )
        return progress

    @staticmethod
    def download_ts_file(ts_file: dict, ts_content_buffer: list, progress: Progress, task_id: TaskID) -> None:
        """ 下载视频 ts 文件 """
        chunks = []
        response = requests.get(url=ts_file["ts_url"], stream=True)
        for chunk in response.iter_content(chunk_size=2048):
            if not chunk:
                break
            chunks.append(chunk)

        ts_content_buffer.append((ts_file["count"], b''.join(chunks)))
        progress.update(task_id, advance=1)

    def _table(self, case: dict) -> None:
        """ 表格显示 """
        table = Table(
            title="Trial Information",
            caption="table",
            caption_justify="right",
            box=box.MINIMAL
        )

        table.add_column("Publish", style="gray74", justify="center", no_wrap=True)
        table.add_column("Title", style="gray74", justify="center")
        table.add_column("No", style="gray74", justify="center")
        table.add_column("Id", style="gray74", justify="center")

        table.add_row(
            case["time"],
            case["caseTitle"],
            case["caseNo"],
            str(case["caseId"])
        )
        self._header()
        self.console.print(table, justify="center")

    def _header(self) -> None:
        self.console.print()
        self.console.rule(style="gray74")
        self.console.print()

    def _check_cookie_availability(self) -> bool:
        """ 检查 cookie 可用性 """
        url = 'http://tingshen.court.gov.cn/u/collect'
        response = self.session.get(url=url, headers=self._encapsulate_headers())
        dom = self._response_to_dom(response)

        # 查询用户信息, 判断 cookie 是否有效
        username = dom.xpath('//h3[@id="username"]/text()')
        if not username:
            logger.error('cookie 已失效, 请及时更新')
            return False
        else:
            return True

    def parse_province_court(self) -> list[dict]:
        """ 查询省份所有法院信息 """
        url = 'http://tingshen.court.gov.cn/court/courts'
        params = {"areaCode": self.area_code}
        response = self.session.get(url=url, params=params, headers=self._encapsulate_headers()).json()
        courts = response.get('data').get('courts')
        # 市级名称
        city_names = []
        city_names_dict = {}
        for index, court in enumerate(courts):
            city_names.append(court["courtName"])
            city_names_dict[court["courtName"]] = index

        # 选择需要爬取的市级
        c = Choice(
            title="请选择要爬取的法院",
            icon=">  ",
            choices=city_names,
            icon_style=StringStyle(fore=Fore.green),
            selected_style=StringStyle(fore=Fore.green)
        )
        index, choice_case_name = c.get_choice()
        case_name_index = city_names_dict[choice_case_name]

        # 获取指定需爬取的市级下所有法院信息
        current_city_cases = []
        if courts[case_name_index]["courts"]:
            for current_c in courts[case_name_index]["courts"]:
                current_city_cases.append(
                    {
                        "courtName": current_c["courtName"],
                        "courtCode": current_c["courtCode"],
                        "courtLevel": int(current_c["type"]) - 1
                    }
                )
        else:
            current_city_cases.append(
                {
                    "courtName": courts[case_name_index]["courtName"],
                    "courtCode": courts[case_name_index]["courtCode"],
                    "courtLevel": int(courts[case_name_index]["type"]) - 1
                }
            )
        logger.success(f"共获取{choice_case_name} {len(current_city_cases)}家法院信息")
        return current_city_cases

    def parse_case_id(self, court_information: dict) -> Generator:
        """ 获取当地法庭所上传的庭审视频 """
        for page in range(1, 101):
            query_api = 'http://tingshen.court.gov.cn/search/a/revmor/full'
            params = {
                "unUnionIds": "",
                "label": "",
                "courtCode": court_information["courtCode"],
                "catalogId": "",
                "pageNumber": page,
                "courtLevel": court_information["courtLevel"],
                "dataType": "2",
                "pageSize": "15",
                "level": "0",
                "extType": "",
                "isOts": "",
                "timeFlag": "0",
                "keywords": ""
            }
            logger.info(f"当前解析 {court_information['courtName']}第{page}页")

            response = self.session.get(url=query_api, headers=self._encapsulate_headers(), params=params).json()
            time.sleep(random.uniform(5, 6))

            # 提取案件相关信息
            for result in response["resultList"]:
                dt_object = datetime.datetime.fromtimestamp(float(str(result["beginTime"])[:-3]))
                readable_time = dt_object.strftime('%Y-%m-%d %H:%M:%S')
                yield {
                    "caseName": result["courtName"],
                    "caseId": result["caseId"],
                    "caseNo": result["caseNo"],
                    "caseTitle": result["title"],
                    "time": readable_time
                }

    def parse_play_url(self, case: dict) -> str:
        """
        获取 playUrl, playUrl 中包含对应庭审视频的 m3u8文件
        :param case:
        :return: playUrl
        """
        url = 'http://tingshen.court.gov.cn/cmn/showPlay'
        params = {
            "caseId": case["caseId"]
        }
        response = self.session.get(url=url, headers=self._encapsulate_headers(), params=params).json()
        time.sleep(random.uniform(1, 1.5))

        # 提取 playUrl
        play_url = response.get('data').get('playUrl')
        if 'http:' not in play_url:
            play_url = "http:" + play_url

        return play_url

    def parse_m3u8_file(self, play_url: str) -> str:
        """
        获取 m3u8 文件下载链接
        :param play_url:
        :return:
        """
        response = self.session.get(url=play_url).text
        m3u8_file = re.findall("url: '(.*?)',", response)[0]
        if 'm3u8' not in m3u8_file:
            return ""

        if 'http' not in m3u8_file:
            m3u8_file = 'http:' + m3u8_file

        return m3u8_file

    def parse_ts_file(self, m3u8_url: str) -> Union[Queue, bool]:
        """
        获取 m3u8 文件中所有 ts 文件, 并返回 ts 文件队列
        :param m3u8_url:
        :return:
        """
        ts_count = 1
        ts_task_queue = Queue()
        response = self.session.get(url=m3u8_url)
        if response.status_code != 200:
            return False

        for ts_file in response.text.split('\n'):
            if '#' in ts_file or not ts_file:
                continue
            if 'http' not in ts_file:
                ts_file = 'http:' + ts_file

            ts_task_queue.put({'count': ts_count, 'ts_url': ts_file})
            ts_count += 1

        return ts_task_queue

    def save_video(self, ts_content_buffer: list, case: dict) -> None:
        """ 保存 """
        case_title = case["caseTitle"] + "_" + str(case["caseId"])
        save_file_name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', '', case_title) + '.mp4'
        save_file_path = os.path.join(self.save_path, save_file_name)
        with open(save_file_path, mode='wb') as f:
            for buffer in ts_content_buffer:
                f.write(buffer[1])

        time.sleep(random.uniform(2.5, 3))

    def download_engine(self, case: dict) -> None:
        """
        视频解析下载引擎
        :param case: 案件的详细信息
        :return:
        """
        # self._table(case)
        play_url = self.parse_play_url(case)
        m3u8_file = self.parse_m3u8_file(play_url)
        if not m3u8_file:
            return

        ts_task_queue = self.parse_ts_file(m3u8_file)
        if ts_task_queue is False:
            return

        # 实例化进度条
        ts_content_buffer = []
        thread_futures = []
        progress = self._progress()
        task_id = progress.add_task(
            "Download",
            filename=case["caseTitle"] + "_" + str(case["caseId"]),
            total=ts_task_queue.qsize()
        )
        with progress:
            progress.update(task_id)
            while not ts_task_queue.empty():
                future = self.thread_pool.submit(self.download_ts_file, ts_task_queue.get(),
                                                 ts_content_buffer, progress, task_id)
                thread_futures.append(future)

            # 等待任务完成
            for tf in thread_futures:
                tf.result()

        # 保存
        ts_content_buffer = sorted(ts_content_buffer, key=lambda x: x[0])
        self.save_video(ts_content_buffer, case)

    def _scheduler(self) -> None:
        """ 调度器 """
        # 检查cookie是否可用
        if self._check_cookie_availability() is True:
            court_information_list = self.parse_province_court()  # 获取所有法庭信息
            for court_information in court_information_list:
                # 获取案件信息
                for case in self.parse_case_id(court_information):
                    # 对案件 id 进行去重
                    if not self.sqlite.sqlite_dedup(case):
                        self.download_engine(case)
                        self.sqlite.insert_value(case)

    def _engine(self) -> None:
        """ 系统引擎 """
        # 检查保存路径是否存在
        if self._check_the_folder_exists(self.save_path) is False:
            self._create_folder(self.save_path)

        # 启动调度器
        self._scheduler()

        # 关闭线程池
        self.thread_pool.shutdown()

    def start(self) -> None:
        self._engine()


if __name__ == '__main__':
    current_location = os.getcwd()
    default_save_path = os.path.join(os.path.dirname(current_location), "save_video")

    parser = argparse.ArgumentParser(description="")
    parser.add_argument("-ac", "--area_code", type=str, help="爬取的省份code")
    parser.add_argument("-sp", "--save_path", type=str, help="保存路径", default=default_save_path)
    parser.add_argument("-f", "--file", type=str, help="file path")

    args = parser.parse_args()
    ac = args.area_code
    sp = args.save_path
    file_path = args.file
    if file_path:
        df = pd.read_excel(file_path, sheet_name="贵州")
        urls = df["链接"].tolist()
        ids = [url.split("/")[-1] for url in urls]
        names = df["案件名"].tolist()
        for i, n in zip(ids, names):
            data_dict = {
                "caseId": i,
                "caseTitle": i
            }
            TrialCrawl(save_path=sp).download_engine(data_dict)
    else:
        TrialCrawl(area_code=ac, save_path=sp).start()
