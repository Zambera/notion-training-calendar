
import os
import json
import hashlib
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ==========================================
# 配置
# ==========================================

TOKEN = os.environ["NOTION_TOKEN"]
DATABASE_ID = os.environ["NOTION_DATABASE_ID"]

NOTION_VERSION = "2025-09-03"
BASE_URL = "https://api.notion.com/v1"

DATE_PROPERTY = "日期"
ACTION_PROPERTIES = [f"动作{i}" for i in range(1, 11)]

OUTPUT = Path("public/training.ics")

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {TOKEN}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json"
})


# ==========================================
# Notion API
# ==========================================

def api(method, path, **kwargs):
    response = session.request(
        method,
        BASE_URL + path,
        timeout=30,
        **kwargs
    )
    response.raise_for_status()
    return response.json()


def get_data_source_id():
    data = api("GET", f"/databases/{DATABASE_ID}")

    sources = data.get("data_sources", [])

    if not sources:
        raise RuntimeError("没有找到数据库数据源")

    return sources[0]["id"]


def get_all_pages(data_source_id):
    pages = []
    cursor = None

    while True:
        payload = {"page_size": 100}

        if cursor:
            payload["start_cursor"] = cursor

        data = api(
            "POST",
            f"/data_sources/{data_source_id}/query",
            json=payload
        )

        pages.extend(data.get("results", []))

        if not data.get("has_more"):
            break

        cursor = data["next_cursor"]

    return pages


# ==========================================
# 提取 Notion 属性
# ==========================================

def get_rich_text(prop):
    if not prop:
        return ""

    items = prop.get("rich_text", [])
    return "".join(x.get("plain_text", "") for x in items)


def get_title(properties):
    for prop in properties.values():
        if prop.get("type") == "title":
            return "".join(
                x.get("plain_text", "")
                for x in prop.get("title", [])
            )

    return "训练计划"


def get_actions(properties):
    actions = []

    for name in ACTION_PROPERTIES:
        prop = properties.get(name, {})
        value = get_rich_text(prop)

        if value.strip():
            actions.append(value.strip())

    return actions


# ==========================================
# iCalendar 格式处理
# ==========================================

def escape_ical(value):
    value = str(value)
    value = value.replace("\\", "\\\\")
    value = value.replace("\r\n", "\n")
    value = value.replace("\r", "\n")
    value = value.replace("\n", "\\n")
    value = value.replace(",", "\\,")
    value = value.replace(";", "\\;")
    return value


def fold_line(line):
    """按 UTF-8 字节长度折叠 iCalendar 行。"""
    result = []
    current = ""
    current_bytes = 0

    for char in line:
        size = len(char.encode("utf-8"))

        if current_bytes + size > 73:
            result.append(current)
            current = " " + char
            current_bytes = 1 + size
        else:
            current += char
            current_bytes += size

    result.append(current)
    return "\r\n".join(result)


def make_event(page):
    properties = page.get("properties", {})

    title = get_title(properties)

    date_prop = properties.get(DATE_PROPERTY, {})
    date_data = date_prop.get("date")

    if not date_data or not date_data.get("start"):
        return None

    start = date_data["start"]

    # 当前训练计划使用全天日期
    if "T" in start:
        start_date = datetime.fromisoformat(
            start.replace("Z", "+00:00")
        ).date()
    else:
        start_date = datetime.strptime(
            start[:10], "%Y-%m-%d"
        ).date()

    # iCalendar 全天事件结束日期为不包含的下一天
    end_date = start_date + timedelta(days=1)

    actions = get_actions(properties)

    description = "\n".join(
        f"{i}. {action}"
        for i, action in enumerate(actions, 1)
    )

    if not description:
        description = "暂无训练动作"

    # 用 Notion Page ID 保证事件 UID 稳定
    uid = page["id"] + "@notion-training-calendar"

    now = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{now}",
        f"DTSTART;VALUE=DATE:{start_date.strftime('%Y%m%d')}",
        f"DTEND;VALUE=DATE:{end_date.strftime('%Y%m%d')}",
        f"SUMMARY:{escape_ical(title)}",
        f"DESCRIPTION:{escape_ical(description)}",
        "STATUS:CONFIRMED",
        "TRANSP:TRANSPARENT",
        "END:VEVENT"
    ]

    return lines


# ==========================================
# 生成 ICS 文件
# ==========================================

def main():
    print("正在连接 Notion...")

    data_source_id = get_data_source_id()

    print("正在读取训练记录...")

    pages = get_all_pages(data_source_id)

    events = []
    skipped = 0

    for page in pages:
        event = make_event(page)

        if event:
            events.extend(event)
        else:
            skipped += 1

    now = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    calendar_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Notion Training Calendar//ZH",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Notion Training",
        "X-WR-TIMEZONE:Asia/Shanghai",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        f"LAST-MODIFIED:{now}",
    ]

    calendar_lines.extend(events)
    calendar_lines.append("END:VCALENDAR")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    content = "\r\n".join(
        fold_line(line) for line in calendar_lines
    ) + "\r\n"

    OUTPUT.write_text(content, encoding="utf-8")

    print("================================")
    print(f"训练记录：{len(pages)}")
    print(f"生成事件：{len(events) // 9}")
    print(f"跳过记录：{skipped}")
    print(f"输出文件：{OUTPUT}")
    print("日历生成成功！")


if __name__ == "__main__":
    main()
