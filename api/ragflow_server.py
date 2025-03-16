#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

# 导入日志初始化工具并初始化根日志记录器
from api.utils.log_utils import initRootLogger
initRootLogger("ragflow_server")

# 导入所需的模块
import logging
import os
import signal
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
import threading

# 导入 Werkzeug 的 run_simple 函数用于启动 HTTP 服务器
from werkzeug.serving import run_simple
from api import settings
from api.apps import app
from api.db.runtime_config import RuntimeConfig
from api.db.services.document_service import DocumentService
from api import utils

# 导入数据库初始化相关函数
from api.db.db_models import init_database_tables as init_web_db
from api.db.init_data import init_web_data
from api.versions import get_ragflow_version
from api.utils import show_configs
from rag.settings import print_rag_settings
from rag.utils.redis_conn import RedisDistributedLock

# 创建一个线程事件，用于通知程序停止
stop_event = threading.Event()

# 定义更新进度的函数
def update_progress():
    # 创建一个 Redis 分布式锁，超时时间为 60 秒
    redis_lock = RedisDistributedLock("update_progress", timeout=60)
    while not stop_event.is_set():  # 当 stop_event 未被设置时循环执行
        try:
            if not redis_lock.acquire():  # 尝试获取锁，如果失败则继续循环
                continue
            # 调用 DocumentService 的 update_progress 方法更新进度
            DocumentService.update_progress()
            # 等待 6 秒
            stop_event.wait(6)
        except Exception:
            # 记录异常信息
            logging.exception("update_progress exception")
        finally:
            # 释放锁
            redis_lock.release()

# 定义信号处理函数
def signal_handler(sig, frame):
    # 记录接收到中断信号的信息
    logging.info("Received interrupt signal, shutting down...")
    # 设置 stop_event 以通知所有线程停止
    stop_event.set()
    # 等待 1 秒
    time.sleep(1)
    # 退出程序
    sys.exit(0)

# 主程序入口
if __name__ == '__main__':
    # 记录 RAGFlow 的启动标志
    logging.info(r"""
        ____   ___    ______ ______ __               
       / __ \ /   |  / ____// ____// /____  _      __
      / /_/ // /| | / / __ / /_   / // __ \| | /| / /
     / _, _// ___ |/ /_/ // __/  / // /_/ /| |/ |/ / 
    /_/ |_|/_/  |_|\____//_/    /_/ \____/ |__/|__/                             
    """)
    # 记录 RAGFlow 的版本信息
    logging.info(f'RAGFlow version: {get_ragflow_version()}')
    # 记录项目基础目录
    logging.info(f'project base: {utils.file_utils.get_project_base_directory()}')
    # 显示配置信息
    show_configs()
    # 初始化设置
    settings.init_settings()
    # 打印 RAG 设置
    print_rag_settings()

    # 初始化数据库表
    init_web_db()
    # 初始化数据库数据
    init_web_data()

    # 解析命令行参数
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--version", default=False, help="RAGFlow version", action="store_true"
    )
    parser.add_argument(
        "--debug", default=False, help="debug mode", action="store_true"
    )
    args = parser.parse_args()
    # 如果传入了 --version 参数，打印版本信息并退出
    if args.version:
        print(get_ragflow_version())
        sys.exit(0)

    # 设置调试模式
    RuntimeConfig.DEBUG = args.debug
    if RuntimeConfig.DEBUG:
        logging.info("run on debug mode")

    # 初始化运行时环境
    RuntimeConfig.init_env()
    # 初始化运行时配置，设置服务器主机和端口
    RuntimeConfig.init_config(JOB_SERVER_HOST=settings.HOST_IP, HTTP_PORT=settings.HOST_PORT)

    # 设置信号处理函数，用于捕获 SIGINT 和 SIGTERM 信号
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 创建一个线程池，最大工作线程数为 1
    thread = ThreadPoolExecutor(max_workers=1)
    # 提交 update_progress 函数到线程池中执行
    thread.submit(update_progress)

    # 启动 HTTP 服务器
    try:
        logging.info("RAGFlow HTTP server start...")
        run_simple(
            hostname=settings.HOST_IP,  # 服务器主机地址
            port=settings.HOST_PORT,    # 服务器端口
            application=app,            # Flask 应用
            threaded=True,              # 启用多线程模式
            use_reloader=RuntimeConfig.DEBUG,  # 调试模式下启用自动重载
            use_debugger=RuntimeConfig.DEBUG,  # 调试模式下启用调试器
        )
    except Exception:
        # 如果发生异常，打印堆栈跟踪信息
        traceback.print_exc()
        # 设置 stop_event 以通知所有线程停止
        stop_event.set()
        # 等待 1 秒
        time.sleep(1)
        # 强制终止当前进程
        os.kill(os.getpid(), signal.SIGKILL)