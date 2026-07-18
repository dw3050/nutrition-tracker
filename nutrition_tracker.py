#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日饮食营养记录小程序
用法: python3 nutrition_tracker.py
数据保存在同目录下的 nutrition_log.csv 中，按日期自动区分"今日汇总"。
"""

import csv
import json
import os
import uuid
from datetime import datetime, timedelta

# 数据文件存放目录。
# 本地跑（不设置NUTRITION_DATA_DIR环境变量）时，跟以前一样存在脚本所在文件夹。
# 部署到Render这类平台时，需要把这个环境变量指向持久磁盘的挂载路径，
# 否则CSV文件会写到每次部署都会被清空的临时目录里，磁盘白挂了。
DATA_DIR = os.environ.get("NUTRITION_DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
os.makedirs(DATA_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# 食物库 —— 现在存在 foods.json 里，不再是写死在代码里的字典。
#
# 这是个真实的架构改动，不是小修小补：之前想加一种食物、改一个数值，
# 必须我来改Python代码。现在食物目录是数据，不是代码，网页上"管理食物"
# 那个功能可以直接读写这个文件，加菜、改营养值都不需要碰代码了。
#
# _DEFAULT_FOODS 是"种子数据"——只在 foods.json 还不存在的时候（比如你
# 第一次部署、或者本地第一次跑）用来初始化这个文件，之后 foods.json 才是
# 真正的数据来源，这份写死的字典不会再被读取。
#
# 数据可信度说明（保留作为历史记录，2026-07-03至2026-07-11期间陆续核实）：
# 1. 煎鸡蛋：用橄榄油煎、油量偏多，误差±20kcal，主要不确定性来自实际用油量。
# 3. 水煮虾：基于Bowen Basket包装数据换算，蛋白质密度低于天然虾、钠偏高，
#    大概率是"增强/腌渍虾"，脂肪含量未在包装上给出，是估算值。
# 4. 蛋白粉：⚠️ 从头到尾未验证——没有具体品牌/规格信息，是行业通用估算，
#    误差可能达到±50%甚至更高。
# 5. 冷冻蔬菜包：品牌已确认Birds Eye Steamfresh，已用真实产品数据修正。
# 6. 鸡胸肉：数据来源USDA生鸡胸肉每100g=114kcal/21.2g蛋白/2.6g脂肪，
#    按盒子上标的生重换算到1磅。
# 7/8. 香蕉、牛油果：为补钾加入，数据来源USDA标准中等大小份量。
# ---------------------------------------------------------------------------
FOODS_PATH = os.path.join(DATA_DIR, "foods.json")

_DEFAULT_FOODS = {
    "1": {"name": "煎鸡蛋",     "unit": "1个",  "kcal": 115, "protein": 6.3,  "carb": 0.4,  "fat": 9},
    "2": {"name": "无糖燕麦粥", "unit": "1份",  "kcal": 130, "protein": 5.5,  "carb": 21.8, "fat": 2.3},
    "3": {"name": "水煮虾",     "unit": "10只", "kcal": 91,  "protein": 17,   "carb": 0,    "fat": 0.8},
    "4": {"name": "蛋白粉",     "unit": "1勺",  "kcal": 120, "protein": 24,   "carb": 3,    "fat": 1},
    "5": {"name": "冷冻蔬菜包", "unit": "1袋",  "kcal": 100, "protein": 3.3,  "carb": 18.3, "fat": 1.7, "tracker": "vegetable"},
    "6": {"name": "鸡胸肉",     "unit": "1磅",  "kcal": 517, "protein": 96.2, "carb": 0,    "fat": 11.8},
    "7": {"name": "香蕉",       "unit": "1根",  "kcal": 105, "protein": 1.3,  "carb": 27,   "fat": 0.4},
    "8": {"name": "牛油果",     "unit": "1个",  "kcal": 240, "protein": 3,    "carb": 13,   "fat": 22},
    "9": {"name": "额外热量",   "unit": "1卡",  "kcal": 1,   "protein": 0,    "carb": 0,    "fat": 0},
    "10": {"name": "维生素补剂", "unit": "1份",  "kcal": 0,   "protein": 0,    "carb": 0,    "fat": 0, "tracker": "vitamin"},
    # 地瓜数据来源：USDA生地瓜每100g=86kcal/1.57g蛋白/20.12g碳水/0.05g脂肪(多个独立
    # 来源交叉验证一致)，按你说的220g生重换算。
    "11": {"name": "地瓜",       "unit": "220g", "kcal": 189, "protein": 3.5,  "carb": 44.3, "fat": 0.1},
}


def _load_foods_file():
    """
    读取foods.json。文件里存的不只是食物字典，还有一个next_id计数器。
    这个计数器只增不减，即使某个食物被删除了，它的编号也永远不会被
    分配给之后新加的食物——这是防止"删掉旧鸡肉、新建同名鸡肉"这种情况下
    新食物意外顶替旧食物历史身份的关键。

    兼容旧格式：如果读到的是没有next_id包装的老版本文件（纯食物字典），
    自动从当前最大编号推算一个初始计数器值，然后重存成新格式。
    """
    if not os.path.exists(FOODS_PATH):
        foods = dict(_DEFAULT_FOODS)
        next_id = max((int(k) for k in foods if k.isdigit()), default=0) + 1
        _save_foods_file(foods, next_id)
        return foods, next_id

    with open(FOODS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "next_id" in data and "foods" in data:
        return data["foods"], data["next_id"]

    # 老格式迁移：整个文件本身就是食物字典
    foods = data
    next_id = max((int(k) for k in foods if k.isdigit()), default=0) + 1
    _save_foods_file(foods, next_id)
    return foods, next_id


def _save_foods_file(foods_dict, next_id):
    with open(FOODS_PATH, "w", encoding="utf-8") as f:
        json.dump({"next_id": next_id, "foods": foods_dict}, f, ensure_ascii=False, indent=2)


FOODS, _NEXT_FOOD_ID = _load_foods_file()


def _save_foods():
    _save_foods_file(FOODS, _NEXT_FOOD_ID)


def _clear_other_holders_of_tracker(tracker_type, except_id=None):
    """
    确保同一个tracker类型(vegetable/vitamin)同时只有一个食物持有——
    如果不这么做，用户可能不小心把两种食物都标成"蔬菜"，那"今天吃了蔬菜没"
    这种判断就会变得含糊不清（该按哪个食物的记录算？）。
    新设置一个持有者之前，先把其他食物身上同类型的旧标记摘掉。
    """
    if not tracker_type:
        return
    for fid, item in FOODS.items():
        if fid != except_id and item.get("tracker") == tracker_type:
            item.pop("tracker", None)


def add_food(name, unit, kcal, protein, carb, fat, tracker=None, daily_target=0):
    """新增一种食物，用只增不减的计数器分配id，绝不会跟历史上曾经存在过、哪怕已被删除的食物撞号。"""
    global _NEXT_FOOD_ID
    new_id = str(_NEXT_FOOD_ID)
    _clear_other_holders_of_tracker(tracker, except_id=new_id)
    entry = {
        "name": name, "unit": unit, "kcal": kcal, "protein": protein, "carb": carb, "fat": fat,
        "daily_target": daily_target,
    }
    if tracker:
        entry["tracker"] = tracker
    FOODS[new_id] = entry
    _NEXT_FOOD_ID += 1
    _save_foods()
    return new_id


def update_food(food_id, name, unit, kcal, protein, carb, fat, tracker=None, daily_target=0):
    """
    修改某个食物的定义（名字/单位/营养值/特殊标记/每日目标份数），并把这个改动同步应用到
    这个食物已有的所有历史记录上——按你的要求，"编辑"代表的是同一个食物
    的定义在演进，不是变成了另一个东西，所以历史记录应该跟着新定义重算，
    而不是继续保留编辑前的旧快照。

    这跟"删除后新建同名食物"是两回事：那种情况新食物拿到的是全新id，
    跟旧食物的历史记录没有任何关联，不会互相影响——这是本函数不需要
    额外处理的部分，因为id不同，天然不会匹配到旧食物的历史记录。

    返回 (是否成功, 被同步更新的历史记录条数)。
    """
    if food_id not in FOODS:
        return False, 0
    _clear_other_holders_of_tracker(tracker, except_id=food_id)
    entry = {
        "name": name, "unit": unit, "kcal": kcal, "protein": protein, "carb": carb, "fat": fat,
        "daily_target": daily_target,
    }
    if tracker:
        entry["tracker"] = tracker
    FOODS[food_id] = entry
    _save_foods()
    updated_count = recompute_entries_for_food(food_id)
    return True, updated_count


def delete_food(food_id):
    """
    删除一个食物定义。它已有的历史记录不会被删除、不会被改动，
    继续保留删除前最后一次的营养快照——只是这个食物不再出现在
    "记录今天吃了什么"的清单里，也没法再通过管理食物界面编辑它了。
    它的id从此永久退休，不会被后续新增的食物复用（见add_food的计数器逻辑）。
    """
    if food_id not in FOODS:
        return False
    del FOODS[food_id]
    _save_foods()
    return True


def move_food(food_id, direction):
    """
    把某个食物在列表里往上或往下挪一位，direction是"up"或"down"。
    Python字典从3.7起会记住插入顺序，foods.json读写也是按这个顺序存的，
    所以"调整显示顺序"本质上就是"重新按新顺序往字典里插入一遍"——
    不需要额外的排序字段，字典本身的键顺序就是显示顺序。
    """
    global FOODS
    ids = list(FOODS.keys())
    if food_id not in ids:
        return False
    idx = ids.index(food_id)
    if direction == "up" and idx > 0:
        ids[idx - 1], ids[idx] = ids[idx], ids[idx - 1]
    elif direction == "down" and idx < len(ids) - 1:
        ids[idx + 1], ids[idx] = ids[idx], ids[idx + 1]
    else:
        return False  # 已经在最上面/最下面了，挪不动
    FOODS = {fid: FOODS[fid] for fid in ids}
    _save_foods()
    return True


def get_special_food_name(tracker_type):
    """
    查找带有特殊标记(tracker)的食物，返回它当前的名字。
    tracker_type 是 "vegetable" 或 "vitamin"。
    用标记字段而不是硬编码某个id，是因为"哪个食物承担这个特殊追踪角色"这件事
    应该由用户自己在"管理食物"里指定，不该是我写死在代码里的假设。

    向后兼容：老版本的数据里食物没有tracker这个字段(这个字段是后来才加的)，
    如果找不到任何食物带vegetable标记，退回到"id=5就是蔬菜"这个历史假设，
    不然老用户升级后蔬菜打卡功能会突然失效。vitamin没有这个历史包袱，
    找不到就是没有，不用编造一个默认值。
    """
    for food_id, item in FOODS.items():
        if item.get("tracker") == tracker_type:
            return item["name"]
    if tracker_type == "vegetable":
        return FOODS.get("5", {}).get("name", "")
    return ""


def get_vegetable_food_name():
    """向后兼容旧代码里的调用方式，内部转发给通用版本。"""
    return get_special_food_name("vegetable")


# ---------------------------------------------------------------------------
# 每日目标（热量/蛋白质/碳水/脂肪）—— 存在settings.json里，可以通过网页修改，
# 不再是写死在代码里的常量。热量/蛋白质种子值沿用之前算出来的1800/200；
# 碳水/脂肪没有算过科学依据的目标值，默认给0——前端的规则是目标值为0就不画
# 目标线，不会显示一条没有意义的假目标，等你自己设定了才会出现。
# ---------------------------------------------------------------------------
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
_DEFAULT_SETTINGS = {"target_kcal": 1800, "target_protein": 200, "target_carb": 0, "target_fat": 0}


def _load_settings():
    if os.path.exists(SETTINGS_PATH):
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            settings = json.load(f)
        # 兼容旧版本settings.json(只有target_kcal/target_protein，没有碳水/脂肪)：
        # 缺的字段直接补上默认值0，不用重新迁移整个文件。
        for key, default_val in _DEFAULT_SETTINGS.items():
            settings.setdefault(key, default_val)
        return settings
    _save_settings(_DEFAULT_SETTINGS)
    return dict(_DEFAULT_SETTINGS)


def _save_settings(settings_dict):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings_dict, f, ensure_ascii=False, indent=2)


_SETTINGS = _load_settings()


def get_targets():
    return (
        _SETTINGS["target_kcal"],
        _SETTINGS["target_protein"],
        _SETTINGS.get("target_carb", 0),
        _SETTINGS.get("target_fat", 0),
    )


def update_targets(target_kcal, target_protein, target_carb=0, target_fat=0):
    _SETTINGS["target_kcal"] = target_kcal
    _SETTINGS["target_protein"] = target_protein
    _SETTINGS["target_carb"] = target_carb
    _SETTINGS["target_fat"] = target_fat
    _save_settings(_SETTINGS)


# ---------------------------------------------------------------------------
# 用户账号 —— 现在存在users.json里，不再只依赖环境变量。
#
# 环境变量 NUTRITION_USERS（格式"用户名1:密码1,用户名2:密码2"）和
# NUTRITION_ADMIN（格式"用户名"，指定谁是管理员）只在users.json还不存在的
# 时候用来做"种子数据"——第一次启动会把环境变量里的账号导入进这个文件，
# 之后这个文件才是真正的数据来源，管理员通过网页新增/删除账号，都是在改
# 这个文件，不会去改环境变量（环境变量运行时改不了，也不该改）。
# ---------------------------------------------------------------------------
USERS_PATH = os.path.join(DATA_DIR, "users.json")


def _parse_users_env(raw):
    users = {}
    if not raw:
        return users
    for pair in raw.split(","):
        pair = pair.strip()
        if ":" not in pair:
            continue
        user, _, pw = pair.partition(":")
        users[user.strip()] = pw.strip()
    return users


def _load_users():
    if os.path.exists(USERS_PATH):
        with open(USERS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    seed = _parse_users_env(os.environ.get("NUTRITION_USERS", ""))
    admin_name = os.environ.get("NUTRITION_ADMIN", "").strip()
    users = {
        user: {"password": pw, "is_admin": (user == admin_name)}
        for user, pw in seed.items()
    }
    _save_users(users)
    return users


def _save_users(users_dict):
    with open(USERS_PATH, "w", encoding="utf-8") as f:
        json.dump(users_dict, f, ensure_ascii=False, indent=2)


USERS = _load_users()


def check_login(username, password):
    info = USERS.get(username)
    if not info or not password:
        return False
    return info.get("password") == password


def is_admin_user(username):
    return USERS.get(username, {}).get("is_admin", False)


def add_user(username, password, is_admin=False):
    username = username.strip()
    if not username or not password:
        return False, "用户名或密码不能为空"
    if username in USERS:
        return False, "这个用户名已经存在"
    USERS[username] = {"password": password, "is_admin": is_admin}
    _save_users(USERS)
    return True, None


def delete_user(username):
    if username not in USERS:
        return False, "用户不存在"
    if USERS[username].get("is_admin"):
        other_admins = [u for u, info in USERS.items() if info.get("is_admin") and u != username]
        if not other_admins:
            return False, "不能删除最后一个管理员账号，会导致没人能管理用户"
    del USERS[username]
    _save_users(USERS)
    return True, None


CSV_PATH = os.path.join(DATA_DIR, "nutrition_log.csv")
# 加了 id 和 food_id 两列——之前的版本没有任何方式能精确定位"某一条具体记录"，
# 只能整天清空重来。要支持"撤回上一条"和"编辑某一天的某一条记录"，
# 每条记录必须有唯一ID，而且要记住它对应哪个食物(food_id)，
# 不能只存食物名字——名字以后可能会改，id不会变。
#
# ⚠️ 迁移说明：如果你在Render上已经跑了一段时间、攒了一些用旧格式(没有id/food_id
# 这两列)记录的数据，这些旧记录在读取时id/food_id会是空——旧记录仍然会被正确计入
# 每天的热量/蛋白质汇总(那部分逻辑不依赖这两个新列)，但没法通过"撤回"或"编辑"
# 功能精确操作，因为程序找不到它们的id。这不是bug，是新旧数据结构交接的必然结果。
CSV_HEADER = ["id", "date", "time", "food", "food_id", "servings", "kcal", "protein_g", "carb_g", "fat_g"]

# 第二份CSV：每天一行的汇总（只含热量/蛋白质/碳水，按你的要求不含脂肪）
SUMMARY_CSV_PATH = os.path.join(DATA_DIR, "nutrition_daily_summary.csv")
SUMMARY_HEADER = ["date", "total_kcal", "total_protein_g", "total_carb_g"]


def ensure_csv_exists():
    """
    确保明细表存在，且表头是正确的。这个函数现在做两件事，不只是"文件不存在就创建"：

    1. 文件完全不存在 → 创建一个带正确表头的新文件，并打印一条明显的日志。
       之前这个动作是静默的，你完全看不出"程序发现数据没了、自己重建了一份"
       这件事发生过——现在会主动告诉你。

    2. 文件存在，但第一行不是正确的表头（说明被污染了，比如某条真实数据
       被误当成表头写了进去）→ 不再对着一个坏文件默默地逐行跳过、印一堆
       看不出问题本质的小警告。而是：
       - 打印一条非常显眼、独立成块的严重错误日志，说清楚"表头污染了"这件事
       - 把损坏的文件原样备份一份（不删除任何数据，留档方便你事后查）
       - 把被误当表头的那一行当成一条正常数据，重建一份表头正确的新文件

    这两件事对应的是你提的两个需求：数据格式跟预期不一致时要报出来（不是
    默默跳过）、数据在运行期间消失了程序自己也要能意识到并且有动静。
    """
    if not os.path.exists(CSV_PATH):
        print(f"ℹ️  明细表({CSV_PATH})不存在，正在创建一个带正确表头的新文件。"
              f"如果你预期这里应该已经有数据，说明文件是刚刚才消失的，这不是正常情况，值得你去确认一下原因。")
        with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER)
        return

    with open(CSV_PATH, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        try:
            first_row = next(reader)
        except StopIteration:
            first_row = None  # 文件存在但完全是空的（连表头都没有）

    if first_row == CSV_HEADER:
        return  # 表头正常，什么都不用做

    print("=" * 70)
    print("🚨 严重警告：明细表的表头被污染了，不是正常状态！")
    print(f"   期望的表头: {CSV_HEADER}")
    print(f"   实际读到的第一行: {first_row}")
    print("   这通常发生在：程序运行期间文件被外部删除、但程序没有重启，")
    print("   之后有新数据被追加写入时，本该是表头的位置被一条真实数据占据了。")
    print("   正在自动修复：备份损坏文件 → 用正确表头重建 → 把误当表头的那行当普通数据保留。")
    print("=" * 70)

    backup_path = f"{CSV_PATH}.corrupted-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    with open(CSV_PATH, "r", newline="", encoding="utf-8-sig") as f:
        original_content = f.read()
    with open(backup_path, "w", newline="", encoding="utf-8-sig") as f:
        f.write(original_content)
    print(f"   已备份损坏文件到: {backup_path}（不会自动删除，你可以手动检查里面的内容）")

    with open(CSV_PATH, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        all_rows = list(reader)  # 第一行(被误当表头的数据)也在里面，会被当普通数据处理

    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        kept = 0
        for row in all_rows:
            if len(row) == len(CSV_HEADER):
                writer.writerow(row)
                kept += 1
            else:
                print(f"   ⚠️  有一行列数对不上（{len(row)}列，期望{len(CSV_HEADER)}列），已丢弃: {row}")

    print(f"   修复完成，重建了正确表头，保留了 {kept} 行数据。")
    print("=" * 70)


def _resolve_today(today_override=None):
    """
    统一处理"今天是哪天"这个问题。
    优先使用调用方传入的日期字符串(浏览器用JS算出的、用户自己所在时区的本地日期)，
    没传的时候才退回服务器自己的系统时间——这个退回路径主要是给终端版CLI用的，
    终端版直接跑在你自己电脑上，系统时间本来就是你的本地时间，没有这个时区错位问题。
    网页版任何涉及"今天"判断的请求，都应该带上这个参数，不能让服务器自己猜。
    """
    if today_override:
        try:
            return datetime.strptime(today_override, "%Y-%m-%d").date()
        except ValueError:
            pass  # 格式不对就当没传，退回服务器时间，不让一个格式错误直接搞崩整个请求
    return datetime.now().date()


def append_entry(food_id, food_item, servings, client_datetime=None):
    ensure_csv_exists()  # 每次写入前都检查一遍，不能只信任"启动时已经检查过"——
    # 如果文件在程序运行期间被外部删除（比如你去Shell里手动删了），
    # 下次追加写入会创建一个没有表头的文件，后面所有读取都会被污染。
    #
    # 记录用的日期/时间优先用client_datetime(浏览器传来的、用户本地时区的当前时刻)，
    # 不用服务器自己的datetime.now()——服务器在Render上跑的是UTC，直接用会导致
    # 晚上记录的东西被打上"明天"的日期标签，跟你本地实际感受的日期对不上。
    now = client_datetime if client_datetime else datetime.now()
    entry_id = uuid.uuid4().hex[:10]
    with open(CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            entry_id,
            now.strftime("%Y-%m-%d"),
            now.strftime("%H:%M:%S"),
            food_item["name"],
            food_id,
            servings,
            round(food_item["kcal"] * servings, 1),
            round(food_item["protein"] * servings, 1),
            round(food_item["carb"] * servings, 1),
            round(food_item["fat"] * servings, 1),
        ])
    return entry_id


def clear_today_entries(today_override=None):
    """
    删除明细表(CSV_PATH)里"今天"的所有行，历史数据不受影响。
    用于误输入后的一键重来，不是撤销单条记录——是把今天清零重新开始。
    做法是把不是今天的行原样保留、重写整个文件，而不是"追加一条反向抵消记录"，
    这样CSV里不会留下垃圾数据，今天这一天在文件里就跟没记录过一样干净。
    """
    today = _resolve_today(today_override).strftime("%Y-%m-%d")

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


def undo_last_entry(today_override=None):
    """
    撤销"今天"最后添加的一条记录。
    做法：读出今天的所有行，按文件里的顺序（也就是记录的时间顺序，因为
    append_entry永远是往文件末尾追加）取最后一条，删掉它，其余原样保留。
    返回被删除的那条记录（方便调用方告诉用户"撤销了什么"），
    如果今天没有任何记录，返回None。
    """
    today = _resolve_today(today_override).strftime("%Y-%m-%d")

    if not os.path.exists(CSV_PATH):
        return None

    all_rows = []
    with open(CSV_PATH, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            all_rows.append(row)

    # 找到"今天"的行里，最后一条的位置
    today_indices = [i for i, row in enumerate(all_rows) if row.get("date") == today]
    if not today_indices:
        return None

    last_idx = today_indices[-1]
    removed = all_rows.pop(last_idx)

    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)

    return removed


def get_entries_for_date(date_str):
    """
    返回某一天的所有明细记录，按时间顺序，每条带上id方便前端定位删除/编辑。
    用于"历史数据编辑"功能——用户选一天，看到那天具体吃了什么，可以单条删除或改份数。
    """
    entries = []
    ensure_csv_exists()
    if not os.path.exists(CSV_PATH):
        return entries
    with open(CSV_PATH, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("date") == date_str:
                entries.append(row)
    return entries


def delete_entry_by_id(entry_id):
    """按id删除明细表里的某一条具体记录，其余不受影响。返回是否真的删到了。"""
    if not os.path.exists(CSV_PATH):
        return False

    all_rows = []
    found = False
    with open(CSV_PATH, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("id") == entry_id:
                found = True
                continue
            all_rows.append(row)

    if not found:
        return False

    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)

    return True


def recompute_entries_for_food(food_id):
    """
    用food_id当前的定义，重新计算明细表里所有属于这个食物的历史记录。
    改的字段包括食物名字（如果食物改名了，历史记录里显示的名字也同步更新，
    因为这仍然是"同一个食物"，只是名字变了，不应该新旧记录显示两个名字）
    和营养数值。份数(servings)本身不变——只重算"份数 × 单份营养值"这个结果。
    返回被更新的记录条数，方便调用方告诉用户"同步改了几条"。
    """
    if food_id not in FOODS:
        return 0
    food_item = FOODS[food_id]

    if not os.path.exists(CSV_PATH):
        return 0

    all_rows = []
    updated_count = 0
    with open(CSV_PATH, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("food_id") == food_id:
                try:
                    servings = float(row["servings"])
                except (TypeError, ValueError, KeyError):
                    all_rows.append(row)
                    continue
                row["food"] = food_item["name"]
                row["kcal"] = round(food_item["kcal"] * servings, 1)
                row["protein_g"] = round(food_item["protein"] * servings, 1)
                row["carb_g"] = round(food_item["carb"] * servings, 1)
                row["fat_g"] = round(food_item["fat"] * servings, 1)
                updated_count += 1
            all_rows.append(row)

    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)

    return updated_count


def update_entry_servings(entry_id, new_servings):
    """
    修改某一条具体记录的份数，并按food_id重新查表算出对应的热量/蛋白/碳水/脂肪。
    这就是为什么明细表要存food_id而不只是食物名字——改份数需要知道
    "这条记录对应的是哪个食物"，才能按新份数重新算出正确的营养值。
    如果这条记录是旧数据、没有food_id（迁移前的历史记录），没法安全重算，
    直接返回失败，不做任何猜测性的处理。
    """
    if not os.path.exists(CSV_PATH):
        return False

    all_rows = []
    target_row = None
    with open(CSV_PATH, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("id") == entry_id:
                target_row = row
            all_rows.append(row)

    if target_row is None:
        return False

    food_id = target_row.get("food_id")
    if not food_id or food_id not in FOODS:
        return False  # 旧数据没有food_id，或者food_id对应的食物已经不存在了，不做猜测性修改

    food_item = FOODS[food_id]
    target_row["servings"] = new_servings
    target_row["kcal"] = round(food_item["kcal"] * new_servings, 1)
    target_row["protein_g"] = round(food_item["protein"] * new_servings, 1)
    target_row["carb_g"] = round(food_item["carb"] * new_servings, 1)
    target_row["fat_g"] = round(food_item["fat"] * new_servings, 1)

    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)

    return True


def get_today_total(today_override=None):
    today = _resolve_today(today_override).strftime("%Y-%m-%d")
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
    ensure_csv_exists()  # 每次读取前先检查表头完整性，不只是写入前才检查——
    # 这样哪怕用户只是打开页面看图表、没有点任何写入类按钮，也能第一时间
    # 发现并修复文件被外部删除/损坏的情况，而不是要等到下次写入才触发检查。
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
                fat = float(row["fat_g"])
            except (TypeError, ValueError, KeyError):
                print(f"⚠️  明细表里有一行数据读不了，已跳过: {row}")
                continue

            if date_key not in totals_by_date:
                totals_by_date[date_key] = {"kcal": 0.0, "protein": 0.0, "carb": 0.0, "fat": 0.0}
            totals_by_date[date_key]["kcal"] += kcal
            totals_by_date[date_key]["protein"] += protein
            totals_by_date[date_key]["carb"] += carb
            totals_by_date[date_key]["fat"] += fat

    return totals_by_date


def get_last_n_days_data(n=7, today_override=None):
    """
    返回过去n天（含今天）的日期列表(旧→新)，以及对应的 {kcal, protein, carb, fat} 数据。
    直接调用 compute_all_daily_totals() 现算，不再读 SUMMARY_CSV_PATH。
    这样无论你手动怎么改明细表，图表永远反映明细表的真实内容，
    汇总CSV文件的状态完全不影响程序自己的计算结果——它只是个单向导出的报告，
    不再是程序信任的数据来源。
    """
    today = _resolve_today(today_override)
    dates = [today - timedelta(days=i) for i in range(n - 1, -1, -1)]  # n-1天前 -> 今天
    all_totals = compute_all_daily_totals()

    data = {}
    for d in dates:
        key = d.strftime("%Y-%m-%d")
        day_total = all_totals.get(key, {"kcal": 0.0, "protein": 0.0, "carb": 0.0, "fat": 0.0})
        data[key] = {
            "kcal": day_total["kcal"],
            "protein": day_total["protein"],
            "carb": day_total.get("carb", 0.0),
            "fat": day_total.get("fat", 0.0),
        }

    return dates, data


def get_today_servings_by_food(today_override=None):
    """
    返回今天每种食物已经吃了多少份，格式 {food_id: 份数}。
    按食物名称匹配明细表当天的记录并累加servings，同一食物今天记录了多次
    （比如早上吃1个鸡蛋、晚上又吃1个）会自动加总，不是只取最后一次。
    """
    today = _resolve_today(today_override).strftime("%Y-%m-%d")
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


def get_tracked_days(tracker_type):
    """
    返回过去所有日期中，哪些天记录过带指定特殊标记(vegetable/vitamin)的食物。
    这是get_vegetable_days的泛化版本——蔬菜打卡和维生素打卡用的是同一套逻辑，
    只是查的标记类型不同，没必要为每种特殊追踪都单独写一个函数。
    """
    tracked_days = set()
    food_name = get_special_food_name(tracker_type)
    if not food_name:
        return tracked_days
    if not os.path.exists(CSV_PATH):
        return tracked_days
    with open(CSV_PATH, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("food") == food_name:
                tracked_days.add(row["date"])
    return tracked_days


def get_vegetable_days():
    """向后兼容旧代码里的调用方式，内部转发给通用版本。"""
    return get_tracked_days("vegetable")


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
    target_kcal, target_protein, _, _ = get_targets()  # CLI版终端图表目前只画热量/蛋白质，碳水脂肪目标先不用

    print("=" * 70)

    # ---- 热量图 ----
    kcal_values = [data[d.strftime("%Y-%m-%d")]["kcal"] for d in dates]
    # 刻度取"当周最大实际值"和"目标值"中较大的一个，保证目标线一定在图内
    max_kcal_scale = max(max(kcal_values), target_kcal, 1)

    print(f"\n🔥 总热量 (kcal)  目标: {target_kcal}")
    for d in dates:
        key = d.strftime("%Y-%m-%d")
        val = data[key]["kcal"]
        label = d.strftime("%m-%d") + (" 今天" if key == today_str else "     ")
        veg_mark = "✓" if key in veg_days else " "
        print(_render_bar_row(label, val, target_kcal, max_kcal_scale, max_bar_width, "█") + f"  {veg_mark}")

    # ---- 蛋白质图 ----
    protein_values = [data[d.strftime("%Y-%m-%d")]["protein"] for d in dates]
    max_protein_scale = max(max(protein_values), target_protein, 1)

    print(f"\n💪 总蛋白质 (g)  目标: {target_protein}")
    for d in dates:
        key = d.strftime("%Y-%m-%d")
        val = data[key]["protein"]
        label = d.strftime("%m-%d") + (" 今天" if key == today_str else "     ")
        print(_render_bar_row(label, val, target_protein, max_protein_scale, max_bar_width, "▓"))

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

    for food_id, item in FOODS.items():
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

            append_entry(food_id, item, servings)
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
