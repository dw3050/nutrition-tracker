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
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import nutrition_tracker as nt

PORT = 8420


def get_week_payload(days=7):
    """
    返回过去n天的结构化数据（不是渲染好的文本），
    交给前端用CSS画柱状图，不依赖等宽字体里中文=2倍宽度这个在浏览器里不成立的假设。
    终端版(nutrition_tracker.py里的print_weekly_bar_chart)不受影响，继续用文本渲染，
    因为终端环境这个假设是成立的。
    """
    dates, data = nt.get_last_n_days_data(days)
    veg_days = nt.get_vegetable_days()
    today_str = nt.datetime.now().date().strftime("%Y-%m-%d")

    week = []
    for d in dates:
        key = d.strftime("%Y-%m-%d")
        week.append({
            "date": key,
            "label": d.strftime("%m-%d"),
            "is_today": key == today_str,
            "kcal": data[key]["kcal"],
            "protein": data[key]["protein"],
            "veg": key in veg_days,
        })
    return week


def get_foods_list():
    eaten_today = nt.get_today_servings_by_food()
    return [
        {
            "id": food_id,
            "name": item["name"],
            "unit": item["unit"],
            "kcal": item["kcal"],
            "protein": item["protein"],
            "eaten_today": eaten_today.get(food_id, 0.0),
        }
        for food_id, item in nt.FOODS.items()
    ]


def get_data_payload(days=7):
    nt.write_summary_csv()  # 顺手把导出用的汇总文件也同步一次，不影响图表本身的计算
    return {
        "week": get_week_payload(days),
        "foods": get_foods_list(),
        "target_kcal": nt.DAILY_KCAL_TARGET,
        "target_protein": nt.DAILY_PROTEIN_TARGET,
    }


HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>饮食记录</title>
<style>
  :root {
    --bg: #f6f4ee;
    --panel: #fffefb;
    --ink: #3c3b34;
    --ink-soft: #8b8776;
    --sage: #5f8a72;
    --sage-dim: #7fa48f;
    --border: #e4e0d3;
    --flash: #edf1e9;
    --track: #eeece3;
    --danger: #b1543f;
    --danger-flash: #f5e8e4;
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

  .clear-btn {
    background: transparent;
    border: 1px solid var(--danger);
    color: var(--danger);
    font-family: inherit;
    font-size: 11px;
    padding: 5px 10px;
    border-radius: 5px;
    cursor: pointer;
    transition: background 0.15s ease;
    margin-left: auto;
  }
  .clear-btn:hover { background: var(--danger-flash); }

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
    width: 14px;
    flex-shrink: 0;
    text-align: center;
    color: var(--sage);
    font-size: 12px;
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
    padding: 5px 10px;
    border-radius: 5px;
    cursor: pointer;
  }
  .range-btn.active {
    background: var(--sage);
    border-color: var(--sage);
    color: #fffefb;
  }
  .days-input {
    width: 84px;
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--ink);
    font-family: inherit;
    font-size: 12px;
    padding: 5px 8px;
    border-radius: 5px;
  }

  .food-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 7px 6px;
    border-radius: 4px;
    transition: background 0.5s ease;
  }
  .food-row.flash { background: var(--flash); }
  .food-info { flex: 1; min-width: 0; }
  .food-name { color: var(--ink); font-size: 13.5px; }
  .food-meta { color: var(--ink-soft); font-size: 11.5px; margin-top: 1px; }

  .food-progress {
    width: 32px;
    flex-shrink: 0;
    text-align: right;
    font-size: 13px;
    font-weight: 600;
    color: var(--sage);
    margin-right: 14px;
  }

  .food-input {
    width: 52px;
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--ink);
    font-family: inherit;
    font-size: 14px;
    padding: 5px 7px;
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
    box-shadow: 0 0 0 2px rgba(95,138,114,0.15);
  }

</style>
</head>
<body>
  <div class="panel">

    <div class="section-label">显示范围</div>
    <div class="range-picker">
      <button class="range-btn active" data-days="7">7天</button>
      <button class="range-btn" data-days="30">30天</button>
      <button class="range-btn" data-days="60">60天</button>
      <input type="number" id="custom-days" class="days-input" placeholder="自定义天数" min="1" max="365">
      <button class="clear-btn" id="clear-btn">清空今天</button>
    </div>

    <div class="section-label" id="range-title">过去 7 天</div>
    <div id="chart-kcal" class="chart-group"></div>
    <div id="chart-protein" class="chart-group"></div>

    <div class="section-label">记录</div>
    <div id="food-list"></div>

  </div>

<script>
let FOODS = [];
let currentDays = 7;

async function loadInitial() {
  const res = await fetch(`/api/data?days=${currentDays}`);
  const data = await res.json();
  FOODS = data.foods;
  renderCharts(data);
  renderFoodList();
}

function setDays(n) {
  currentDays = n;
  document.querySelectorAll('.range-btn').forEach(btn => {
    btn.classList.toggle('active', parseInt(btn.dataset.days) === n);
  });
  document.getElementById('custom-days').value = '';
  loadInitial();
}

function renderCharts(data) {
  document.getElementById('range-title').textContent = `过去 ${currentDays} 天`;
  document.getElementById('chart-kcal').innerHTML =
    buildChart('🔥 总热量 (kcal)', data.week, 'kcal', data.target_kcal, true);
  document.getElementById('chart-protein').innerHTML =
    buildChart('💪 总蛋白质 (g)', data.week, 'protein', data.target_protein, false);
}

function buildChart(title, week, key, target, showVeg) {
  const maxVal = Math.max(...week.map(d => d[key]), target, 1);
  const rows = week.map(d => {
    const val = d[key];
    const fillPct = (val / maxVal) * 100;
    const targetPct = (target / maxVal) * 100;
    const labelClass = d.is_today ? 'bar-label today' : 'bar-label';
    const todayTag = d.is_today ? '今天' : '';
    const vegMark = showVeg && d.veg ? '✓' : '';
    return `
      <div class="bar-row">
        <div class="${labelClass}">${d.label}</div>
        <div class="bar-today-tag">${todayTag}</div>
        <div class="bar-track">
          <div class="bar-fill" style="width:${fillPct}%"></div>
          <div class="bar-target-line" style="left:${targetPct}%"></div>
        </div>
        <div class="bar-value">${val.toFixed(1)}</div>
        ${showVeg ? `<div class="bar-veg">${vegMark}</div>` : ''}
      </div>
    `;
  }).join('');

  return `<div class="chart-title">${title} <span class="target">目标: ${target}</span></div>${rows}`;
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
  const inputs = [];

  FOODS.forEach(food => {
    const row = document.createElement('div');
    row.className = 'food-row';
    row.id = `row-${food.id}`;
    row.innerHTML = `
      <div class="food-info">
        <div class="food-name">${food.name}</div>
        <div class="food-meta">${food.unit} · ${food.kcal}kcal / ${food.protein}g蛋白</div>
      </div>
      <div class="food-progress" id="progress-${food.id}">${progressText(food)}</div>
      <input type="number" step="any" class="food-input" data-id="${food.id}" value="" placeholder="0">
    `;
    container.appendChild(row);
    const input = row.querySelector('.food-input');
    inputs.push(input);

    input.addEventListener('keydown', e => {
      if (e.key === 'Enter') {
        e.preventDefault();
        submitOne(food.id, input, row, inputs);
        return;
      }
      // 上下箭头：不改数值，改成在输入框之间跳，跟原生spin行为反过来
      if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
        e.preventDefault();
        const idx = inputs.indexOf(input);
        const nextIdx = e.key === 'ArrowUp' ? idx - 1 : idx + 1;
        if (nextIdx >= 0 && nextIdx < inputs.length) {
          inputs[nextIdx].focus();
          inputs[nextIdx].select();
        }
      }
    });
  });
}

function refreshProgress(updatedFoods) {
  // 只更新每行左边"今天吃了多少"的数字，不重建整个列表，
  // 这样其他还没提交的输入框内容不会被打断。
  updatedFoods.forEach(food => {
    const el = document.getElementById(`progress-${food.id}`);
    if (!el) return;
    el.textContent = progressText(food);
  });
  FOODS = updatedFoods;
}

async function submitOne(foodId, input, row, inputs) {
  const v = parseFloat(input.value);
  if (isNaN(v) || v <= 0) return;

  try {
    const res = await fetch(`/api/record?days=${currentDays}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [foodId]: v })
    });
    const data = await res.json();

    renderCharts(data);
    refreshProgress(data.foods);
    input.value = '';

    row.classList.add('flash');
    setTimeout(() => row.classList.remove('flash'), 900);

    // 回车提交完，自动跳到下一个输入框，方便连续记录
    const idx = inputs.indexOf(input);
    if (idx >= 0 && idx + 1 < inputs.length) {
      inputs[idx + 1].focus();
    }
  } catch (err) {
    alert('记录失败，请检查服务是否还在运行。');
    console.error(err);
  }
}

document.querySelectorAll('.range-btn').forEach(btn => {
  btn.addEventListener('click', () => setDays(parseInt(btn.dataset.days)));
});

document.getElementById('custom-days').addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    e.preventDefault();
    const v = parseInt(e.target.value);
    if (!isNaN(v) && v > 0) {
      currentDays = v;
      document.querySelectorAll('.range-btn').forEach(btn => btn.classList.remove('active'));
      loadInitial();
    }
  }
});

document.getElementById('clear-btn').addEventListener('click', async () => {
  if (!confirm('清空今天记录的所有食物？这个操作不能撤销。')) return;

  try {
    const res = await fetch(`/api/clear-today?days=${currentDays}`, { method: 'POST' });
    const data = await res.json();
    FOODS = data.foods;
    renderCharts(data);
    renderFoodList();
  } catch (err) {
    alert('清空失败，请检查服务是否还在运行。');
    console.error(err);
  }
});

loadInitial();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 不在终端刷屏打印每次请求日志

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/":
            body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/api/data":
            qs = parse_qs(parsed.query)
            try:
                days = int(qs.get("days", ["7"])[0])
            except ValueError:
                days = 7
            days = max(1, min(days, 365))  # 防止传入0、负数或离谱的大数字
            self._send_json(get_data_payload(days))
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/clear-today":
            qs = parse_qs(parsed.query)
            try:
                days = int(qs.get("days", ["7"])[0])
            except ValueError:
                days = 7
            days = max(1, min(days, 365))

            nt.clear_today_entries()
            nt.write_summary_csv()
            self._send_json(get_data_payload(days))
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
            nt.append_entry(item, servings)
            recorded.append(f"{item['name']} x{servings}份")

        nt.write_summary_csv()

        payload = get_data_payload(days)
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
