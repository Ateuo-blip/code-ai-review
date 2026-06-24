"""
API 服务主程序入口（GitLab 专用版）
"""
from dotenv import load_dotenv

# 必须在其他导入之前加载环境变量
load_dotenv("conf/.env")

import os

from biz.api import api_app, init_app
from biz.utils.config_checker import check_config

# 初始化应用并注册路由
init_app(api_app)

if __name__ == '__main__':
    check_config()

    # 启动Flask API服务
    port = int(os.environ.get('SERVER_PORT', 5001))
    api_app.run(host='0.0.0.0', port=port)
