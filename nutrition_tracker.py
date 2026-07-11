#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日饮食营养记录小程序
用法: python3 nutrition_tracker.py
数据保存在同目录下的 nutrition_log.csv 中，按日期自动区分"今日汇总"。
"""

import csv
import os
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# 食物库：每份的营养成分（可自行修改这里的数值）
# 格式: "名称": (卡路里, 蛋白质g, 碳水g, 脂肪g)
#
# 数据可信度说明（2026-07-03 核实）：
# 1. 煎鸡蛋：USDA基准90kcal/6.26g蛋白/6.83g脂肪/0.38g碳水较可靠；
#    因你用橄榄油且油量偏多，额外加了约20-25kcal/2g脂肪的估算值。
#    误差带 ±20kcal，主要不确定性来自实际用油量，不是鸡蛋本身。
# 2. 无糖燕麦粥：130kcal为你提供的产品已知值，直接采用；
#    蛋白/碳水/脂肪拆分按USDA纯燕麦干重宏量比例（17%/67%/16%热量占比）反推，
#    不是你实际产品的实测值。如果产品有额外配料（蛋白粉/坚果等），误差会更大。
# 3. 水煮虾：基于你提供的Bowen Basket包装数据(4oz/112g=80kcal/15g蛋白/
#    170mg胆固醇/570mg钠)，按10只虾≈128g生重换算。
#    ⚠️ 重要发现：换算成每100g后，蛋白质(13.4g)明显低于USDA天然生虾(20.1g)，
#    钠(509mg)是天然虾肉本身钠含量(约224mg)的两倍多——大概率是"增强/腌渍虾"
#    (注水+盐水/磷酸盐工艺)，不是纯虾肉。水煮后实际摄入钠可能因溶出流失10-30%，
#    脂肪含量包装上未提供，暂标"待核实"，不要完全当真。
# 4. 蛋白粉：⚠️ 未验证——没有具体品牌/规格信息，无法查证，以下为行业通用估算，
#    误差可能达到±50%甚至更高。强烈建议提供实际包装信息后替换此项。
# 5. 冷冻蔬菜包(微波即食,含芝士调味)：⚠️ 低可信度——你说明了"每袋配料不固定"，
#    没有单一产品可查证。以下数值是参照同类"冷冻蔬菜+芝士酱"产品的宏量比例，
#    按你提供的150kcal换算得来，不是你实际那一袋的实测值，仅供大致参考。
# ---------------------------------------------------------------------------
FOODS = {
    # 数据可信度说明（原本写在显示名称里的标注，现在移到这里——
    # 删掉显示名称上的括号标注是纯UI清理，不代表这些数据变得更可信了，
    # 该有的保留度还是要保留，只是不再刷在你每天看到的界面上）：
    #
    # 1. 煎鸡蛋：用橄榄油煎、油量偏多，误差±20kcal，主要不确定性来自实际用油量。
    # 4. 蛋白粉：⚠️ 仍未验证——没有具体品牌/规格信息，以下是行业通用估算，
    #    误差可能达到±50%甚至更高。你现在把它加到2份/天，等于在一个未验证的
    #    数字上加倍下注，等你哪天把包装发我，这个数字大概率要整体重算。
    # 5. 冷冻蔬菜包：品牌已确认是Birds Eye Steamfresh。已用真实产品数据修正
    #    （之前的150kcal/6g蛋白是错的，用错了参考产品）。目标份数锁定1份/天，
    #    这是你的现实食量上限，不再往上调。
    # 6. 鸡胸肉：数据来源USDA生鸡胸肉每100g=114kcal/21.2g蛋白/2.6g脂肪，
    #    按你说的"盒子上的生重"换算到1磅。目标份数锁定1磅/天，不再往上调
    #    （你明确说过一天超过1磅吃不下）。
    # 7/8. 香蕉、牛油果：为了补钾加入的两项。数据来源USDA标准中等大小份量
    #    （香蕉118g、牛油果整个约201g）。这两项加起来能把每日钾摄入覆盖率
    #    从原来接近0提到大约37%-47%，仍有明显缺口，不是"解决方案"，
    #    是"性价比最高的部分缓解"，你自己决定这个覆盖率够不够。
    "1": {"name": "煎鸡蛋",     "unit": "1个",  "kcal": 115, "protein": 6.3,  "carb": 0.4,  "fat": 9},
    "2": {"name": "无糖燕麦粥", "unit": "1份",  "kcal": 130, "protein": 5.5,  "carb": 21.8, "fat": 2.3},
    "3": {"name": "水煮虾",     "unit": "10只", "kcal": 91,  "protein": 17,   "carb": 0,    "fat": 0.8},
    "4": {"name": "蛋白粉",     "unit": "1勺",  "kcal": 120, "protein": 24,   "carb": 3,    "fat": 1},
    "5": {"name": "冷冻蔬菜包", "unit": "1袋",  "kcal": 100, "protein": 3.3,  "carb": 18.3, "fat": 1.7},
    "6": {"name": "鸡胸肉",     "unit": "1磅",  "kcal": 517, "protein": 96.2, "carb": 0,    "fat": 11.8},
    "7": {"name": "香蕉",       "unit": "1根",  "kcal": 105, "protein": 1.3,  "carb": 27,   "fat": 0.4},
    "8": {"name": "牛油果",     "unit": "1个",  "kcal": 322, "protein": 4,    "carb": 17,   "fat": 29.5},
    # 9. 额外热量：不是真实食物，是给计划外饮食用的热量补记项。
    #    单份定义为1卡路里，所以你输入的数字就直接是总热量，不需要换算。
    #    蛋白质/碳水/脂肪都设成0——这里只管热量，不追踪其他营养素，
    #    这是你明确说过不需要管的。
    "9": {"name": "额外热量",   "unit": "1卡",  "kcal": 1,   "protein": 0,    "carb": 0,    "fat": 0},
}

# 用于图表里判断"今天是否吃了蔬菜"时匹配的食物名称
VEGETABLE_FOOD_NAME = FOODS["5"]["name"]

# 每日目标（用于柱状图里画目标线）
# 注意：这两个数字不因为鸡胸肉份量的不确定性而调整——它们是根据体重/身高/
# 年龄/活动量/减脂速率算出来的独立生理需求值，跟某一种食物一份到底是1.1磅
# 还是1.4磅没有因果关系，不应该因为后者去反推调整前者。
DAILY_KCAL_TARGET = 1800
DAILY_PROTEIN_TARGET = 200

# 数据文件存放目录。
# 本地跑（不设置NUTRITION_DATA_DIR环境变量）时，跟以前一样存在脚本所在文件夹。
# 部署到Render这类平台时，需要把这个环境变量指向持久磁盘的挂载路径，
# 否则CSV文件会写到每次部署都会被清空的临时目录里，磁盘白挂了。
DATA_DIR = os.environ.get("NUTRITION_DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
os.makedirs(DATA_DIR, exist_ok=True)

CSV_PATH = os.path.join(DATA_DIR, "nutrition_log.csv")
CSV_HEADER = ["date", "time", "food", "servings", "kcal", "protein_g", "carb_g", "fat_g"]

# 第二份CSV：每天一行的汇总（只含热量/蛋白质/碳水，按你的要求不含脂肪）
SUMMARY_CSV_PATH = os.path.join(DATA_DIR, "nutrition_daily_summary.csv")
SUMMARY_HEADER = ["date", "total_kcal", "total_protein_g", "total_carb_g"]


def ensure_csv_exists():
    if not os.path.exists(CSV_PATH):
        with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER)


def append_entry(food_item, servings):
    now = datetime.now()
    with open(CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            now.strftime("%Y-%m-%d"),
            now.strftime("%H:%M:%S"),
            food_item["name"],
            servings,
            round(food_item["kcal"] * servings, 1),
            round(food_item["protein"] * servings, 1),
            round(food_item["carb"] * servings, 1),
            round(food_item["fat"] * servings, 1),
        ])


def clear_today_entries():
    """
    删除明细表(CSV_PATH)里"今天"的所有行，历史数据不受影响。
    用于误输入后的一键重来，不是撤销单条记录——是把今天清零重新开始。
    做法是把不是今天的行原样保留、重写整个文件，而不是"追加一条反向抵消记录"，
    这样CSV里不会留下垃圾数据，今天这一天在文件里就跟没记录过一样干净。
    """
    today = datetime.now().strftime("%Y-%m-%d")

    if not os.path.exists(CSV_PATH):
        return

    kept_rows = []
    with open(CSV_PATH, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("date") != today:
                kept_rows.append(row)

    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        writer.writeheader()
        for row in kept_rows:
            writer.writerow(row)


def get_today_total():
    today = datetime.now().strftime("%Y-%m-%d")
    total = {"kcal": 0.0, "protein": 0.0, "carb": 0.0, "fat": 0.0}
    records = []
    if not os.path.exists(CSV_PATH):
        return total, records
    with open(CSV_PATH, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["date"] != today:
                continue
            try:
                kcal = float(row["kcal"])
                protein = float(row["protein_g"])
                carb = float(row["carb_g"])
                fat = float(row["fat_g"])
            except (TypeError, ValueError, KeyError):
                print(f"⚠️  明细表里有一行数据读不了，已跳过: {row}")
                continue
            records.append(row)
            total["kcal"] += kcal
            total["protein"] += protein
            total["carb"] += carb
            total["fat"] += fat
    return total, records


def compute_all_daily_totals():
    """
    唯一的"按天汇总"计算入口——直接扫描明细表(CSV_PATH)，按日期分组求和。
    这是整个程序里关于"每天摄入多少"的唯一权威计算逻辑，
    图表和汇总CSV导出都必须调用这个函数，不能各自维护一份计算逻辑，
    否则又会变回"两处数据可能对不上"的老问题。
    对每一行做了容错：读不了的行会跳过并打印警告，不会导致整个程序崩溃
    （这在你手动编辑明细表时尤其重要，一行格式错了不该影响其它所有天的数据）。
    """
    totals_by_date = {}
    if not os.path.exists(CSV_PATH):
        return totals_by_date

    with open(CSV_PATH, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                date_key = row["date"]
                kcal = float(row["kcal"])
                protein = float(row["protein_g"])
                carb = float(row["carb_g"])
            except (TypeError, ValueError, KeyError):
                print(f"⚠️  明细表里有一行数据读不了，已跳过: {row}")
                continue

            if date_key not in totals_by_date:
                totals_by_date[date_key] = {"kcal": 0.0, "protein": 0.0, "carb": 0.0}
            totals_by_date[date_key]["kcal"] += kcal
            totals_by_date[date_key]["protein"] += protein
            totals_by_date[date_key]["carb"] += carb

    return totals_by_date


def get_last_n_days_data(n=7):
    """
    返回过去n天（含今天）的日期列表(旧→新)，以及对应的 {kcal, protein} 数据。
    直接调用 compute_all_daily_totals() 现算，不再读 SUMMARY_CSV_PATH。
    这样无论你手动怎么改明细表，图表永远反映明细表的真实内容，
    汇总CSV文件的状态完全不影响程序自己的计算结果——它只是个单向导出的报告，
    不再是程序信任的数据来源。
    """
    today = datetime.now().date()
    dates = [today - timedelta(days=i) for i in range(n - 1, -1, -1)]  # n-1天前 -> 今天
    all_totals = compute_all_daily_totals()

    data = {}
    for d in dates:
        key = d.strftime("%Y-%m-%d")
        day_total = all_totals.get(key, {"kcal": 0.0, "protein": 0.0, "carb": 0.0})
        data[key] = {"kcal": day_total["kcal"], "protein": day_total["protein"]}

    return dates, data


def get_today_servings_by_food():
    """
    返回今天每种食物已经吃了多少份，格式 {food_id: 份数}。
    按食物名称匹配明细表当天的记录并累加servings，同一食物今天记录了多次
    （比如早上吃1个鸡蛋、晚上又吃1个）会自动加总，不是只取最后一次。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    name_to_id = {item["name"]: food_id for food_id, item in FOODS.items()}
    eaten = {food_id: 0.0 for food_id in FOODS}

    if not os.path.exists(CSV_PATH):
        return eaten

    with open(CSV_PATH, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("date") != today:
                continue
            food_id = name_to_id.get(row.get("food"))
            if food_id is None:
                continue
            try:
                eaten[food_id] += float(row["servings"])
            except (TypeError, ValueError, KeyError):
                print(f"⚠️  明细表里有一行份数读不了，已跳过: {row}")
                continue

    return eaten


def get_vegetable_days():
    """
    返回过去7天中，哪些日期吃过蔬菜（一个 set，元素是 'YYYY-MM-DD' 字符串）。
    这里必须读明细表(CSV_PATH)而不是每日汇总表——因为汇总表只存热量/蛋白/碳水
    三个数字，不记录"吃了什么"，要判断"今天有没有吃蔬菜"这种是非型问题，
    只能回到明细记录里按食物名称匹配。
    """
    veg_days = set()
    if not os.path.exists(CSV_PATH):
        return veg_days
    with open(CSV_PATH, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("food") == VEGETABLE_FOOD_NAME:
                veg_days.add(row["date"])
    return veg_days


def _render_bar_row(label, val, target, max_scale, width, fill_char):
    """
    渲染一行柱状图，并在目标值对应的位置画一条竖线(┊)作为目标线。
    刻度(max_scale)由调用方统一计算，保证同一张图里所有天用同一把尺子，
    且这把尺子会自动放大到能装下目标值和当周最大实际值中较大的那个，
    否则目标值超过当周最大摄入时，目标线会被画到图外面，看不到。
    """
    bar_len = round((val / max_scale) * width) if val > 0 else 0
    chars = [fill_char] * bar_len + [" "] * (width - bar_len)

    target_pos = round((target / max_scale) * width)
    if 0 <= target_pos < width:
        chars[target_pos] = "┊"  # 目标线标记，无论落在柱子内部还是外部都盖上这个符号

    bar_str = "".join(chars)
    return f"  {label} | {bar_str} {val:>6.1f}"


def print_weekly_bar_chart():
    """终端柱状图：过去7天的总热量、总蛋白质，各自独立刻度，含目标线；热量图每行末尾标注是否吃了蔬菜。"""
    dates, data = get_last_n_days_data(7)
    veg_days = get_vegetable_days()
    today_str = datetime.now().date().strftime("%Y-%m-%d")
    max_bar_width = 30

    print("=" * 70)

    # ---- 热量图 ----
    kcal_values = [data[d.strftime("%Y-%m-%d")]["kcal"] for d in dates]
    # 刻度取"当周最大实际值"和"目标值"中较大的一个，保证目标线一定在图内
    max_kcal_scale = max(max(kcal_values), DAILY_KCAL_TARGET, 1)

    print(f"\n🔥 总热量 (kcal)  目标: {DAILY_KCAL_TARGET}")
    for d in dates:
        key = d.strftime("%Y-%m-%d")
        val = data[key]["kcal"]
        label = d.strftime("%m-%d") + (" 今天" if key == today_str else "     ")
        veg_mark = "✓" if key in veg_days else " "
        print(_render_bar_row(label, val, DAILY_KCAL_TARGET, max_kcal_scale, max_bar_width, "█") + f"  {veg_mark}")

    # ---- 蛋白质图 ----
    protein_values = [data[d.strftime("%Y-%m-%d")]["protein"] for d in dates]
    max_protein_scale = max(max(protein_values), DAILY_PROTEIN_TARGET, 1)

    print(f"\n💪 总蛋白质 (g)  目标: {DAILY_PROTEIN_TARGET}")
    for d in dates:
        key = d.strftime("%Y-%m-%d")
        val = data[key]["protein"]
        label = d.strftime("%m-%d") + (" 今天" if key == today_str else "     ")
        print(_render_bar_row(label, val, DAILY_PROTEIN_TARGET, max_protein_scale, max_bar_width, "▓"))

    print("\n" + "=" * 70 + "\n")


def print_summary():
    total, records = get_today_total()
    print("\n===== 今日汇总 =====")
    if not records:
        print("今天还没有记录。")
    else:
        for r in records:
            print(f"  {r['time']}  {r['food']} x{r['servings']}份  "
                  f"({r['kcal']}kcal)")
        print("-" * 30)
        print(f"总计: {total['kcal']:.1f} kcal | "
              f"蛋白质 {total['protein']:.1f}g | "
              f"碳水 {total['carb']:.1f}g | "
              f"脂肪 {total['fat']:.1f}g")
    print(f"\n明细文件: {CSV_PATH}")
    print(f"每日汇总文件: {SUMMARY_CSV_PATH}")


def write_summary_csv():
    """
    把汇总CSV整份重写——这是它唯一的更新方式，不做增量合并。
    数据来源是 compute_all_daily_totals()，也就是明细表本身，
    所以这个文件里的每一行永远是明细表当前内容的忠实反映。

    重要：程序自己从不读取这个文件（get_last_n_days_data 已经改成直接读明细表），
    这个文件纯粹是给你自己打开看的导出报告。这意味着：
    - 就算你手动改坏了这个文件，也不会影响程序的任何计算或图表，
      下次程序一跑，这个文件会被完整覆盖重写，坏的内容自动消失。
    - 如果你手动改的是明细表(nutrition_log.csv)，改完后跑一次程序，
      这个汇总文件会自动同步成新的正确值，不需要你手动对齐两边。
    """
    all_totals = compute_all_daily_totals()

    with open(SUMMARY_CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_HEADER)
        writer.writeheader()
        for date_key in sorted(all_totals.keys()):
            t = all_totals[date_key]
            writer.writerow({
                "date": date_key,
                "total_kcal": round(t["kcal"], 1),
                "total_protein_g": round(t["protein"], 1),
                "total_carb_g": round(t["carb"], 1),
            })


def print_food_reference():
    """在交互开始前，一次性列出所有食物的热量/蛋白值，供用户对照。"""
    for item in FOODS.values():
        print(f"  {item['name']}（{item['unit']}）: {item['kcal']}kcal / {item['protein']}g蛋白")
    print()


def main():
    ensure_csv_exists()
    write_summary_csv()  # 先同步一次，保证图表反映的是明细表当前的真实内容
    print_weekly_bar_chart()
    print_food_reference()

    recorded_items = []

    for item in FOODS.values():
        while True:
            qty_str = input(f"{item['name']}（{item['unit']}/份）吃了几份？回车=没吃: ").strip()

            if qty_str == "":
                # 直接回车，视为没吃，跳到下一种食物
                break

            try:
                servings = float(qty_str)
            except ValueError:
                print("⚠️  请输入数字，或直接回车跳过。")
                continue

            if servings <= 0:
                print("⚠️  份数必须大于0，或直接回车跳过。")
                continue

            append_entry(item, servings)
            recorded_items.append(f"{item['name']} x{servings}份")
            break

    print()
    if not recorded_items:
        print("本次没有记录任何食物。")
    else:
        for line in recorded_items:
            print(f"✅ 已记录: {line}")

    print_summary()
    write_summary_csv()
    print_weekly_bar_chart()


if __name__ == "__main__":
    main()
