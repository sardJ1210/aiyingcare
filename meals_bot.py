# -*- coding: utf-8 -*-
"""
����: requests
��������:
  FEISHU_APP_ID / FEISHU_APP_SECRET
  BITABLE_APP_TOKEN / BITABLE_TABLE_ID
  WECHAT_WEBHOOK
  TIMEZONE=Asia/Shanghai
  MODE=remind|report
  MEAL_KIND=lunch|dinner|breakfast_next   # report ʱ����
  DATE_SHIFT_DAYS=0|1                     # �����İ��ã�report �ᰴ�ʹ��Զ����Ƶ���׼��
  FORM_URL=...                            # ������ű����ӣ��ű����Զ�ƴ�ӽ��죩
  DEADLINE_HHMM=09:30/15:00               # ������չʾ
  MENTION_MOBILES=13800000000,13900000000 # ��ѡ��@ �ֻ���
  LOCK_DATE=0/1                           # ��ѡ��1=�ѡ��ò����ڡ�����/����
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
MENTION = [s.strip() for s in os.getenv("MENTION_MOBILES","").split(",") if s.strip()]
LOCK_DATE = os.getenv("LOCK_DATE","0") == "1"

# �ֶ�
F_DATE, F_NAME, F_MEALS = "�ò�����", "����", "�ͱ�"
F_ADULT, F_CHILD = "���˷���", "��ͯ����"
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
    """��/��Ч/�������� 0���Ҳ��������� hi��"""
    try: x = int(v)
    except: x = 0
    if x < 0: x = 0
    return hi if x > hi else x

def normalize_meals(v):
    """��ѡͳһ�� {'lunch','dinner','breakfast_next'}"""
    if isinstance(v, list):
        names = set(v)
    elif isinstance(v, str):
        names = set([x.strip() for x in v.replace("��", ",").split(",") if x.strip()])
    else:
        names = set()
    out = set()
    for n in names:
        if n in ("���","lunch"): out.add("lunch")
        elif n in ("���","dinner"): out.add("dinner")
        elif n in ("�������","���","breakfast","breakfast_next"): out.add("breakfast_next")
    return out

def index_latest_per_meal(items):
    """
    ͬһ��+ͬһ��׼��+ͬһ�ʹ� ȡ���һ�Σ���ѡ�����ͼ�¼��
    �����{base, name, meal, adult, child, _ts}
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
        child = _clip_optional(f.get(F_CHILD, 0), CHILD_MAX)  # ��ͯ�ɲ������0��
        for mk in meals:
            key = (base, name, mk)
            if key not in latest or ts >= latest[key]["_ts"]:
                latest[key] = {"base": base, "name": name, "meal": mk,
                               "adult": adult, "child": child, "_ts": ts}
    return list(latest.values())

def sum_for(meal_kind, served_date, rows):
    """��/��;Ͳ���=��׼�գ�������;Ͳ���=��׼��+1��"""
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

def send_text(t, mobiles=None):
    send_wecom({"msgtype":"text","text":{"content":t,"mentioned_mobile_list":mobiles or []}})

def send_md(md):
    send_wecom({"msgtype":"markdown","markdown":{"content":md}})

def md_report(date_str, meal_kind, a, c):
    cn = {"lunch":"���","dinner":"���","breakfast_next":"���"}[meal_kind]
    return "\n".join([
        f"**{date_str} {cn} �òͻ���**",
        f"> ���ˣ�**{a}** �ݡ���ͯ��**{c}** �ݡ��ϼƣ�**{a+c}** ��",
        "\n���Զ����ͣ����ˡ�2����ͯ��2����ͯ�ɲ�����ͱ��ѡ��ÿ�͸��������һ��Ϊ׼��"
    ])

# --- �������������Զ�Ԥ��ò�����=���족����ѡ���ظ��ֶΣ� ---
def add_prefill_date(url: str, date_str: str, lock: bool=False) -> str:
    if not url:
        return url
    parts = urlsplit(url)
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    params["prefill_�ò�����"] = date_str
    if lock:
        params["hide_�ò�����"] = "1"
    new_query = urlencode(params, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))

def run_remind():
    d = dstr(SHIFT)  # ���죨�����趨��ƫ�ƣ�
    tip = f"����ֹ {DEADLINE}��" if DEADLINE else ""
    link = add_prefill_date(FORM_URL, d, lock=LOCK_DATE) if FORM_URL else ""
    msg = "\n".join([
        f"{d} �ò͵Ǽǿ�ʼ {tip}",
        "�ͱ�**�ɶ�ѡ**�����/���/������ͣ�����/��ͯ������Ӧ�õ�ÿ����ѡ�ĲʹΣ�**��ͯ�ɲ���**����",
        f"����ڣ�{link}" if link else "����ڼ�Ⱥ���档",
        "���򣺳��ˡ�2����ͯ��2���ɷ����ύ��**���ͻ������ǣ����������һ��Ϊ׼**��"
    ])
    send_text(msg)

def run_report():
    served = dstr(SHIFT)  # �Ͳ���
    base = dstr(SHIFT - 1) if MEAL_KIND=="breakfast_next" else served
    tkn = tenant_token()
    rows = index_latest_per_meal(list_by_base_date(base, tkn))
    a, c = sum_for(MEAL_KIND, served, rows)
    if MENTION:
        cn = {"lunch":"���","dinner":"���","breakfast_next":"���"}[MEAL_KIND]
        send_text(f"{served} {cn} ���ܣ����� {a}����ͯ {c}���ϼ� {a+c}��", MENTION)
    send_md(md_report(served, MEAL_KIND, a, c))

if __name__ == "__main__":
    assert APP_ID and APP_SECRET and APP_TOKEN and TABLE_ID and WEBHOOK
    if MODE=="remind": run_remind()
    else: run_report()
