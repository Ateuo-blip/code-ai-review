"""
路由注册模块（GitLab 专用版）
"""
from biz.api.routes import webhook


def register_routes(app):
    app.register_blueprint(webhook.webhook_bp)
