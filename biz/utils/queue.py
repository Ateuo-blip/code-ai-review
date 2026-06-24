import os
from multiprocessing import Process

from biz.utils.log import logger


def handle_queue(function: callable, data: any, token: str, url: str, url_slug: str):
    process = Process(target=function, args=(data, token, url, url_slug))
    process.start()
