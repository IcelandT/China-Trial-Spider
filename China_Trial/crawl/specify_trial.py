# -*- coding: utf-8 -*-
import argparse
from concurrent.futures import ThreadPoolExecutor
import os
import re

import requests
import pandas as pd
from loguru import logger
from queue import Queue
from rich.progress import Progress, TextColumn, SpinnerColumn, TaskID

import settings


class SpecifyTrialSpider(object):
    def __init__(self, task_file: str, save_path: str) -> None:
        self.task_file = task_file
        self.save_path = save_path

        # task queue
        self.task_queue = []

        self.thread_pool = ThreadPoolExecutor(max_workers=20)

    @staticmethod
    def _progress() -> Progress:
        """ 进度条 """
        progress = Progress(
            SpinnerColumn(spinner_name='dots12', style='gray74'),
            TextColumn("[gray74]{task.fields[filename]}", justify="right"),
            "•",
            "[gray74]{task.percentage:>3.0f}%",
        )
        return progress

    @property
    def enclosure_headers(self) -> dict:
        """ 封装 headers """
        return {
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
            "Host": "player.videoincloud.com",
            "Cookie": settings.COOKIE,
            "Referer": "http://tingshen.court.gov.cn/live/37114168?u_atoken=c5e16d69-183e-4688-aa81-908497cc6256&u_asession=01WeeJ6FY8BlhuXxWRfQefZ38Vd1ZjaceaLrR0S_eDeXVdZHuRPgUKlJzolrQsmkbV3VO1vwAdXxeaJzs9fMxe09sq8AL43dpOnCClYrgFm6o&u_asig=05Il5fzBncc_3GGVCLVu67Y3CPlsldIFf8DrrpbP7QhFGUlDYqx7vlHvnzBzdskMZXhLXUoHXQnELu1KDDPecFHUe3eCc-U9CfFhrmp3WYiZ_iqbEI8Z_olWIpQbOLiGQHhWN1UJRV8D7H2XXmSvSSeRJNhLaRxEbFwU9kPIUFhAaVQULC_zk2CmDhvAxlAeHDksmHjM0JOodanL5-M1Qs1XTvp0JgG9oKANLZuhbnKvV9WqsvVBnYR2XsDv0mWVEX4c3fFotPH0gQAZIx6g_fPPuZvDkhTuu_v6YzuQXu3vTY94r_LXIIil3Y3aVPRGAe&u_aref=ImvEXMnKZTrwXMnZza%2FTmCAwK4c%3D",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0",
            "X-Requested-With": "XMLHttpRequest"
        }

    def load_task_file(self) -> None:
        """ 读取任务文件 """
        task_file = pd.read_excel(self.task_file, engine='openpyxl')
        task_url = task_file.iloc[:, 0].tolist()
        for task in task_url:
            if not task or str(task) == 'nan':
                continue
            task_id = task.split('/')[-1]
            self.task_queue.append(task_id)
            logger.info(f'添加任务 taskID = {task_id}')

    def parse_engine(self) -> None:
        """ 解析引擎 """
        for task in self.task_queue:
            self.parse_play_url(task)

    def parse_play_url(self, task: str) -> None:
        """ 提取 playUrl """
        url = 'http://tingshen.court.gov.cn/cmn/showPlay'
        params = {"caseId": task}
        response = requests.get(url=url, headers=self.enclosure_headers, params=params).json()
        play_url = response.get('data').get('playUrl')   # 提取 playUrl
        if 'http:' not in play_url:
            play_url = 'http:' + play_url

        self.parse_m3u8_url(task, play_url)

    def parse_m3u8_url(self, task: str, play_url: str) -> None:
        """ 提取 m3u8 url """
        response = requests.get(url=play_url, headers=self.enclosure_headers).text
        url = re.findall("url: '(.*?)',", response)[0]
        if 'http' not in url:
            url = 'http:' + url

        self.parse_ts_file(task, url)

    def parse_ts_file(self, task: str, url: str) -> None:
        """ 解析 ts 文件 """
        response = requests.get(url=url, headers=self.enclosure_headers).text
        count = 1
        ts_buffer = Queue()
        for ts_url in response.split('\n'):
            if '#' in ts_url or not ts_url:
                continue
            if 'http' not in ts_url:
                ts_url = 'http:' + ts_url
            ts_buffer.put({'count': count, 'ts_url': ts_url})
            count += 1

        progress = self._progress()
        task_id = progress.add_task('download', filename=task, total=int(ts_buffer.qsize()), start=False)    # 手动启动任务, start=False
        buffers = []
        futures = []
        with progress:
            progress.start_task(task_id)
            while not ts_buffer.empty():
                ts = ts_buffer.get()
                future = self.thread_pool.submit(self.download_ts_file, task_id, ts, buffers, progress)
                futures.append(future)

            # 等待线程池中任务完成
            for f in futures:
                f.result()

        # 保存
        buffers = sorted(buffers, key=lambda x: x[0])
        self.save_video(buffers, task)

    def download_ts_file(self, task_id: TaskID, ts: dict, buffers: list, progress: Progress) -> None:
        """ 下载 ts 文件 """
        chunks = []
        response = requests.get(url=ts["ts_url"], stream=True)
        for chunk in response.iter_content(chunk_size=4096):
            if not chunk:
                break
            chunks.append(chunk)

        buffers.append((ts["count"], b''.join(chunks)))
        progress.update(task_id, advance=1)

    def save_video(self, buffers: list, task: str) -> None:
        """ 保存 """
        save_file_name = "云南话_" + task + '.mp4'
        save_file_path = os.path.join(self.save_path, save_file_name)
        with open(save_file_path, mode='wb') as f:
            for buffer in buffers:
                f.write(buffer[1])

    def start(self) -> None:
        # 读取文件
        self.load_task_file()
        # 启动引擎
        self.parse_engine()
        self.thread_pool.shutdown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="")
    parser.add_argument('-t', '--task_file', type=str)
    parser.add_argument('-s', '--save_path', type=str)

    args = parser.parse_args()
    specify_trial_spider = SpecifyTrialSpider(args.task_file, args.save_path)
    specify_trial_spider.start()
