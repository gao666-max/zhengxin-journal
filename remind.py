# -*- coding: utf-8 -*-
"""正心 · 每日提醒：今天没写日记就弹通知，点击打开日记页面。加 --force 强制弹（测试用）"""
import os
import sys
import json
import datetime
import winreg

from winotify import Notification

HERE = os.path.dirname(os.path.abspath(__file__))
APP_ID = "ZhengXinReminder"
URL = "http://127.0.0.1:8900"


def register_aumid():
    """注册 AppUserModelID（无快捷方式时让 toast 能稳定显示）"""
    try:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\AppUserModelId\ZhengXinReminder")
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "正心")
        winreg.CloseKey(key)
    except Exception:
        pass


def already_journaled():
    """今天已经写过（data/YYYY-MM-DD.json 非空）就返回 True，不再打扰"""
    f = os.path.join(HERE, "data", datetime.date.today().isoformat() + ".json")
    if not os.path.exists(f):
        return False
    try:
        return bool(json.loads(open(f, encoding="utf-8").read()))
    except Exception:
        return False


if "--force" in sys.argv or not already_journaled():
    register_aumid()
    Notification(
        app_id=APP_ID,
        title="今天写日记了吗～",
        msg="点开记两笔吧，几秒钟就好",
        duration="short",
        launch=URL,
    ).show()
