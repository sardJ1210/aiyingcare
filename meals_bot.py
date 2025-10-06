# -*- coding: utf-8 -*-
"""
依赖: requests
环境变量(Secrets 注入)：
  FEISHU_APP_ID / FEISHU_APP_SECRET
  BITABLE_APP_TOKEN / BITABLE_TABLE_ID
  WECHAT_WEBHOOK
  TIMEZONE=Asia/Shanghai
  MODE=remind|report
  MEAL_KIND=lunch|dinner|breakfast_next   # report 时必填
  DATE_SHIFT_DAYS=0|1                     # 提醒文案用；report 会按餐次自动回推到基准日
  FORM_URL=...                            # 提醒里放表单链接（脚本会自动拼接今天）
  DEADLINE_HHMM=09:30/15:00               # 提醒里展示
  MENTION_USERIDS=mr.Yu,zhangsan          # 只用 userID 方式 @（多个英文逗号分隔）
  LOCK_DATE=0/1                           # 1=把“用餐日期”隐藏/锁定
"""
import os, requests, datetime
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

APP_ID, APP_SECRET = os.getenv("FEISHU_APP_ID"), os.getenv("FEISHU_APP_SECRET")
APP_TOKEN, TABLE_ID = os.getenv("BITABLE_APP_TOKEN"), os.getenv("BITABLE_TABLE_ID")
WEBHOOK = os.getenv("WECHAT_WEBHOOK")
TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Shanghai"))

MODE = os.getenv("MODE", "report")
MEAL_KIND = os.getenv("MEAL_KIND", "lunch")  # lunch/dinner/breakfast_next
SHIFT = int(os.getenv("DATE_SHIFT_DAYS", "0"))
FORM_URL = os.getenv("FORM_URL", "")
DEADLINE = os.getenv("DEADLINE_HHMM", "")
MENTION_USERIDS = [s.strip() for s in os.getenv("MENTION_USERIDS","").split(",") if s.strip()]
LOCK_DATE = os.getenv("LOCK_DATE","0") == "1"

# 字段
F_DATE, F_NAME, F_MEALS = "用餐日期", "姓名", "餐别"
F_ADULT, F_CHILD = "成人份数", "儿童份数"
ADULT_MAX, CHILD_MAX = 2, 2

def dstr(days=0):
    return (datetime.datetime.now(TZ) + datetime.timedelta(days=days)).date().strftime("%Y-%m-%d")

def tenant_token():
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    r = requests.post(url, json={"app_id": APP_ID, "app_secret": APP_SECRET}, timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(data)
    return data["tenant_access_token"]

def list_by_base_date(base_date, tkn):
    base = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records"
    headers = {"Authorization": f"Bearer {tkn}"}
    filt = f'CurrentValue.[{F_DATE}] = "{base_date}"'
    items, page_token = [], None
    while True:
        params = {"page_size": 500, "filter": filt}
        if page_token: params["page_token"] = page_token
        resp = requests.get(base, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0: raise RuntimeError(data)
        items += data.get("data",{}).get("items",[])
        page_token = data.get("data",{}).get("page_token")
        if not data.get("data",{}).get("has_more"): break
    return items

def _ts(it): 
    return int(it.get("last_modified_time") or it.get("updated_time") or it.get("created_time") or 0)

def _clip_optional(v, hi):
    """空/无效/负数都当 0，且不超过上限 hi。"""
    try: x = int(v)
    except: x = 0
    if x < 0: x = 0
    return hi if x > hi else x

def normalize_meals(v):
    """多选统一成 {'lunch','dinner','breakfast_next'}"""
    if isinstance(v, list):
        names = set(v)
    elif isinstance(v, str):
        names = set([x.strip() for x in v.replace("，", ",").split(",") if x.strip()])
    else:
        names = set()
    out = set()
    for n in names:
        if n in ("午餐","lunch"): out.add("lunch")
        elif n in ("晚餐","dinner"): out.add("dinner")
        elif n in ("次日早餐","早餐","breakfast","breakfast_next"): out.add("breakfast_next")
    return out

def index_latest_per_meal(items):
    """
    同一人+同一基准日+同一餐次 取最后一次；多选拆成逐餐记录。
    输出：{base, name, meal, adult, child, _ts}
    """
    latest = {}
    for it in items:
        f = it.get("fields", {})
        base = f.get(F_DATE)
        name = (f.get(F_NAME) or "").strip()
        if not (base and name): continue
        ts = _ts(it)
        meals = normalize_meals(f.get(F_MEALS))
        adult = _clip_optional(f.get(F_ADULT, 0), ADULT_MAX)
        child = _clip_optional(f.get(F_CHILD, 0), CHILD_MAX)  # 儿童可不填→按0计
        for mk in meals:
            key = (base, name, mk)
            if key not in latest or ts >= latest[key]["_ts"]:
                latest[key] = {"base": base, "name": name, "meal": mk,
                               "adult": adult, "child": child, "_ts": ts}
    return list(latest.values())

def sum_for(meal_kind, served_date, rows):
    """午/晚餐就餐日=基准日；次日早餐就餐日=基准日+1。"""
    a = c = 0
    for r in rows:
        if r["meal"] != meal_kind: 
            continue
        base = r["base"]
        if meal_kind == "breakfast_next":
            if (datetime.date.fromisoformat(base) + datetime.timedelta(days=1)).strftime("%Y-%m-%d") != served_date:
                continue
        else:
            if base != served_date:
                continue
        a += r["adult"]; c += r["child"]
    return a, c

def send_wecom(payload):
    r = requests.post(WEBHOOK, json=payload, timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("errcode",0)!=0: raise RuntimeError(data)

def send_text(t, userids=None):
    payload = {"msgtype":"text","text":{"content":t}}
    if userids:
        payload["text"]["mentioned_list"] = userids  # 企业微信 userID 列表
    send_wecom(payload)

def send_md(md):
    send_wecom({"msgtype":"markdown","markdown":{"content":md}})

def md_report(date_str, meal_kind, a, c):
    cn = {"lunch":"午餐","dinner":"晚餐","breakfast_next":"早餐"}[meal_kind]
    return "\n".join([
        f"**{date_str} {cn} 用餐汇总**",
        f"> 成人：**{a}** 份　儿童：**{c}** 份　合计：**{a+c}** 份",
        "\n（自动发送｜成人≤2、儿童≤2〔儿童可不填〕｜餐别多选；每餐各自以最后一次为准）"
    ])

# --- 在提醒链接里自动预填“用餐日期=今天”（可选隐藏该字段） ---
def add_prefill_date(url: str, date_str: str, lock: bool=False) -> str:
    if not url:
        return url
    parts = urlsplit(url)
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    params["prefill_用餐日期"] = date_str
    if lock:
        params["hide_用餐日期"] = "1"
    new_query = urlencode(params, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))

def run_remind():
    d = dstr(SHIFT)  # 今天（或你设定的偏移）
    tip = f"（截止 {DEADLINE}）" if DEADLINE else ""
    link = add_prefill_date(FORM_URL, d, lock=LOCK_DATE) if FORM_URL else ""
    msg = "\n".join([
        f"{d} 用餐登记开始 {tip}",
        "截止登记时间：午餐：09:30、晚餐/次日早餐：15:00；（可多选）",
        f"员工餐订餐链接➡️：{link}" if link else "表单入口见群公告。",
        "福利：可额外点选家人餐（堂食/外带均可）：成人≤2、儿童≤2 用餐杜绝浪费。"
    ])
    # 若想在提醒里也 @ 厨师，可改为：send_text(msg, userids=MENTION_USERIDS)
    send_text(msg)

def run_report():
    served = dstr(SHIFT)  # 就餐日
    base = dstr(SHIFT - 1) if MEAL_KIND=="breakfast_next" else served
    tkn = tenant_token()
    rows = index_latest_per_meal(list_by_base_date(base, tkn))
    a, c = sum_for(MEAL_KIND, served, rows)
    if MENTION_USERIDS:
        cn = {"lunch":"午餐","dinner":"晚餐","breakfast_next":"早餐"}[MEAL_KIND]
        send_text(f"{served} {cn} 汇总：成人 {a}，儿童 {c}，合计 {a+c}。", userids=MENTION_USERIDS)
    send_md(md_report(served, MEAL_KIND, a, c))

if __name__ == "__main__":
    # 提醒：只需要企业微信 Webhook（和可选 FORM_URL）
    if MODE == "remind":
        assert WEBHOOK, "WECHAT_WEBHOOK missing"
        run_remind()
    else:
        # 汇总：需要飞书凭证 & 表信息
        assert APP_ID and APP_SECRET and APP_TOKEN and TABLE_ID and WEBHOOK, \
            "Missing Feishu credentials (FEISHU_APP_ID/SECRET, BITABLE_APP_TOKEN/TABLE_ID, WECHAT_WEBHOOK)"
        run_report()
