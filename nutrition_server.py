#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
饮食记录 —— 网页版本地服务器
用法: python3 nutrition_server.py
然后浏览器打开 http://localhost:8420

数据完全复用 nutrition_tracker.py 里的逻辑和两份CSV文件
（nutrition_log.csv / nutrition_daily_summary.csv），
跟终端版是同一套数据，两边可以混用，不会冲突。

这个文件不依赖任何第三方库，只用Python标准库，不需要pip install。
"""

import json
import os
import secrets
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import nutrition_tracker as nt

# Render这类平台会把标准输出当成日志来源，但Python默认在非终端环境下用
# "全缓冲"模式——print的内容攒在缓冲区里不会立刻发出去，对一个长期运行、
# 不会主动退出的服务来说，日志可能会一直卡在缓冲区里，导致你在Render的
# 日志页面上看不到任何实时输出。强制改成"行缓冲"：每写完一行就立刻发送，
# 不再等缓冲区攒满。
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

PORT = 8420

# ---------------------------------------------------------------------------
# 登录验证（session cookie + 密码，允许多个人各自有自己的账号）
#
# 用的是"服务端session token + HttpOnly cookie"这套机制——这是几乎所有网站
# 登录功能底层都在用的标准做法（哪怕是Google登录，最后一步落地也是这个），
# 不是简化版或者不入流的方案。跟"真正的OAuth第三方登录(比如Google一键登录)"
# 相比，区别只在于"验证密码"这一步是自己比对，不是交给Google去验证——
# OAuth更"标准"，但需要去Google Cloud Console注册应用、处理回调地址这些
# 额外步骤，对你这个个人小工具来说投入产出不成正比，先用这一版。
#
# 账号数据现在存在users.json里(nutrition_tracker.py里的USERS)，环境变量
# NUTRITION_USERS/NUTRITION_ADMIN只在这个文件还不存在时用来做初始种子数据，
# 之后新增/删除账号都是管理员通过网页操作，改的是这个文件，不是环境变量。
#
# 本地跑（种子账号都没配）时，不启用登录验证，效果跟以前一样直接能用。
# ---------------------------------------------------------------------------
AUTH_ENABLED = bool(nt.USERS)

# 部署到Render(有PORT环境变量)时用HTTPS，cookie加Secure标记更安全；
# 本地用http://localhost跑的时候，浏览器不会通过明文HTTP发送Secure cookie，
# 所以本地必须不加这个标记，否则登录会莫名其妙一直失败。
SECURE_COOKIE = "PORT" in os.environ

SESSION_TTL_SECONDS = 30 * 24 * 3600  # session有效期30天，不用每次打开都重新登录
_sessions = {}  # token -> {"user": 用户名, "is_admin": 是否管理员, "expires": 过期时间戳}


def _create_session(username):
    token = secrets.token_hex(24)
    _sessions[token] = {
        "user": username,
        "is_admin": nt.is_admin_user(username),
        "expires": time.time() + SESSION_TTL_SECONDS,
    }
    return token


def _get_session(token):
    session = _sessions.get(token)
    if not session:
        return None
    if session["expires"] < time.time():
        _sessions.pop(token, None)
        return None
    return session


def _get_session_user(token):
    session = _get_session(token)
    return session["user"] if session else None


def _parse_cookies(header_value):
    cookies = {}
    if not header_value:
        return cookies
    for part in header_value.split(";"):
        if "=" in part:
            k, _, v = part.strip().partition("=")
            cookies[k] = v
    return cookies


def _parse_client_datetime(raw):
    """
    解析前端传来的本地时间字符串(格式 "YYYY-MM-DDTHH:MM:SS")，
    返回 (日期字符串, datetime对象)，解析失败或没传就都返回None，
    调用方各自决定要不要退回到服务器自己的时间。
    """
    if not raw:
        return None, None
    try:
        dt = nt.datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S")
        return dt.strftime("%Y-%m-%d"), dt
    except ValueError:
        return None, None


def get_week_payload(days=7, client_today=None):
    """
    返回过去n天的结构化数据（不是渲染好的文本），
    交给前端用CSS画柱状图，不依赖等宽字体里中文=2倍宽度这个在浏览器里不成立的假设。
    终端版(nutrition_tracker.py里的print_weekly_bar_chart)不受影响，继续用文本渲染，
    因为终端环境这个假设是成立的。

    client_today: 浏览器传来的、用户本地时区的今天日期字符串(YYYY-MM-DD)。
    Render服务器跑在UTC，不能用服务器自己的系统时间判断"今天是哪天"——
    那样一到晚上(美东时间)服务器那边已经跨天了，会把"今天"错判成明天。
    """
    dates, data = nt.get_last_n_days_data(days, today_override=client_today)
    veg_days = nt.get_tracked_days("vegetable")
    vit_days = nt.get_tracked_days("vitamin")
    today_str = nt._resolve_today(client_today).strftime("%Y-%m-%d")

    week = []
    for d in dates:
        key = d.strftime("%Y-%m-%d")
        week.append({
            "date": key,
            "label": d.strftime("%m-%d"),
            "is_today": key == today_str,
            "kcal": data[key]["kcal"],
            "protein": data[key]["protein"],
            "carb": data[key]["carb"],
            "fat": data[key]["fat"],
            "veg": key in veg_days,
            "vitamin": key in vit_days,
        })
    return week


def _get_users_list():
    return [
        {"username": username, "is_admin": info.get("is_admin", False)}
        for username, info in nt.USERS.items()
    ]


def get_foods_list(client_today=None):
    eaten_today = nt.get_today_servings_by_food(today_override=client_today)
    return [
        {
            "id": food_id,
            "name": item["name"],
            "unit": item["unit"],
            "kcal": item["kcal"],
            "protein": item["protein"],
            "carb": item["carb"],
            "fat": item["fat"],
            "tracker": item.get("tracker"),
            "eaten_today": eaten_today.get(food_id, 0.0),
        }
        for food_id, item in nt.FOODS.items()
    ]


def get_data_payload(days=7, client_today=None):
    nt.write_summary_csv()  # 顺手把导出用的汇总文件也同步一次，不影响图表本身的计算
    target_kcal, target_protein, target_carb, target_fat = nt.get_targets()
    return {
        "week": get_week_payload(days, client_today),
        "foods": get_foods_list(client_today),
        "target_kcal": target_kcal,
        "target_protein": target_protein,
        "target_carb": target_carb,
        "target_fat": target_fat,
    }


HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>饮食记录</title>
<style>
  :root {
    --bg: #1a1d24;
    --panel: #22262f;
    --ink: #dcdde0;
    --ink-soft: #838995;
    --sage: #4a7c6f;
    --sage-dim: #5f9186;
    --border: #333944;
    --flash: #2a2f3a;
    --track: #2a2f3a;
    --danger: #c1595f;
    --danger-flash: #3a2428;
    --modal-bg: #262019;
    --modal-border: #4a3d29;
    --modal-accent: #c9a15c;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0;
    background: var(--bg);
    color: var(--ink);
    font-family: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, "Liberation Mono", monospace;
    min-height: 100vh;
  }
  body {
    display: flex;
    justify-content: center;
    padding: 20px 16px;
  }
  .panel {
    width: 100%;
    max-width: 680px;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 18px 20px;
  }

  .page-header {
    position: relative;
    text-align: center;
    margin-bottom: 14px;
    padding-top: 2px;
  }
  .page-title {
    font-size: 21px;
    font-weight: 700;
    color: var(--sage);
    letter-spacing: 0.1em;
    margin: 0;
  }
  .user-badge {
    position: absolute;
    top: 4px;
    right: 0;
    font-size: 11px;
    color: var(--ink-soft);
  }

  .top-bar {
    display: flex;
    align-items: center;
    gap: 5px;
    flex-wrap: wrap;
  }
  .btn-small {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--ink-soft);
    font-family: inherit;
    font-size: 11px;
    padding: 6px 8px;
    border-radius: 5px;
    cursor: pointer;
    white-space: nowrap;
  }
  .btn-small:hover { background: var(--flash); }
  .clear-btn {
    background: transparent;
    border: 1px solid var(--danger);
    color: var(--danger);
    font-family: inherit;
    font-size: 11px;
    padding: 6px 8px;
    border-radius: 5px;
    cursor: pointer;
    white-space: nowrap;
  }
  .clear-btn:hover { background: var(--danger-flash); }
  #undo-btn { margin-left: auto; }

  .section-label {
    color: var(--ink-soft);
    font-size: 11px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    margin: 14px 0 8px;
    border-top: 1px solid var(--border);
    padding-top: 12px;
  }
  .section-label:first-of-type { border-top: none; padding-top: 0; margin-top: 0; }

  .chart-group { margin-bottom: 6px; }
  .chart-title {
    font-size: 12.5px;
    color: var(--ink);
    margin-bottom: 6px;
  }
  .chart-title .target { color: var(--ink-soft); font-size: 11px; }

  .bar-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 2px 0;
  }
  .bar-label {
    width: 44px;
    flex-shrink: 0;
    font-size: 11.5px;
    color: var(--ink-soft);
    text-align: right;
  }
  .bar-label.today { color: var(--sage); font-weight: 600; }
  .bar-today-tag {
    width: 30px;
    flex-shrink: 0;
    font-size: 10.5px;
    color: var(--sage);
  }
  .bar-track {
    position: relative;
    flex: 1;
    height: 12px;
    background: var(--track);
    border-radius: 3px;
    overflow: hidden;
  }
  .bar-fill {
    height: 100%;
    background: var(--sage);
    border-radius: 3px;
    transition: width 0.4s ease;
  }
  .bar-target-line {
    position: absolute;
    top: -2px;
    bottom: -2px;
    width: 0;
    border-left: 1.5px dashed var(--ink-soft);
    opacity: 0.7;
  }
  .bar-value {
    width: 46px;
    flex-shrink: 0;
    font-size: 11.5px;
    text-align: right;
    color: var(--ink);
  }
  .bar-veg {
    width: 32px;
    flex-shrink: 0;
    text-align: right;
    color: var(--sage);
    font-size: 12px;
    white-space: nowrap;
  }

  .range-picker {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }
  .range-btn {
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--ink-soft);
    font-family: inherit;
    font-size: 12px;
    padding: 6px 10px;
    border-radius: 5px;
    cursor: pointer;
  }
  .range-btn.active {
    background: var(--sage);
    border-color: var(--sage);
    color: #fffefb;
  }
  .days-input {
    width: 100px;
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--ink);
    font-family: inherit;
    font-size: 12px;
    padding: 6px 8px;
    border-radius: 5px;
    -moz-appearance: textfield;
    appearance: textfield;
  }
  .days-input::-webkit-inner-spin-button,
  .days-input::-webkit-outer-spin-button {
    -webkit-appearance: none;
    margin: 0;
  }

  /* 显示指标 + 显示范围 合并成一行：左边指标(可能换行)，右边天数输入框固定靠右，
     两边挤在一起容易显得拥挤，所以指标按钮单独做得更紧凑一点(内边距/字号都比
     其他按钮小一档)，天数输入框也从原来能装"自定义天数"占位文字的宽度缩到
     只需要装两三位数字的宽度。窄屏下space-between+flex-wrap会让天数控件
     自然掉到指标按钮下面一行，而不是硬挤在一起。 */
  .display-controls-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 8px 10px;
  }
  .metric-picker {
    gap: 5px;
  }
  .metric-picker .range-btn {
    padding: 5px 9px;
    font-size: 11px;
  }
  .days-control {
    display: flex;
    align-items: center;
    gap: 5px;
    flex-shrink: 0;
    margin-left: auto;
  }
  .days-control .days-input {
    width: 52px;
    text-align: center;
  }
  .days-suffix {
    font-size: 12px;
    color: var(--ink-soft);
  }

  /* 今天的进度条悬浮条——position:sticky不需要写JS去监听滚动事件，
     浏览器原生支持"滚动到这个元素本来的位置之前正常排版，滚过去之后
     自动贴在容器顶部"，这正好是"往下滑也能一直看到今天进度"这个需求
     的标准实现方式。 */
  .sticky-today-bar {
    position: sticky;
    top: 0;
    z-index: 40;
    background: var(--panel);
    border-bottom: 1px solid var(--border);
    padding: 8px 6px;
    margin: 8px 0 4px;
    display: flex;
    flex-wrap: wrap;
    gap: 6px 16px;
  }
  .sticky-metric {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
  }
  .sticky-metric-bar {
    width: 46px;
    height: 5px;
    background: var(--track);
    border-radius: 3px;
    overflow: hidden;
  }
  .sticky-metric-fill {
    height: 100%;
    background: var(--sage);
  }
  .sticky-metric-value {
    color: var(--ink);
    font-weight: 600;
    white-space: nowrap;
  }

  .food-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 9px 6px;
    border-radius: 4px;
    border-bottom: 1px solid var(--border);
    transition: background 0.5s ease;
  }
  .food-row:last-child { border-bottom: none; }
  .food-row.flash { background: var(--flash); }
  .food-info {
    flex: 1 1 auto;
    min-width: 0;   /* 允许内部文字被压缩到能显示省略号，而不是把整行撑爆换行 */
    overflow: hidden;
  }
  .food-name {
    color: var(--ink);
    font-size: 13.5px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;   /* 名字太长时省略号截断，不会把按钮挤到下一行 */
  }
  .food-meta {
    color: var(--ink-soft);
    font-size: 11px;
    margin-top: 1px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .food-controls {
    display: flex;
    align-items: center;
    gap: 5px;
    margin-left: auto;
    flex-shrink: 0;   /* 控件区域永远保持完整大小，收缩空间的责任全部交给左边的文字省略号 */
  }
  .food-progress {
    min-width: 20px;
    text-align: right;
    font-size: 12.5px;
    font-weight: 600;
    color: var(--sage);
    margin-right: 2px;
  }
  .food-input {
    width: 44px;
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--ink);
    font-family: inherit;
    font-size: 14px;
    padding: 7px 6px;
    border-radius: 5px;
    text-align: right;
    -moz-appearance: textfield;
    appearance: textfield;
  }
  .food-input::-webkit-inner-spin-button,
  .food-input::-webkit-outer-spin-button {
    -webkit-appearance: none;
    margin: 0;
  }
  .food-input:focus {
    outline: none;
    border-color: var(--sage-dim);
    box-shadow: 0 0 0 2px rgba(74,124,111,0.25);
  }
  .add-btn, .plus-btn {
    background: var(--sage);
    color: #fffefb;
    border: none;
    font-family: inherit;
    font-weight: 600;
    border-radius: 5px;
    cursor: pointer;
    flex-shrink: 0;
  }
  .add-btn:hover, .plus-btn:hover { background: var(--sage-dim); }
  .add-btn {
    font-size: 14px;
    width: 32px;
    height: 32px;
  }
  .plus-btn {
    font-size: 11px;
    padding: 7px 8px;
    height: 32px;
    white-space: nowrap;
  }

  /* ---- 弹窗（编辑历史 / 管理食物 共用），刻意用跟主界面不同的暖色调，
     让人一眼分辨"我现在不是在做日常记录，是在改历史/改食物库" ---- */
  .modal-overlay {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.55);
    align-items: flex-start;
    justify-content: center;
    padding: 24px 12px;
    overflow-y: auto;
    z-index: 100;
  }
  .modal-overlay.open { display: flex; }
  .modal-box {
    width: 100%;
    max-width: 640px;
    background: var(--modal-bg);
    border: 1px solid var(--modal-border);
    border-radius: 10px;
    padding: 18px 20px 22px;
  }
  .modal-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 14px;
  }
  .modal-title {
    font-size: 13px;
    color: var(--modal-accent);
    font-weight: 600;
    letter-spacing: 0.02em;
  }
  .modal-close-btn {
    background: transparent;
    border: 1px solid var(--modal-accent);
    color: var(--modal-accent);
    font-family: inherit;
    font-size: 13px;
    width: 28px;
    height: 28px;
    border-radius: 6px;
    cursor: pointer;
    line-height: 1;
  }
  .modal-close-btn:hover { background: rgba(201,161,92,0.15); }

  .day-editor-controls {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 10px;
  }
  .date-input {
    background: var(--bg);
    border: 1px solid var(--modal-border);
    color: var(--ink);
    font-family: inherit;
    font-size: 13px;
    padding: 7px 9px;
    border-radius: 5px;
  }
  .entry-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px 10px;
    padding: 9px 6px;
    border-bottom: 1px solid var(--modal-border);
  }
  .entry-row:last-child { border-bottom: none; }
  .entry-info { flex: 1 1 auto; min-width: 120px; }
  .entry-name { font-size: 13.5px; color: var(--ink); }
  .entry-meta { font-size: 11px; color: var(--ink-soft); }
  .entry-controls {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .entry-input {
    width: 56px;
    background: var(--bg);
    border: 1px solid var(--modal-border);
    color: var(--ink);
    font-family: inherit;
    font-size: 13px;
    padding: 6px 7px;
    border-radius: 5px;
    text-align: right;
  }
  .entry-save-btn {
    background: var(--modal-accent);
    color: #fffefb;
    border: none;
    font-family: inherit;
    font-size: 11px;
    padding: 6px 9px;
    border-radius: 5px;
    cursor: pointer;
  }
  .entry-save-btn:hover { opacity: 0.85; }
  .entry-delete-btn {
    background: transparent;
    border: 1px solid var(--danger);
    color: var(--danger);
    font-family: inherit;
    font-size: 11px;
    padding: 6px 9px;
    border-radius: 5px;
    cursor: pointer;
  }
  .entry-delete-btn:hover { background: var(--danger-flash); }
  .empty-note { color: var(--ink-soft); font-size: 12px; padding: 8px 6px; }

  /* 待确认的改动：先给出明显的视觉标记，真正提交要等点了"确认修改" */
  .entry-row.pending-edit {
    background: rgba(184, 134, 61, 0.10);
    border-left: 3px solid var(--modal-accent);
    padding-left: 8px;
  }
  .entry-row.pending-delete {
    background: var(--danger-flash);
    border-left: 3px solid var(--danger);
    padding-left: 8px;
    opacity: 0.6;
  }
  .entry-row.pending-delete .entry-name { text-decoration: line-through; }

  .confirm-history-btn {
    width: 100%;
    margin-top: 14px;
    background: var(--modal-accent);
    color: #fffefb;
    border: none;
    font-family: inherit;
    font-weight: 600;
    font-size: 13px;
    padding: 11px;
    border-radius: 6px;
    cursor: pointer;
  }
  .confirm-history-btn:hover:not(:disabled) { opacity: 0.85; }
  .confirm-history-btn:disabled {
    background: var(--track);
    color: var(--ink-soft);
    cursor: not-allowed;
  }


  /* ---- 管理食物弹窗 ---- */
  .food-edit-row {
    padding: 10px 6px;
    border-bottom: 1px solid var(--modal-border);
  }
  .food-edit-row:last-child { border-bottom: none; }
  .food-edit-name-line {
    display: flex;
    align-items: center;
    justify-content: space-between;
    cursor: pointer;
  }
  .food-edit-name { font-size: 13.5px; color: var(--ink); }
  .food-edit-meta { font-size: 11px; color: var(--ink-soft); margin-top: 1px; }
  .food-edit-toggle { color: var(--modal-accent); font-size: 12px; }
  .food-edit-form {
    display: none;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
    margin-top: 10px;
  }
  .food-edit-form.open { display: grid; }
  .food-edit-form label {
    font-size: 10.5px;
    color: var(--ink-soft);
    display: block;
    margin-bottom: 2px;
  }
  .food-edit-form input,
  .food-edit-form select {
    width: 100%;
    background: var(--bg);
    border: 1px solid var(--modal-border);
    color: var(--ink);
    font-family: inherit;
    font-size: 13px;
    padding: 6px 8px;
    border-radius: 5px;
  }
  .food-edit-form .full-width { grid-column: 1 / -1; }
  .food-edit-save-btn {
    grid-column: 1 / -1;
    background: var(--modal-accent);
    color: #fffefb;
    border: none;
    font-family: inherit;
    font-weight: 600;
    font-size: 12px;
    padding: 9px;
    border-radius: 5px;
    cursor: pointer;
    margin-top: 2px;
  }
  .food-edit-save-btn:hover { opacity: 0.85; }
  .food-edit-delete-btn {
    grid-column: 1 / -1;
    background: transparent;
    border: 1px solid var(--danger);
    color: var(--danger);
    font-family: inherit;
    font-size: 11px;
    padding: 7px;
    border-radius: 5px;
    cursor: pointer;
    margin-top: 2px;
  }
  .food-edit-delete-btn:hover { background: var(--danger-flash); }
  .add-food-section {
    margin-top: 14px;
    padding-top: 14px;
    border-top: 1px dashed var(--modal-border);
  }
  .add-food-label {
    font-size: 11px;
    color: var(--modal-accent);
    margin-bottom: 8px;
    font-weight: 600;
  }

  /* 手机屏幕适配：字号、间距、触控区域都放大一档，避免误触和看不清 */
  @media (max-width: 480px) {
    body { padding: 10px 8px; }
    .panel, .modal-box { padding: 14px 12px; border-radius: 6px; }
    .food-name { font-size: 14.5px; }
    .food-meta { font-size: 11.5px; }
    .food-input { font-size: 16px; width: 42px; padding: 7px 5px; }
    .add-btn { width: 30px; height: 30px; font-size: 14px; }
    .plus-btn { height: 30px; font-size: 11px; padding: 6px 8px; }
    .range-btn { font-size: 12.5px; padding: 8px 12px; }
    .btn-small, .clear-btn { font-size: 11.5px; padding: 7px 9px; }
    #undo-btn { padding-right: 10px; }
    .bar-label { width: 38px; font-size: 11px; }
    .bar-value { width: 40px; font-size: 11px; }
    .food-edit-form { grid-template-columns: 1fr; }
    .metric-picker .range-btn { padding: 6px 8px; font-size: 10.5px; }
    .days-control .days-input { width: 46px; }
  }
</style>
</head>
<body>
  <div class="panel">

    <div class="page-header">
      <h1 class="page-title">饮食记录</h1>
      <div class="user-badge" id="user-badge"></div>
    </div>

    <div class="top-bar">
      <button class="clear-btn" id="clear-btn">清空今天</button>
      <button class="btn-small" id="open-history-btn">历史</button>
      <button class="btn-small" id="open-foods-btn">设置</button>
      __LOGOUT_BUTTON__
      <button class="btn-small" id="undo-btn">撤回上一步</button>
    </div>

    <div class="section-label">显示</div>
    <div class="display-controls-row">
      <div class="range-picker metric-picker" id="metric-picker">
        <button class="range-btn active" data-metric="kcal">热量</button>
        <button class="range-btn active" data-metric="protein">蛋白质</button>
        <button class="range-btn" data-metric="carb">碳水</button>
        <button class="range-btn" data-metric="fat">脂肪</button>
      </div>
      <div class="days-control">
        <input type="number" id="custom-days" class="days-input" value="7" min="1" max="365">
        <span class="days-suffix">天</span>
      </div>
    </div>

    <div class="section-label" id="range-title">过去 7 天</div>
    <div id="chart-container"></div>

    <div class="sticky-today-bar" id="sticky-today-bar"></div>
    <div class="section-label">记录</div>
    <div id="food-list"></div>

  </div>

  <!-- ---- 编辑历史 弹窗 ---- -->
  <div class="modal-overlay" id="history-modal">
    <div class="modal-box">
      <div class="modal-header">
        <div class="modal-title">编辑历史</div>
        <button class="modal-close-btn" id="close-history-btn">✕</button>
      </div>
      <div class="day-editor-controls">
        <input type="date" id="edit-date" class="date-input">
        <button class="btn-small" id="load-day-btn">查看</button>
      </div>
      <div id="day-entries"></div>
      <button class="confirm-history-btn" id="confirm-history-btn" disabled>确认修改</button>
    </div>
  </div>

  <!-- ---- 管理食物/目标 弹窗 ---- -->
  <div class="modal-overlay" id="foods-modal">
    <div class="modal-box">
      <div class="modal-header">
        <div class="modal-title">管理食物/目标</div>
        <button class="modal-close-btn" id="close-foods-btn">✕</button>
      </div>

      <div class="add-food-label">每日目标</div>
      <div class="food-edit-form open">
        <div><label>总热量目标(kcal)</label><input type="number" step="any" id="target-kcal-input"></div>
        <div><label>总蛋白质目标(g)</label><input type="number" step="any" id="target-protein-input"></div>
        <div><label>总碳水目标(g，选填)</label><input type="number" step="any" id="target-carb-input"></div>
        <div><label>总脂肪目标(g，选填)</label><input type="number" step="any" id="target-fat-input"></div>
        <button class="food-edit-save-btn" id="save-targets-btn">保存目标</button>
      </div>

      <div class="add-food-section">
        <div class="add-food-label">食物列表</div>
        <div id="food-edit-list"></div>
      </div>

      <div class="add-food-section">
        <div class="add-food-label">+ 添加新食物</div>
        <div class="food-edit-form open" id="add-food-form">
          <div><label>名字</label><input type="text" id="new-food-name"></div>
          <div><label>单位</label><input type="text" id="new-food-unit" placeholder="比如 1个 / 1份"></div>
          <div><label>热量(kcal)</label><input type="number" step="any" id="new-food-kcal"></div>
          <div><label>蛋白质(g)</label><input type="number" step="any" id="new-food-protein"></div>
          <div><label>碳水(g)</label><input type="number" step="any" id="new-food-carb"></div>
          <div><label>脂肪(g)</label><input type="number" step="any" id="new-food-fat"></div>
          <div class="full-width">
            <label>特殊标记(选填)</label>
            <select id="new-food-tracker">
              <option value="">无</option>
              <option value="vegetable">蔬菜打卡</option>
              <option value="vitamin">维生素打卡</option>
            </select>
          </div>
          <button class="food-edit-save-btn" id="add-food-save-btn">添加</button>
        </div>
      </div>

      <div class="add-food-section" id="admin-users-section" style="display:none;">
        <div class="add-food-label">用户管理（仅管理员可见）</div>
        <div id="users-list"></div>
        <div class="food-edit-form open" style="margin-top:10px;">
          <div><label>新用户名</label><input type="text" id="new-user-name"></div>
          <div><label>密码</label><input type="text" id="new-user-pass"></div>
          <div class="full-width">
            <label style="display:flex;align-items:center;gap:6px;font-size:11.5px;color:var(--ink);">
              <input type="checkbox" id="new-user-admin" style="width:auto;"> 设为管理员
            </label>
          </div>
          <button class="food-edit-save-btn" id="add-user-save-btn">添加用户</button>
        </div>
      </div>
    </div>
  </div>

<script>
let FOODS = [];
let currentDays = 7;
let selectedMetrics = new Set(JSON.parse(localStorage.getItem('nutritionMetrics') || '["kcal","protein"]'));
let CURRENT_IS_ADMIN = false;

// 计算浏览器所在时区的本地当前时间，格式"YYYY-MM-DDTHH:MM:SS"。
// 必须用这个而不是服务器自己的时间——Render服务器跑在UTC，"今天是哪天"这种
// 判断如果交给服务器自己算，一到晚上(比如美东时间)就会因为UTC已经跨天而判断错误。
// 用的是Date对象的本地getter(getFullYear/getMonth/getDate等)，不是toISOString()——
// toISOString()返回的是UTC时间，会犯同样的错误，这里必须用本地时间的取值方法。
function getClientDatetimeParam() {
  const d = new Date();
  const pad = n => String(n).padStart(2, '0');
  const s = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  return s;
}

async function loadInitial() {
  const res = await fetch(`/api/data?days=${currentDays}&client_datetime=${encodeURIComponent(getClientDatetimeParam())}`);
  if (res.status === 401) { window.location.href = '/login'; return; }
  const data = await res.json();
  FOODS = data.foods;
  renderCharts(data);
  renderFoodList();

  if (data.current_user && data.current_user !== 'local') {
    document.getElementById('user-badge').textContent = `你好，${data.current_user}`;
  }
  CURRENT_IS_ADMIN = !!data.is_admin;
  document.getElementById('admin-users-section').style.display = CURRENT_IS_ADMIN ? 'block' : 'none';
  document.getElementById('target-kcal-input').value = data.target_kcal;
  document.getElementById('target-protein-input').value = data.target_protein;
  document.getElementById('target-carb-input').value = data.target_carb;
  document.getElementById('target-fat-input').value = data.target_fat;
}

const METRIC_CONFIG = {
  kcal:    { title: '🔥 总热量 (kcal)',   icon: '🔥', key: 'kcal',    fill: '█', showVeg: true,  target: payload => payload.target_kcal },
  protein: { title: '💪 总蛋白质 (g)',    icon: '💪', key: 'protein', fill: '▓', showVeg: false, target: payload => payload.target_protein },
  carb:    { title: '🍞 总碳水 (g)',      icon: '🍞', key: 'carb',    fill: '▒', showVeg: false, target: payload => payload.target_carb },
  fat:     { title: '🥑 总脂肪 (g)',      icon: '🥑', key: 'fat',     fill: '░', showVeg: false, target: payload => payload.target_fat },
};

function renderCharts(data) {
  document.getElementById('range-title').textContent = `过去 ${currentDays} 天`;
  const container = document.getElementById('chart-container');
  container.innerHTML = '';
  selectedMetrics.forEach(metric => {
    const config = METRIC_CONFIG[metric];
    if (!config) return;
    const target = config.target ? config.target(data) : 0;
    const div = document.createElement('div');
    div.className = 'chart-group';
    div.innerHTML = buildChart(config.title, data.week, config.key, target, config.showVeg, config.fill);
    container.appendChild(div);
  });
  renderStickyBar(data);
}

function renderStickyBar(data) {
  const today = data.week.find(d => d.is_today);
  const container = document.getElementById('sticky-today-bar');
  if (!today) { container.innerHTML = ''; return; }

  // 跟主图表一样，只显示用户当前选中的那几个指标——保持两处呈现的信息一致，
  // 不会出现"主图表没显示碳水，悬浮条却显示了"这种不一致的情况。
  container.innerHTML = Array.from(selectedMetrics).map(metric => {
    const config = METRIC_CONFIG[metric];
    if (!config) return '';
    const val = today[config.key];
    const target = config.target(data);
    const pct = target > 0 ? Math.min((val / target) * 100, 100) : 0;
    const valueText = target > 0 ? `${val.toFixed(0)}/${target.toFixed(0)}` : val.toFixed(0);
    return `
      <div class="sticky-metric">
        <span>${config.icon}</span>
        <div class="sticky-metric-bar"><div class="sticky-metric-fill" style="width:${pct}%"></div></div>
        <span class="sticky-metric-value">${valueText}</span>
      </div>
    `;
  }).join('');
}

function buildChart(title, week, key, target, showVeg, fillChar) {
  const maxVal = Math.max(...week.map(d => d[key]), target, 1);
  const hasTarget = target > 0;
  const rows = week.map(d => {
    const val = d[key];
    const fillPct = (val / maxVal) * 100;
    const targetPct = hasTarget ? (target / maxVal) * 100 : null;
    const labelClass = d.is_today ? 'bar-label today' : 'bar-label';
    const todayTag = d.is_today ? '今天' : '';
    // 蔬菜和维生素这两个emoji放在同一列里，不管有没有勾中都统一渲染这一列，
    // 保证这一列的宽度在四张图表(热量/蛋白/碳水/脂肪)里完全一致——
    // 之前的bug教训是：只有部分图表渲染这一列会导致进度条宽度不对齐。
    const vegMark = showVeg && d.veg ? '🥦' : '';
    const vitaminMark = showVeg && d.vitamin ? '💊' : '';
    return `
      <div class="bar-row">
        <div class="${labelClass}">${d.label}</div>
        <div class="bar-today-tag">${todayTag}</div>
        <div class="bar-track">
          <div class="bar-fill" style="width:${fillPct}%"></div>
          ${hasTarget ? `<div class="bar-target-line" style="left:${targetPct}%"></div>` : ''}
        </div>
        <div class="bar-value">${val.toFixed(1)}</div>
        <div class="bar-veg">${vegMark}${vitaminMark}</div>
      </div>
    `;
  }).join('');

  const targetLabel = hasTarget ? `<span class="target">目标: ${target}</span>` : '';
  return `<div class="chart-title">${title} ${targetLabel}</div>${rows}`;
}

function fmtNum(n) {
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

function progressText(food) {
  return food.eaten_today > 0 ? fmtNum(food.eaten_today) : '';
}

function renderFoodList() {
  const container = document.getElementById('food-list');
  container.innerHTML = '';

  FOODS.forEach(food => {
    const row = document.createElement('div');
    row.className = 'food-row';
    row.id = `row-${food.id}`;
    row.innerHTML = `
      <div class="food-info">
        <div class="food-name">${food.name}</div>
        <div class="food-meta">${food.unit} · ${food.kcal}kcal / ${food.protein}g蛋白</div>
      </div>
      <div class="food-controls">
        <div class="food-progress" id="progress-${food.id}">${progressText(food)}</div>
        <input type="number" step="any" inputmode="decimal" class="food-input" data-id="${food.id}" value="" placeholder="0">
        <button class="add-btn" data-id="${food.id}">✓</button>
        <button class="plus-btn" data-id="${food.id}">+1</button>
      </div>
    `;
    container.appendChild(row);

    const input = row.querySelector('.food-input');
    const addBtn = row.querySelector('.add-btn');
    const plusBtn = row.querySelector('.plus-btn');

    addBtn.addEventListener('click', () => submitOne(food.id, input, row));
    plusBtn.addEventListener('click', () => submitDirect(food.id, 1, row));

    input.addEventListener('keydown', e => {
      if (e.key === 'Enter') {
        e.preventDefault();
        submitOne(food.id, input, row);
        return;
      }
      if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
        e.preventDefault();
        const allInputs = Array.from(document.querySelectorAll('.food-input'));
        const idx = allInputs.indexOf(input);
        const nextIdx = e.key === 'ArrowUp' ? idx - 1 : idx + 1;
        if (nextIdx >= 0 && nextIdx < allInputs.length) {
          allInputs[nextIdx].focus();
          allInputs[nextIdx].select();
        }
      }
    });
  });
}

function refreshProgress(updatedFoods) {
  updatedFoods.forEach(food => {
    const el = document.getElementById(`progress-${food.id}`);
    if (!el) return;
    el.textContent = progressText(food);
  });
  FOODS = updatedFoods;
}

async function submitOne(foodId, input, row) {
  const v = parseFloat(input.value);
  if (isNaN(v) || v <= 0) return;
  await submitDirect(foodId, v, row);
  input.value = '';
}

async function submitDirect(foodId, servings, row) {
  try {
    const res = await fetch(`/api/record?days=${currentDays}&client_datetime=${encodeURIComponent(getClientDatetimeParam())}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [foodId]: servings })
    });
    if (res.status === 401) { window.location.href = '/login'; return; }
    const data = await res.json();

    renderCharts(data);
    refreshProgress(data.foods);

    row.classList.add('flash');
    setTimeout(() => row.classList.remove('flash'), 900);
  } catch (err) {
    alert('记录失败，请检查服务是否还在运行。');
    console.error(err);
  }
}

function applyDaysInput() {
  const v = parseInt(document.getElementById('custom-days').value);
  if (!isNaN(v) && v > 0) {
    currentDays = v;
    loadInitial();
  }
}

document.getElementById('custom-days').addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    e.preventDefault();
    applyDaysInput();
  }
});
document.getElementById('custom-days').addEventListener('blur', applyDaysInput);

document.querySelectorAll('#metric-picker .range-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const metric = btn.dataset.metric;
    if (selectedMetrics.has(metric)) {
      if (selectedMetrics.size === 1) return;  // 至少保留一个指标，不能一个都不选
      selectedMetrics.delete(metric);
      btn.classList.remove('active');
    } else {
      selectedMetrics.add(metric);
      btn.classList.add('active');
    }
    localStorage.setItem('nutritionMetrics', JSON.stringify(Array.from(selectedMetrics)));
    loadInitial();
  });
});

document.getElementById('clear-btn').addEventListener('click', async () => {
  if (!confirm('清空今天记录的所有食物？这个操作不能撤销。')) return;

  try {
    const res = await fetch(`/api/clear-today?days=${currentDays}&client_datetime=${encodeURIComponent(getClientDatetimeParam())}`, { method: 'POST' });
    if (res.status === 401) { window.location.href = '/login'; return; }
    const data = await res.json();
    FOODS = data.foods;
    renderCharts(data);
    renderFoodList();
  } catch (err) {
    alert('清空失败，请检查服务是否还在运行。');
    console.error(err);
  }
});

document.getElementById('undo-btn').addEventListener('click', async () => {
  try {
    const res = await fetch(`/api/undo?days=${currentDays}&client_datetime=${encodeURIComponent(getClientDatetimeParam())}`, { method: 'POST' });
    if (res.status === 401) { window.location.href = '/login'; return; }
    const data = await res.json();
    if (!data.undone) {
      alert('今天还没有记录，没什么可撤回的。');
      return;
    }
    FOODS = data.foods;
    renderCharts(data);
    renderFoodList();
  } catch (err) {
    alert('撤回失败，请检查服务是否还在运行。');
    console.error(err);
  }
});

const logoutBtn = document.getElementById('logout-btn');
if (logoutBtn) {
  logoutBtn.addEventListener('click', async () => {
    await fetch('/api/logout', { method: 'POST' });
    window.location.href = '/login';
  });
}

// ---------------------------------------------------------------------------
// 弹窗开关 + 地址栏hash联动
//
// 两个弹窗（编辑历史 #history / 管理食物 #foods）共用同一套开关逻辑。
// 打开弹窗时把地址栏hash设成对应的名字，关闭时清空——这样：
// 1. 浏览器的"后退"按钮能在"弹窗开着"和"弹窗关着"之间切换（History API的
//    标准用法，不是我自己发明的技巧）
// 2. 分享一个带#history的链接给别人，对方打开会自动弹出编辑历史面板
// 3. 用地址栏的变化本身就在告诉你"现在在哪个视图"，不用靠肉眼分辨背景色
// ---------------------------------------------------------------------------
const MODALS = {
  history: document.getElementById('history-modal'),
  foods: document.getElementById('foods-modal'),
};

function openModal(name) {
  Object.values(MODALS).forEach(m => m.classList.remove('open'));
  MODALS[name].classList.add('open');
  if (window.location.hash !== `#${name}`) {
    window.history.pushState({ modal: name }, '', `#${name}`);
  }
  if (name === 'history') loadDayEntries(document.getElementById('edit-date').value);
  if (name === 'foods') { loadFoodEditList(); loadUsersList(); }
}

function closeModal() {
  Object.values(MODALS).forEach(m => m.classList.remove('open'));
  if (window.location.hash !== '') {
    window.history.pushState({}, '', window.location.pathname);
  }
}

function applyHash() {
  const hash = window.location.hash.replace('#', '');
  if (hash === 'history' || hash === 'foods') {
    Object.values(MODALS).forEach(m => m.classList.remove('open'));
    MODALS[hash].classList.add('open');
    if (hash === 'history') loadDayEntries(document.getElementById('edit-date').value);
    if (hash === 'foods') { loadFoodEditList(); loadUsersList(); }
  } else {
    Object.values(MODALS).forEach(m => m.classList.remove('open'));
  }
}

window.addEventListener('popstate', applyHash);

document.getElementById('open-history-btn').addEventListener('click', () => openModal('history'));
document.getElementById('close-history-btn').addEventListener('click', closeModal);
document.getElementById('open-foods-btn').addEventListener('click', () => openModal('foods'));
document.getElementById('close-foods-btn').addEventListener('click', closeModal);

// 点弹窗外面的深色背景也能关闭，是弹窗类UI的标准行为
document.querySelectorAll('.modal-overlay').forEach(overlay => {
  overlay.addEventListener('click', e => {
    if (e.target === overlay) closeModal();
  });
});

// Esc键关闭，同样是标准行为
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeModal();
});

// ---- 编辑历史某一天的数据 ----
const editDateInput = document.getElementById('edit-date');
const todayStr = new Date().toISOString().slice(0, 10);
editDateInput.value = todayStr;

document.getElementById('load-day-btn').addEventListener('click', () => loadDayEntries(editDateInput.value));

let pendingChanges = {};  // entryId -> {action: 'update', servings: X} 或 {action: 'delete'}

async function loadDayEntries(dateStr) {
  const container = document.getElementById('day-entries');
  container.innerHTML = '<div class="empty-note">加载中…</div>';
  pendingChanges = {};  // 每次重新加载这一天的数据，之前没确认的暂存改动作废

  const res = await fetch(`/api/day?date=${dateStr}`);
  if (res.status === 401) { window.location.href = '/login'; return; }
  const data = await res.json();

  if (!data.entries || data.entries.length === 0) {
    container.innerHTML = '<div class="empty-note">这天没有记录。</div>';
    updateConfirmBtnState();
    return;
  }

  container.innerHTML = '';
  data.entries.forEach(entry => {
    const row = document.createElement('div');
    row.className = 'entry-row';
    row.id = `entry-row-${entry.id}`;
    const canEdit = !!entry.food_id;
    row.innerHTML = `
      <div class="entry-info">
        <div class="entry-name">${entry.food}</div>
        <div class="entry-meta">${entry.time} · ${entry.kcal}kcal / ${entry.protein_g}g蛋白</div>
      </div>
      <div class="entry-controls">
        ${canEdit ? `
          <input type="number" step="any" class="entry-input" value="${entry.servings}">
        ` : `<span class="entry-meta">（旧数据，不可编辑）</span>`}
        <button class="entry-delete-btn">删除</button>
      </div>
    `;
    container.appendChild(row);

    if (canEdit) {
      const editInput = row.querySelector('.entry-input');
      editInput.addEventListener('input', () => {
        const newVal = parseFloat(editInput.value);
        if (isNaN(newVal) || newVal <= 0) {
          delete pendingChanges[entry.id];
          row.classList.remove('pending-edit');
        } else if (newVal === parseFloat(entry.servings)) {
          // 改回了原始值，等于没有改动，不需要占一个待确认的位置
          delete pendingChanges[entry.id];
          row.classList.remove('pending-edit');
        } else {
          pendingChanges[entry.id] = { action: 'update', servings: newVal };
          row.classList.remove('pending-delete');
          row.classList.add('pending-edit');
        }
        updateConfirmBtnState();
      });
    }

    const deleteBtn = row.querySelector('.entry-delete-btn');
    deleteBtn.addEventListener('click', () => {
      const isMarked = row.classList.contains('pending-delete');
      if (isMarked) {
        // 再点一次取消标记删除
        delete pendingChanges[entry.id];
        row.classList.remove('pending-delete');
        deleteBtn.textContent = '删除';
      } else {
        pendingChanges[entry.id] = { action: 'delete' };
        row.classList.remove('pending-edit');
        row.classList.add('pending-delete');
        deleteBtn.textContent = '取消删除';
      }
      updateConfirmBtnState();
    });
  });

  updateConfirmBtnState();
}

function updateConfirmBtnState() {
  const btn = document.getElementById('confirm-history-btn');
  const count = Object.keys(pendingChanges).length;
  btn.disabled = count === 0;
  btn.textContent = count === 0 ? '确认修改' : `确认修改（${count}处待保存）`;
}

document.getElementById('confirm-history-btn').addEventListener('click', async () => {
  const entryIds = Object.keys(pendingChanges);
  if (entryIds.length === 0) return;

  for (const entryId of entryIds) {
    const change = pendingChanges[entryId];
    if (change.action === 'update') {
      await fetch('/api/entry/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: entryId, servings: change.servings })
      });
    } else if (change.action === 'delete') {
      await fetch('/api/entry/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: entryId })
      });
    }
  }

  pendingChanges = {};
  await loadDayEntries(editDateInput.value);
  await loadInitial();
});


// ---- 管理食物 ----
async function loadFoodEditList() {
  const container = document.getElementById('food-edit-list');
  container.innerHTML = '<div class="empty-note">加载中…</div>';

  const res = await fetch(`/api/data?days=1&client_datetime=${encodeURIComponent(getClientDatetimeParam())}`);
  if (res.status === 401) { window.location.href = '/login'; return; }
  const data = await res.json();

  container.innerHTML = '';
  data.foods.forEach(food => {
    const row = document.createElement('div');
    row.className = 'food-edit-row';
    const trackerLabel = food.tracker === 'vegetable' ? ' · 🥦蔬菜打卡'
                        : food.tracker === 'vitamin' ? ' · 💊维生素打卡' : '';
    row.innerHTML = `
      <div class="food-edit-name-line">
        <div>
          <div class="food-edit-name">${food.name}</div>
          <div class="food-edit-meta">${food.unit} · ${food.kcal}kcal / ${food.protein}g蛋白${trackerLabel}</div>
        </div>
        <div class="food-edit-toggle">编辑 ▾</div>
      </div>
      <div class="food-edit-form">
        <div><label>名字</label><input type="text" class="f-name" value="${food.name}"></div>
        <div><label>单位</label><input type="text" class="f-unit" value="${food.unit}"></div>
        <div><label>热量(kcal)</label><input type="number" step="any" class="f-kcal" value="${food.kcal}"></div>
        <div><label>蛋白质(g)</label><input type="number" step="any" class="f-protein" value="${food.protein}"></div>
        <div><label>碳水(g)</label><input type="number" step="any" class="f-carb" value="${food.carb}"></div>
        <div><label>脂肪(g)</label><input type="number" step="any" class="f-fat" value="${food.fat}"></div>
        <div class="full-width">
          <label>特殊标记(选填，同类型只能有一个食物持有，设置后会自动取消原持有者的标记)</label>
          <select class="f-tracker">
            <option value="" ${!food.tracker ? 'selected' : ''}>无</option>
            <option value="vegetable" ${food.tracker === 'vegetable' ? 'selected' : ''}>蔬菜打卡</option>
            <option value="vitamin" ${food.tracker === 'vitamin' ? 'selected' : ''}>维生素打卡</option>
          </select>
        </div>
        <button class="food-edit-save-btn">保存修改（会同步更新这个食物已有的历史记录）</button>
        <button class="food-edit-delete-btn">删除这个食物</button>
      </div>
    `;
    container.appendChild(row);

    const nameLine = row.querySelector('.food-edit-name-line');
    const form = row.querySelector('.food-edit-form');
    const toggle = row.querySelector('.food-edit-toggle');
    nameLine.addEventListener('click', () => {
      form.classList.toggle('open');
      toggle.textContent = form.classList.contains('open') ? '收起 ▴' : '编辑 ▾';
    });

    row.querySelector('.food-edit-save-btn').addEventListener('click', async (e) => {
      e.stopPropagation();
      const payload = {
        id: food.id,
        name: row.querySelector('.f-name').value.trim(),
        unit: row.querySelector('.f-unit').value.trim(),
        kcal: parseFloat(row.querySelector('.f-kcal').value),
        protein: parseFloat(row.querySelector('.f-protein').value),
        carb: parseFloat(row.querySelector('.f-carb').value),
        fat: parseFloat(row.querySelector('.f-fat').value),
        tracker: row.querySelector('.f-tracker').value || null,
        client_datetime: getClientDatetimeParam(),
      };
      if (!payload.name) { alert('名字不能为空'); return; }
      const res = await fetch('/api/foods/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const result = await res.json();
      if (result.ok) {
        if (result.updated_entries > 0) {
          alert(`已保存，同步更新了 ${result.updated_entries} 条历史记录。`);
        }
        loadFoodEditList();
        loadInitial();
      } else {
        alert('保存失败：' + (result.error || '未知错误'));
      }
    });

    row.querySelector('.food-edit-delete-btn').addEventListener('click', async (e) => {
      e.stopPropagation();
      const confirmed = confirm(
        `确定删除"${food.name}"？\\n\\n` +
        `已有的历史记录不会被删除或改动，会保留删除前最后的营养数值。\\n` +
        `以后如果你新建一个同名食物，那会是一个完全独立的新食物，` +
        `不会跟这次删除的记录产生任何关联。`
      );
      if (!confirmed) return;
      const res = await fetch('/api/foods/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: food.id, client_datetime: getClientDatetimeParam() })
      });
      const result = await res.json();
      if (result.ok) {
        loadFoodEditList();
        loadInitial();
      } else {
        alert('删除失败');
      }
    });
  });
}

document.getElementById('add-food-save-btn').addEventListener('click', async () => {
  const payload = {
    name: document.getElementById('new-food-name').value.trim(),
    unit: document.getElementById('new-food-unit').value.trim(),
    kcal: parseFloat(document.getElementById('new-food-kcal').value) || 0,
    protein: parseFloat(document.getElementById('new-food-protein').value) || 0,
    carb: parseFloat(document.getElementById('new-food-carb').value) || 0,
    fat: parseFloat(document.getElementById('new-food-fat').value) || 0,
    tracker: document.getElementById('new-food-tracker').value || null,
    client_datetime: getClientDatetimeParam(),
  };
  if (!payload.name) { alert('名字不能为空'); return; }
  if (!payload.unit) { alert('单位不能为空'); return; }

  const res = await fetch('/api/foods/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  const result = await res.json();
  if (result.ok) {
    ['name', 'unit', 'kcal', 'protein', 'carb', 'fat'].forEach(f => {
      document.getElementById(`new-food-${f}`).value = '';
    });
    document.getElementById('new-food-tracker').value = '';
    loadFoodEditList();
    loadInitial();
  } else {
    alert('添加失败：' + (result.error || '未知错误'));
  }
});

// ---- 每日目标 ----
document.getElementById('save-targets-btn').addEventListener('click', async () => {
  const target_kcal = parseFloat(document.getElementById('target-kcal-input').value);
  const target_protein = parseFloat(document.getElementById('target-protein-input').value);
  const carbRaw = document.getElementById('target-carb-input').value;
  const fatRaw = document.getElementById('target-fat-input').value;
  const target_carb = carbRaw === '' ? 0 : parseFloat(carbRaw);
  const target_fat = fatRaw === '' ? 0 : parseFloat(fatRaw);

  if (isNaN(target_kcal) || isNaN(target_protein) || target_kcal <= 0 || target_protein <= 0) {
    alert('热量和蛋白质目标必须是大于0的数字');
    return;
  }
  if (isNaN(target_carb) || isNaN(target_fat) || target_carb < 0 || target_fat < 0) {
    alert('碳水/脂肪目标不能是负数（可以留空或填0，代表不设目标线）');
    return;
  }

  const res = await fetch('/api/settings/update', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target_kcal, target_protein, target_carb, target_fat })
  });
  const result = await res.json();
  if (result.ok) {
    alert('目标已更新');
    loadInitial();
  } else {
    alert('保存失败：' + (result.error || '未知错误'));
  }
});

// ---- 用户管理(仅管理员可见可用，后端也会再校验一次权限，前端隐藏只是体验层面) ----
async function loadUsersList() {
  if (!CURRENT_IS_ADMIN) return;
  const container = document.getElementById('users-list');
  container.innerHTML = '<div class="empty-note">加载中…</div>';

  const res = await fetch('/api/users');
  if (res.status === 403) { container.innerHTML = ''; return; }
  const data = await res.json();

  container.innerHTML = '';
  data.users.forEach(u => {
    const row = document.createElement('div');
    row.className = 'entry-row';
    row.innerHTML = `
      <div class="entry-info">
        <div class="entry-name">${u.username}${u.is_admin ? '（管理员）' : ''}</div>
      </div>
      <div class="entry-controls">
        <button class="entry-delete-btn">删除</button>
      </div>
    `;
    container.appendChild(row);
    row.querySelector('.entry-delete-btn').addEventListener('click', async () => {
      if (!confirm(`确定删除用户"${u.username}"？删除后这个人将无法再登录。`)) return;
      const res = await fetch('/api/users/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: u.username })
      });
      const result = await res.json();
      if (result.ok) {
        loadUsersList();
      } else {
        alert('删除失败：' + (result.error || '未知错误'));
      }
    });
  });
}

document.getElementById('add-user-save-btn').addEventListener('click', async () => {
  const username = document.getElementById('new-user-name').value.trim();
  const password = document.getElementById('new-user-pass').value.trim();
  const is_admin = document.getElementById('new-user-admin').checked;
  if (!username || !password) { alert('用户名和密码不能为空'); return; }

  const res = await fetch('/api/users/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password, is_admin })
  });
  const result = await res.json();
  if (result.ok) {
    document.getElementById('new-user-name').value = '';
    document.getElementById('new-user-pass').value = '';
    document.getElementById('new-user-admin').checked = false;
    loadUsersList();
  } else {
    alert('添加失败：' + (result.error || '未知错误'));
  }
});

// 根据localStorage里存的偏好，初始化指标选择器按钮的高亮状态
document.querySelectorAll('#metric-picker .range-btn').forEach(btn => {
  btn.classList.toggle('active', selectedMetrics.has(btn.dataset.metric));
});

// 必须先等loadInitial()真正拿到is_admin状态，applyHash()才能正确判断
// 要不要在#foods这个hash下加载用户列表——两者不能并发触发。
(async () => {
  await loadInitial();
  applyHash();  // 如果页面一打开地址栏就带着#history或#foods，直接呈现对应弹窗
})();

// 页面被切到后台再切回来时(比如晚上开着放一边，第二天早上回来看)，
// 自动重新拉一次数据——不用轮询定时器一直悄悄耗资源，只在你真的
// 回来看这个页面的那一刻才刷新，这是标准的"按需刷新"做法。
// 只在编辑历史/管理食物这两个弹窗都没开着的时候才自动刷新，避免正在
// 填表填到一半、切一下屏幕回来，表单内容被意外清空重置。
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible') return;
  const anyModalOpen = Object.values(MODALS).some(m => m.classList.contains('open'));
  if (anyModalOpen) return;
  loadInitial();
});
</script>
</body>
</html>
"""


LOGIN_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>登录</title>
<style>
  :root {
    --bg: #1a1d24; --panel: #22262f; --ink: #dcdde0; --ink-soft: #838995;
    --sage: #4a7c6f; --sage-dim: #5f9186; --border: #333944; --danger: #c1595f;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: var(--bg); color: var(--ink);
    font-family: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;
    min-height: 100vh; display: flex; align-items: center; justify-content: center; }
  .box { width: 100%; max-width: 320px; background: var(--panel); border: 1px solid var(--border);
    border-radius: 8px; padding: 28px 24px; margin: 16px; }
  h1 { font-size: 15px; margin: 0 0 20px; color: var(--ink); text-align: center; }
  label { display: block; font-size: 12px; color: var(--ink-soft); margin-bottom: 4px; }
  input { width: 100%; background: var(--bg); border: 1px solid var(--border); color: var(--ink);
    font-family: inherit; font-size: 15px; padding: 10px 12px; border-radius: 6px; margin-bottom: 14px; }
  button { width: 100%; background: var(--sage); color: #fffefb; border: none; border-radius: 6px;
    font-family: inherit; font-weight: 600; font-size: 14px; padding: 11px; cursor: pointer; }
  button:hover { background: var(--sage-dim); }
  .error { color: var(--danger); font-size: 12.5px; margin-bottom: 12px; text-align: center; }
</style>
</head>
<body>
  <div class="box">
    <h1>饮食记录</h1>
    <div class="error" id="error"></div>
    <form id="login-form">
      <label>用户名</label>
      <input type="text" id="username" autocomplete="username" required>
      <label>密码</label>
      <input type="password" id="password" autocomplete="current-password" required>
      <button type="submit">登录</button>
    </form>
  </div>
<script>
document.getElementById('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const username = document.getElementById('username').value;
  const password = document.getElementById('password').value;
  const res = await fetch('/api/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password })
  });
  if (res.ok) {
    window.location.href = '/';
  } else {
    document.getElementById('error').textContent = '用户名或密码不对';
  }
});
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 不在终端刷屏打印每次请求日志

    def _current_user(self):
        if not AUTH_ENABLED:
            return "local"
        cookies = _parse_cookies(self.headers.get("Cookie", ""))
        token = cookies.get("session")
        if not token:
            return None
        return _get_session_user(token)

    def _current_is_admin(self):
        if not AUTH_ENABLED:
            return False  # 本地无认证模式下，"管理员"这个概念没有意义，统一当作否
        cookies = _parse_cookies(self.headers.get("Cookie", ""))
        token = cookies.get("session")
        if not token:
            return False
        session = _get_session(token)
        return bool(session and session.get("is_admin"))

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html, status=200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            self._handle_get()
        except Exception:
            import traceback
            traceback.print_exc()  # 完整报错堆栈打到标准输出，行缓冲已经打开，Render能实时看到
            try:
                self._send_json({"error": "服务器内部错误，已记录到日志"}, status=500)
            except Exception:
                pass  # 连错误响应都发不出去的话（比如连接已经断了），不再继续折腾

    def _handle_get(self):
        parsed = urlparse(self.path)

        if parsed.path == "/login":
            if not AUTH_ENABLED:
                # 没配置账号密码，说明这个部署根本没启用登录验证，
                # 这个页面在这种情况下永远登不进去（没有账号可以核对），
                # 与其让人卡在一个注定失败的表单前，不如直接送回首页。
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()
                return
            self._send_html(LOGIN_PAGE)
            return

        user = self._current_user()
        if not user:
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
            return

        if parsed.path == "/":
            logout_html = '<button class="btn-small logout-btn" id="logout-btn">登出</button>' if AUTH_ENABLED else ''
            self._send_html(HTML_PAGE.replace("__LOGOUT_BUTTON__", logout_html))
        elif parsed.path == "/api/data":
            qs = parse_qs(parsed.query)
            try:
                days = int(qs.get("days", ["7"])[0])
            except ValueError:
                days = 7
            days = max(1, min(days, 365))  # 防止传入0、负数或离谱的大数字
            client_date, _ = _parse_client_datetime(qs.get("client_datetime", [None])[0])
            payload = get_data_payload(days, client_date)
            payload["current_user"] = user
            payload["is_admin"] = self._current_is_admin()
            self._send_json(payload)
        elif parsed.path == "/api/day":
            qs = parse_qs(parsed.query)
            date_str = qs.get("date", [""])[0]
            entries = nt.get_entries_for_date(date_str)
            self._send_json({"date": date_str, "entries": entries})
        elif parsed.path == "/api/users":
            if not self._current_is_admin():
                self._send_json({"error": "只有管理员能查看用户列表"}, status=403)
                return
            self._send_json({"users": _get_users_list()})
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        try:
            self._handle_post()
        except Exception:
            import traceback
            traceback.print_exc()
            try:
                self._send_json({"error": "服务器内部错误，已记录到日志"}, status=500)
            except Exception:
                pass

    def _handle_post(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/login":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_json({"error": "请求格式错误"}, status=400)
                return
            username = data.get("username", "")
            password = data.get("password", "")
            if nt.check_login(username, password):
                token = _create_session(username)
                self.send_response(200)
                cookie_attrs = f"session={token}; Path=/; HttpOnly; Max-Age={SESSION_TTL_SECONDS}"
                if SECURE_COOKIE:
                    cookie_attrs += "; Secure"
                self.send_header("Set-Cookie", cookie_attrs)
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self._send_json({"error": "用户名或密码不对"}, status=401)
            return

        if parsed.path == "/api/logout":
            self.send_response(200)
            self.send_header("Set-Cookie", "session=; Path=/; HttpOnly; Max-Age=0")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        user = self._current_user()
        if not user:
            self._send_json({"error": "未登录"}, status=401)
            return

        if parsed.path == "/api/clear-today":
            qs = parse_qs(parsed.query)
            try:
                days = int(qs.get("days", ["7"])[0])
            except ValueError:
                days = 7
            days = max(1, min(days, 365))
            client_date, _ = _parse_client_datetime(qs.get("client_datetime", [None])[0])

            nt.clear_today_entries(today_override=client_date)
            nt.write_summary_csv()
            self._send_json(get_data_payload(days, client_date))
            return

        if parsed.path == "/api/undo":
            qs = parse_qs(parsed.query)
            try:
                days = int(qs.get("days", ["7"])[0])
            except ValueError:
                days = 7
            days = max(1, min(days, 365))
            client_date, _ = _parse_client_datetime(qs.get("client_datetime", [None])[0])

            removed = nt.undo_last_entry(today_override=client_date)
            nt.write_summary_csv()
            payload = get_data_payload(days, client_date)
            payload["undone"] = removed["food"] if removed else None
            self._send_json(payload)
            return

        if parsed.path == "/api/entry/delete":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_json({"error": "请求格式错误"}, status=400)
                return
            entry_id = data.get("id", "")
            ok = nt.delete_entry_by_id(entry_id)
            nt.write_summary_csv()
            self._send_json({"ok": ok})
            return

        if parsed.path == "/api/entry/update":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_json({"error": "请求格式错误"}, status=400)
                return
            entry_id = data.get("id", "")
            try:
                new_servings = float(data.get("servings"))
            except (TypeError, ValueError):
                self._send_json({"error": "份数格式不对"}, status=400)
                return
            if new_servings <= 0:
                self._send_json({"error": "份数必须大于0"}, status=400)
                return
            ok = nt.update_entry_servings(entry_id, new_servings)
            nt.write_summary_csv()
            self._send_json({"ok": ok})
            return

        if parsed.path == "/api/foods/add":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_json({"error": "请求格式错误"}, status=400)
                return
            try:
                new_id = nt.add_food(
                    name=str(data.get("name", "")).strip(),
                    unit=str(data.get("unit", "")).strip(),
                    kcal=float(data.get("kcal", 0)),
                    protein=float(data.get("protein", 0)),
                    carb=float(data.get("carb", 0)),
                    fat=float(data.get("fat", 0)),
                    tracker=data.get("tracker") or None,
                )
            except (TypeError, ValueError):
                self._send_json({"error": "营养数值格式不对"}, status=400)
                return
            if not data.get("name", "").strip():
                self._send_json({"error": "食物名字不能为空"}, status=400)
                return
            client_date, _ = _parse_client_datetime(data.get("client_datetime"))
            self._send_json({"ok": True, "id": new_id, "foods": get_foods_list(client_date)})
            return

        if parsed.path == "/api/foods/update":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_json({"error": "请求格式错误"}, status=400)
                return
            food_id = data.get("id", "")
            if not str(data.get("name", "")).strip():
                self._send_json({"error": "食物名字不能为空"}, status=400)
                return
            try:
                ok, updated_count = nt.update_food(
                    food_id,
                    name=str(data.get("name", "")).strip(),
                    unit=str(data.get("unit", "")).strip(),
                    kcal=float(data.get("kcal", 0)),
                    protein=float(data.get("protein", 0)),
                    carb=float(data.get("carb", 0)),
                    fat=float(data.get("fat", 0)),
                    tracker=data.get("tracker") or None,
                )
            except (TypeError, ValueError):
                self._send_json({"error": "营养数值格式不对"}, status=400)
                return
            nt.write_summary_csv()  # 历史记录的营养值可能变了，导出用的汇总文件要跟着同步
            client_date, _ = _parse_client_datetime(data.get("client_datetime"))
            self._send_json({"ok": ok, "updated_entries": updated_count, "foods": get_foods_list(client_date)})
            return

        if parsed.path == "/api/foods/delete":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_json({"error": "请求格式错误"}, status=400)
                return
            food_id = data.get("id", "")
            ok = nt.delete_food(food_id)
            client_date, _ = _parse_client_datetime(data.get("client_datetime"))
            self._send_json({"ok": ok, "foods": get_foods_list(client_date)})
            return

        if parsed.path == "/api/settings/update":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_json({"error": "请求格式错误"}, status=400)
                return
            try:
                target_kcal = float(data.get("target_kcal"))
                target_protein = float(data.get("target_protein"))
                # 碳水/脂肪目标允许是0——0表示"不设目标线"，跟热量/蛋白质不一样，
                # 那两个是核心指标必须大于0，碳水脂肪是可选的，不填就是不显示目标线。
                target_carb = float(data.get("target_carb", 0) or 0)
                target_fat = float(data.get("target_fat", 0) or 0)
            except (TypeError, ValueError):
                self._send_json({"error": "目标值格式不对"}, status=400)
                return
            if target_kcal <= 0 or target_protein <= 0:
                self._send_json({"error": "热量和蛋白质目标必须大于0"}, status=400)
                return
            if target_carb < 0 or target_fat < 0:
                self._send_json({"error": "目标值不能是负数"}, status=400)
                return
            nt.update_targets(target_kcal, target_protein, target_carb, target_fat)
            self._send_json({
                "ok": True,
                "target_kcal": target_kcal,
                "target_protein": target_protein,
                "target_carb": target_carb,
                "target_fat": target_fat,
            })
            return

        if parsed.path == "/api/users/add":
            if not self._current_is_admin():
                self._send_json({"error": "只有管理员能管理用户账号"}, status=403)
                return
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_json({"error": "请求格式错误"}, status=400)
                return
            ok, err = nt.add_user(
                username=str(data.get("username", "")).strip(),
                password=str(data.get("password", "")).strip(),
                is_admin=bool(data.get("is_admin", False)),
            )
            self._send_json({"ok": ok, "error": err, "users": _get_users_list()})
            return

        if parsed.path == "/api/users/delete":
            if not self._current_is_admin():
                self._send_json({"error": "只有管理员能管理用户账号"}, status=403)
                return
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_json({"error": "请求格式错误"}, status=400)
                return
            ok, err = nt.delete_user(str(data.get("username", "")).strip())
            self._send_json({"ok": ok, "error": err, "users": _get_users_list()})
            return

        if parsed.path != "/api/record":
            self.send_error(404, "Not Found")
            return

        qs = parse_qs(parsed.query)
        try:
            days = int(qs.get("days", ["7"])[0])
        except ValueError:
            days = 7
        days = max(1, min(days, 365))
        client_date, client_dt = _parse_client_datetime(qs.get("client_datetime", [None])[0])

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            selections = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json({"error": "请求格式错误"}, status=400)
            return

        recorded = []
        for food_id, servings in selections.items():
            if food_id not in nt.FOODS:
                continue
            try:
                servings = float(servings)
            except (TypeError, ValueError):
                continue
            if servings <= 0:
                continue
            item = nt.FOODS[food_id]
            nt.append_entry(food_id, item, servings, client_datetime=client_dt)
            recorded.append(f"{item['name']} x{servings}份")

        nt.write_summary_csv()

        payload = get_data_payload(days, client_date)
        payload["recorded"] = recorded
        self._send_json(payload)


def main():
    nt.ensure_csv_exists()
    nt.write_summary_csv()  # 启动时先同步一次，反映明细表当前的真实内容

    # 部署到Render这类平台时，平台会通过PORT环境变量告诉你该监听哪个端口，
    # 并且必须绑定0.0.0.0而不是localhost，否则平台连不到你的服务。
    # 本地跑的时候没有这个环境变量，就还是用localhost:8420，行为不变。
    port = int(os.environ.get("PORT", PORT))
    host = "0.0.0.0" if "PORT" in os.environ else "localhost"

    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://localhost:{port}/" if host == "localhost" else f"http://{host}:{port}/"
    print(f"服务已启动: {url}")
    print("按 Ctrl+C 停止服务")

    if host == "localhost":
        try:
            webbrowser.open(url)
        except Exception:
            pass  # 自动打开浏览器失败也没关系，手动打开上面那个网址就行

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
        server.shutdown()


if __name__ == "__main__":
    main()
