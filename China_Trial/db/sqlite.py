# -*- coding: utf-8 -*-
import os
import time

from loguru import logger
from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class TrialSurface(Base):
    """ Trial 模型 """
    __tablename__ = "trial_information"
    caseId = Column(String, primary_key=True)
    caseName = Column(String(128))
    caseNo = Column(String(128))
    caseTitle = Column(String(128))
    publish_time = Column(String(128))

    def __repr__(self):
        return "TrialSurface(id: {}, name: {}, age: {})".format(self.caseId, self.caseName, self.caseNo, self.caseTitle)


class SqliteDB(object):
    def __init__(self) -> None:
        db_path = os.path.dirname(os.getcwd()) + "\\" + "database" + "\\" + "trial.db"
        # 建立 sqlite 连接
        sqlite_engine = create_engine(f"sqlite:///{db_path}", echo=False)
        Base.metadata.create_all(sqlite_engine, checkfirst=True)
        self.session = sessionmaker(bind=sqlite_engine)()

    def get_session(self):
        return self.session

    def query_quantity(self, case_id: str) -> bool:
        """
        统计值在表中的数据，也用于查询值是否存在于表中
        :param case_id: 案件 id
        :return: 返回 True 时则存在相同的 caseId, 反之亦然
        """
        count = self.session.query(TrialSurface).filter_by(caseId=case_id).count()
        if count > 0:
            logger.debug(f'caseId已存在,  caseId = {case_id}')
            return True
        else:
            return False

    def insert_value(self, case: dict) -> None:
        """
        向表中插入值
        :param case: 案件相关信息, 包含 caseName caseId caseNo 等
        :return:
        """
        self.session.add(TrialSurface(
            caseId=case["caseId"],
            caseName=case["caseName"],
            caseNo=case["caseNo"],
            caseTitle=case["caseTitle"],
            publish_time=case["time"]
        ))
        self.session.commit()
        logger.debug(f"插入数据 {case}")

    def sqlite_dedup(self, case: dict) -> bool:
        """
        去重模块, 针对 caseId 进行去重
        :param case:
        :return: 存在则返回True, 不存在返回False
        """
        # 如果表中存在 caseId 则跳过
        if self.query_quantity(case["caseId"]):
            return True
        else:
            return False
