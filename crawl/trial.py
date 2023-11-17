# -*- coding: utf-8 -*-
import os
import re
import sys
import time
import random
import datetime
import argparse
from typing import Generator
from random import choice
from concurrent.futures import ThreadPoolExecutor

import parsel
import requests
from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.getcwd())))
from China_Trial import settings
from China_Trial.database import SqliteDB


class TrialCrawl:
    def __init__(self, area_code: str) -> None:
        self.area_code = area_code
        self.session = requests.Session()

        self.sqlite = SqliteDB()

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

    def parse_province_court(self) -> list[dict]:
        """ 查询省份所有法院信息 """
        url = 'http://tingshen.court.gov.cn/court/courts'
        params = {"areaCode": self.area_code}
        response = self.session.get(url=url, params=params, headers=self._encapsulate_headers()).json()
        courts = response.get('data').get('courts')
        court_information_list = []
        for court in courts:
            # 跳过最高人民法院, 只获取最低级的辖区法院
            if court["type"] == 2:
                continue

            if court["courts"]:
                for c in court["courts"]:
                    court_information_list.append(
                        {
                            "courtName": c["courtName"],
                            "courtCode": c["courtCode"],
                            "courtLevel": int(c["type"]) - 1
                        }
                    )
            else:
                # 若无最低级的辖区法院, 则获取所在地的中级法院
                court_information_list.append(
                    {
                        "courtName": court["courtName"],
                        "courtCode": court["courtCode"],
                        "courtLevel": int(court["type"]) - 1
                    }
                )

        logger.success(f"共获取{courts[0]['areaName']}{len(court_information_list)}家法院信息")
        return court_information_list

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
            logger.info(f"当前解析  {court_information['courtName']}第{page}页")

            response = self.session.get(url=query_api, headers=self._encapsulate_headers(), params=params).json()
            time.sleep(random.uniform(5, 8))

            # 提取 caseId
            cases = []
            for result in response["resultList"]:
                dt_object = datetime.datetime.fromtimestamp(float(str(result["beginTime"])[:-3]))
                readable_time = dt_object.strftime('%Y-%m-%d %H:%M:%S')
                cases.append({
                    "caseName": result["courtName"],
                    "caseId": result["caseId"],
                    "caseNo": result["caseNo"],
                    "caseTitle": result["title"],
                    "time": readable_time
                })

            yield cases

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
        if 'http' not in m3u8_file:
            url = 'http:' + m3u8_file
        return m3u8_file

    def _download_engine(self, cases: list[dict]):
        """
        视频解析下载引擎
        :param cases: 去重后的 cases 列表   (后期优化使用任务队列？ 方便管理任务，更新 cookie)
        :return:
        """
        for case in cases:
            play_url = self.parse_play_url(case)
            m3u8_file = self.parse_m3u8_file(play_url)

    def _scheduler(self) -> None:
        """ 调度器 """
        if self._check_cookie_availability() is not False:
            court_information_list = self.parse_province_court()  # 获取所有法庭信息
            for court_information in court_information_list:
                # 获取案件信息
                for cases in self.parse_case_id(court_information):
                    # 对案件 id 进行去重
                    after_dedup_cases = self.sqlite.sqlite_dedup(cases)
                    self._download_engine(after_dedup_cases)

    def start(self) -> None:
        self._scheduler()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="")
    parser.add_argument("-ac", "--area_code", type=str, help="爬取的省份code", required=True)

    args = parser.parse_args()
    ac = args.area_code

    TrialCrawl(area_code=ac).start()
