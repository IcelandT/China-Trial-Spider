# -*- coding: utf-8 -*-
import re
import os

import requests


class ProvincialCrawl:
    def __init__(self):
        self.session = requests.session()

    @staticmethod
    def save_txt(provincials: list, codes: list) -> None:
        """ 保存 txt 文件 """
        save_path = os.path.dirname(os.path.abspath('.'))
        save_file_path = os.path.join(save_path, 'provincial-code.txt')
        with open(save_file_path, mode='w', encoding='utf-8') as f:
            for provincial, code in zip(provincials, codes):
                content = provincial + ': ' + code
                f.write(content + '\n')
                print(content)

    def parse_provincial_code(self) -> None:
        """ 解析省份对应的 code """
        url = 'http://sts.videoincloud.com/static/libs/raphael/mapData_aa5c121.js'
        response = self.session.get(url=url).text
        provincials = re.findall('areaName:"(.*?)",', response)
        codes = re.findall('post:"(.*?)"', response)
        self.save_txt(provincials, codes)


if __name__ == '__main__':
    ProvincialCrawl().parse_provincial_code()
