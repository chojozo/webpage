from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "afl history"
DEFAULT_OUTPUT = ROOT / "docs" / "afl" / "index.html"
DEFAULT_NOTION_DATABASE_ID = "382b89a3c13380a68912fcb8b4b74e3a"
NOTION_API_VERSION = "2022-06-28"


@dataclass(frozen=True)
class HistoryItem:
    raw_date: str
    title: str
    description: str
    sort_key: tuple[int, int, int]
    score: int


@dataclass(frozen=True)
class Milestone:
    raw_date: str
    title: str
    summary: str
    sort_key: tuple[int, int, int]


KEYWORDS = {
    "첫": 8,
    "매출": 10,
    "재구매": 8,
    "TFT": 6,
    "재시작": 8,
    "핵심": 5,
    "3D SLAM": 9,
    "기술": 4,
    "로드맵": 10,
    "V2.0": 9,
    "PROJECT KICK-OFF": 10,
    "KICK-OFF": 10,
    "PoC": 9,
    "POC": 9,
    "마스터플랜": 10,
    "자체 모델": 8,
    "조립": 8,
    "상용화": 9,
    "벤치마킹": 5,
    "마켓 리서치": 5,
    "디자인리뷰": 6,
}


PREFERRED_TITLES = [
    "포크리프트 AGV의 연구개발 필요성",
    "포크리프트 AGV Feasibility 개발",
    "개발품의 첫 매출 PJT",
    "제조를 고려한 포크리프트 AGV 워크샵",
    "AFL 3D 기술개발의 핵심 요소 정의",
    "3D SLAM 기술 이식 및 개발 스케줄 수립",
    "AFL 실제 차량과 통합 Test를 통한 SW Debugging 지속",
    "AFL 프로덕트 로드맵 수립(V1.5, V1.8, V2.0, V3.0)",
    "AFL V2.0 추진계획 수립",
    "AFL V2.0 PROJECT KICK-OFF",
    "AFL V2.0 마스터플랜 수립",
    "AFL V2.0 자체 모델 조립",
]


FALLBACK_SUMMARIES = {
    "포크리프트 AGV의 연구개발 필요성": "창고 적재 AGV 활용 기대",
    "포크리프트 AGV Feasibility 개발": "예일 스테커 개조, 자체 2D SLAM 적용",
    "개발품의 첫 매출 PJT": "일진글로벌 1공장, AGV 2대 적용",
    "제조를 고려한 포크리프트 AGV 워크샵": "AFL 호칭 사용, TFT 수립",
    "AFL 3D 기술개발의 핵심 요소 정의": "위치인식, HW, 시각화 SW 정의",
    "3D SLAM 기술 이식 및 개발 스케줄 수립": "원스탭 시운전, SW 아키텍처 설계",
    "AFL 운영소프트웨어 시각화 버전 개발 진행": "Multi, Single, Monitoring 구분",
    "AFL 운영소프트웨어 개발 공유회 진행": "개발 현황 공유 및 실습",
    "3D SLAM 기술 보완": "메모리와 정합 문제 해결",
    "AFL 실제 차량과 통합 Test를 통한 SW Debugging 지속": "SW 통신 프로토콜 매칭 및 수정",
    "AFL 프로덕트 로드맵 수립(V1.5, V1.8, V2.0, V3.0)": "V1.5-V3.0 개발 방향 정의",
    "AFL 버전 재정의": "V1.5-V3.0 정의",
    "AFL V2.0 추진계획 수립": "중소형 생산현장 타겟",
    "AFL V2.0 PROJECT KICK-OFF": "미래성장팀 협업, BM 구체화",
    "AFL V2.0 자체 모델 디자인리뷰": "자체 설계 모델링 공유",
    "AFL V2.0 마스터플랜 수립": "기획, 설계, HW, SW, PoC, 평가",
    "AFL V2.0 자체 모델 조립": "AFL V2.0 자체 모델 조립",
}


def parse_date(raw: str) -> tuple[int, int, int]:
    numbers = [int(value) for value in re.findall(r"\d+", raw)]
    year = numbers[0] if numbers else 9999
    month = numbers[1] if len(numbers) > 1 else 1
    day = numbers[2] if len(numbers) > 2 else 1
    return year, month, day


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def compact_label(value: str, max_length: int) -> str:
    text = normalize_text(value)
    text = text.replace("Mulit", "Multi")
    text = text.replace("AFL 로드맵 V1.5~V3", "AFL 로드맵 수립")
    text = text.replace("AFL 로드맵 수립.0", "AFL 로드맵 수립")
    if len(text) <= max_length:
        return text

    cut_points = [text.rfind(separator, 0, max_length + 1) for separator in [",", "·", " / ", " 및 ", "와 ", "과 "]]
    cut_at = max(cut_points)
    if cut_at >= max_length // 2:
        return text[:cut_at].strip(" ,·/")
    return f"{text[: max_length - 1].rstrip(' ,·/')}…"


def has_bad_spacing(value: str) -> bool:
    compacted = re.sub(r"[\s·,./()~:↓+-]", "", value)
    return bool(re.search(r"[가-힣A-Za-z0-9]{14,}", compacted)) and " " not in value


def score_item(raw_date: str, title: str, description: str) -> int:
    haystack = f"{raw_date} {title} {description}"
    score = min(len(description) // 18, 8)
    for keyword, weight in KEYWORDS.items():
        if keyword in haystack:
            score += weight
    return score


def load_items(source: Path) -> list[HistoryItem]:
    items: list[HistoryItem] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue

        parts = [normalize_text(part) for part in line.split("\t")]
        if len(parts) < 3:
            continue

        raw_date, title, description = parts[0], parts[1], " ".join(parts[2:])
        if not raw_date or not title:
            continue

        items.append(
            HistoryItem(
                raw_date=raw_date,
                title=title,
                description=description,
                sort_key=parse_date(raw_date),
                score=score_item(raw_date, title, description),
            )
        )

    return sorted(items, key=lambda item: item.sort_key)


def env_value(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def notion_token() -> str | None:
    return env_value("NOTION_TOKEN", "AFL_NOTION_TOKEN")


def extract_notion_database_id(value: str | None) -> str:
    source = value or DEFAULT_NOTION_DATABASE_ID
    match = re.search(r"([0-9a-fA-F]{32})", source.replace("-", ""))
    if not match:
        raise SystemExit(f"Invalid Notion database id or URL: {source}")
    return match.group(1)


def notion_property_value(prop: dict) -> str:
    value_type = prop.get("type")
    value = prop.get(value_type) if value_type else None

    if value_type in {"title", "rich_text"}:
        return normalize_text("".join(part.get("plain_text", "") for part in value or []))
    if value_type == "date" and value:
        return normalize_text(value.get("start") or "")
    if value_type in {"select", "status"} and value:
        return normalize_text(value.get("name") or "")
    if value_type == "multi_select":
        return normalize_text(", ".join(item.get("name", "") for item in value or []))
    if value_type in {"number", "checkbox", "url", "email", "phone_number"}:
        return normalize_text(str(value or ""))
    if value_type in {"created_time", "last_edited_time"}:
        return normalize_text(str(value or "")[:10])
    if value_type == "people":
        return normalize_text(", ".join(person.get("name", "") for person in value or []))
    if value_type == "formula" and isinstance(value, dict):
        return notion_property_value({"type": value.get("type"), value.get("type", ""): value.get(value.get("type", ""))})
    if value_type == "rollup" and isinstance(value, dict):
        if value.get("type") == "array":
            return normalize_text(", ".join(notion_property_value(item) for item in value.get("array", [])))
    return ""


def notion_configured() -> bool:
    return bool(notion_token())


def notion_request(path: str, token: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"https://api.notion.com/v1/{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_API_VERSION,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def pick_property(properties: dict, preferred_name: str | None, preferred_types: set[str], name_hints: tuple[str, ...] = ()) -> str | None:
    if preferred_name and preferred_name in properties:
        return preferred_name

    lowered_hints = tuple(hint.lower() for hint in name_hints)
    for name, prop in properties.items():
        if prop.get("type") in preferred_types and lowered_hints and any(hint in name.lower() for hint in lowered_hints):
            return name

    for name in properties:
        if lowered_hints and any(hint in name.lower() for hint in lowered_hints):
            return name

    for name, prop in properties.items():
        if prop.get("type") in preferred_types:
            return name

    return None


def pick_description_property(properties: dict, date_name: str | None, title_name: str | None) -> str | None:
    configured = env_value("NOTION_DESCRIPTION_PROPERTY", "AFL_NOTION_DESCRIPTION_PROPERTY")
    if configured and configured in properties:
        return configured

    name_hints = ("내용", "설명", "요약", "description", "summary", "detail", "note", "body")
    lowered_hints = tuple(hint.lower() for hint in name_hints)
    for name, prop in properties.items():
        if name in {date_name, title_name}:
            continue
        if prop.get("type") in {"title", "rich_text"} and any(hint in name.lower() for hint in lowered_hints):
            return name

    for name, prop in properties.items():
        if name not in {date_name, title_name} and prop.get("type") == "title":
            return name
    return None


def page_to_history_item(page: dict) -> HistoryItem | None:
    properties = page.get("properties", {})
    date_name = pick_property(
        properties,
        env_value("NOTION_DATE_PROPERTY", "AFL_NOTION_DATE_PROPERTY"),
        {"date", "rich_text", "title"},
        ("날짜", "일자", "date", "day", "year", "month"),
    )
    title_name = pick_property(
        properties,
        env_value("NOTION_TITLE_PROPERTY", "AFL_NOTION_TITLE_PROPERTY"),
        {"title", "rich_text"},
        ("제목", "타이틀", "title", "name", "이름"),
    )
    description_name = pick_description_property(properties, date_name, title_name)

    raw_date = notion_property_value(properties[date_name]) if date_name else ""
    title = notion_property_value(properties[title_name]) if title_name else ""

    if description_name and description_name in properties:
        description = notion_property_value(properties[description_name])
    else:
        description_parts = []
        for name, prop in properties.items():
            if name in {date_name, title_name}:
                continue
            value = notion_property_value(prop)
            if value:
                description_parts.append(value)
        description = normalize_text(" ".join(description_parts))

    if not raw_date:
        raw_date = normalize_text(str(page.get("created_time", ""))[:10])
    if not title:
        title = page.get("url", "Untitled")
    if not description:
        description = title

    return HistoryItem(
        raw_date=raw_date,
        title=title,
        description=description,
        sort_key=parse_date(raw_date),
        score=score_item(raw_date, title, description),
    )


def load_notion_items(database_id: str | None = None) -> list[HistoryItem]:
    token = notion_token()
    if not token:
        raise SystemExit("NOTION_TOKEN or AFL_NOTION_TOKEN is required to load the Notion database.")

    resolved_database_id = extract_notion_database_id(database_id or env_value("NOTION_DATABASE_ID", "AFL_NOTION_DATABASE_ID"))
    items: list[HistoryItem] = []
    payload: dict = {"page_size": 100}

    while True:
        data = notion_request(f"databases/{resolved_database_id}/query", token, payload)
        for page in data.get("results", []):
            item = page_to_history_item(page)
            if item:
                items.append(item)

        if not data.get("has_more"):
            break
        payload["start_cursor"] = data.get("next_cursor")

    return sorted(items, key=lambda item: item.sort_key)


def select_milestones(items: list[HistoryItem], limit: int) -> list[HistoryItem]:
    if len(items) <= limit:
        return items

    preferred = [item for title in PREFERRED_TITLES for item in items if item.title == title]
    if len(preferred) >= limit:
        return sorted(preferred[:limit], key=lambda item: item.sort_key)

    selected: list[HistoryItem] = [items[0], items[-1]]
    used: set[HistoryItem] = set(selected)
    middle = items[1:-1]
    bucket_count = max(limit - len(selected), 1)
    bucket_size = len(middle) / bucket_count

    for bucket_index in range(bucket_count):
        start = math.floor(bucket_index * bucket_size)
        end = math.floor((bucket_index + 1) * bucket_size)
        bucket = middle[start : max(start + 1, end)]
        winner = max(bucket, key=lambda item: (item.score, item.sort_key))
        if winner not in used:
            selected.append(winner)
            used.add(winner)

    if len(selected) < limit:
        for item in sorted(items, key=lambda value: value.score, reverse=True):
            if item not in used:
                selected.append(item)
                used.add(item)
            if len(selected) == limit:
                break

    return sorted(selected[:limit], key=lambda item: item.sort_key)


def compact_date(raw_date: str, sort_key: tuple[int, int, int]) -> str:
    year, month, day = sort_key
    if re.search(r"월|일", raw_date) and month:
        return f"{year:04d}-{month:02d}"
    return f"{year:04d}년"


def compact_title(title: str) -> str:
    replacements = {
        "포크리프트 AGV의 연구개발 필요성": "포크리프트 AGV 필요성",
        "포크리프트 AGV Feasibility 개발": "AGV Feasibility 개발",
        "개발품의 첫 매출 PJT": "첫 매출 PJT",
        "제조를 고려한 포크리프트 AGV 워크샵": "AFL 워크샵",
        "AFL 3D 기술개발의 핵심 요소 정의": "3D 핵심 요소 정의",
        "3D SLAM 기술 이식 및 개발 스케줄 수립": "3D SLAM 이식",
        "AFL 운영소프트웨어 시각화 버전 개발 진행": "AFL 운영 SW 시각화",
        "AFL 운영소프트웨어 개발 공유회 진행": "운영 SW 개발 공유회",
        "3D SLAM 기술 보완": "3D SLAM 기술 보완",
        "AFL 실제 차량과 통합 Test를 통한 SW Debugging 지속": "통합 Test & Debugging",
        "AFL 프로덕트 로드맵 수립(V1.5, V1.8, V2.0, V3.0)": "제품 로드맵 수립",
        "AFL 버전 재정의": "AFL 버전 재정의",
        "AFL V2.0 추진계획 수립": "V2.0 추진계획",
        "AFL V2.0 PROJECT KICK-OFF": "V2.0 Kick-off",
        "AFL V2.0 자체 모델 디자인리뷰": "V2.0 디자인리뷰",
        "AFL V2.0 마스터플랜 수립": "V2.0 마스터플랜",
        "AFL V2.0 자체 모델 조립": "자체 모델 조립",
    }
    return replacements.get(title, title)


def fallback_milestones(items: list[HistoryItem]) -> list[Milestone]:
    milestones = []
    for item in items:
        summary = FALLBACK_SUMMARIES.get(item.title)
        if summary is None:
            summary = item.description.split(",")[0].strip()
        milestones.append(
            Milestone(
                raw_date=compact_date(item.raw_date, item.sort_key),
                title=compact_title(item.title),
                summary=summary[:42],
                sort_key=item.sort_key,
            )
        )
    return milestones


def llm_configured() -> bool:
    return bool(os.environ.get("AFL_LLM_BASE_URL") and os.environ.get("AFL_LLM_API_KEY"))


def request_json(url: str, api_key: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    timeout = int(os.environ.get("AFL_LLM_TIMEOUT", "90"))
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="GET" if payload is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def discover_model(base_url: str, api_key: str) -> str | None:
    configured = os.environ.get("AFL_LLM_MODEL")
    if configured:
        return configured

    try:
        data = request_json(f"{base_url}/v1/models", api_key)
    except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as error:
        print(f"Could not discover LLM model, using fallback summaries: {error}")
        return None

    models = data.get("data", [])
    if not models:
        print("Could not discover LLM model, using fallback summaries: empty model list")
        return None
    return str(models[0].get("id") or "")


def ask_llm_for_milestones(items: list[HistoryItem], limit: int) -> list[Milestone] | None:
    if not llm_configured():
        return None

    base_url = os.environ["AFL_LLM_BASE_URL"].rstrip("/")
    api_key = os.environ["AFL_LLM_API_KEY"]
    model = discover_model(base_url, api_key)
    if not model:
        return None
    endpoint = f"{base_url}/v1/chat/completions"
    rows = [
        {
            "index": index + 1,
            "date": compact_date(item.raw_date, item.sort_key),
            "title": item.title,
            "description": item.description,
        }
        for index, item in enumerate(items)
    ]
    prompt = {
        "task": "주어진 AFL 핵심 이정표를 첨부 예시 같은 타임라인 라벨로 축약한다.",
        "rules": [
            f"입력된 {limit}개 항목을 모두 사용한다. 누락하거나 다른 항목을 추가하지 않는다.",
            "입력 순서를 유지한다.",
            "date는 입력값을 그대로 쓴다.",
            "title은 14자 안팎의 한국어 라벨로 쓴다.",
            "summary는 18자 안팎의 구체적인 근거 문장으로 쓴다.",
            "한국어 띄어쓰기를 반드시 지킨다. 글자 수를 줄이려고 단어를 붙여 쓰지 않는다.",
            "JSON만 반환한다. 형식: {\"milestones\":[{\"date\":\"YYYY-MM 또는 YYYY년\",\"title\":\"...\",\"summary\":\"...\"}]}",
        ],
        "rows": rows,
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You return compact Korean JSON for a business history timeline."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "temperature": 0.2,
    }
    try:
        data = request_json(endpoint, api_key, payload)
    except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as error:
        print(f"LLM unavailable, using fallback summaries: {error}")
        return None

    try:
        content = data["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE | re.DOTALL)
        parsed = json.loads(content)
        llm_items = parsed["milestones"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        print(f"LLM response was not valid timeline JSON, using fallback summaries: {error}")
        return None

    sort_keys = [item.sort_key for item in items]
    milestones = []
    for index, item in enumerate(llm_items[:limit]):
        date = normalize_text(str(item.get("date", "")))
        title = normalize_text(str(item.get("title", "")))
        summary = normalize_text(str(item.get("summary", "")))
        if not date or not title or not summary:
            return None
        if has_bad_spacing(title) or has_bad_spacing(summary):
            print("LLM response had poor Korean spacing, using fallback summaries.")
            return None
        milestones.append(
            Milestone(
                raw_date=date,
                title=compact_label(title, 30),
                summary=compact_label(summary, 34),
                sort_key=sort_keys[index],
            )
        )

    return milestones if len(milestones) == limit else None


def source_digest(source: Path) -> str:
    return hashlib.sha256(source.read_bytes()).hexdigest()[:12]


def render_timeline_rows(milestones: list[Milestone]) -> str:
    count = len(milestones)
    nodes = []
    for index, item in enumerate(milestones):
        side = "top" if index % 2 else "bottom"
        nodes.append(
            f"""
          <article class="milestone {side}" style="--i: {index + 1};">
            <div class="step-badge" aria-hidden="true">{index + 1:02d}</div>
            <div class="copy">
              <h2>{html.escape(item.title)}</h2>
              <p>{html.escape(item.summary)}</p>
            </div>
          </article>"""
        )

    segments = "\n".join(
        f"""
          <div class="segment segment-{index + 1}">
            <span>{html.escape(item.raw_date)}</span>
          </div>"""
        for index, item in enumerate(milestones)
    )

    return f"""
      <div class="timeline-board" style="--count: {count};">
        <div class="segments" aria-label="AFL 핵심 연혁 연도">
{segments}
        </div>
        <div class="milestones">
{''.join(nodes)}
        </div>
      </div>"""


def render_html(source: Path, items: list[HistoryItem], milestones: list[Milestone], used_llm: bool) -> str:
    timeline_rows = render_timeline_rows(milestones)

    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AFL 핵심 히스토리 타임라인</title>
  <style>
    @import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css");

    :root {{
      color-scheme: light;
      --ink: #17212b;
      --muted: #657383;
      --bg: #eef6f8;
      --panel: #ffffff;
      --line: #dbe8ee;
      --blue: #0878b8;
      --cyan: #19a7c7;
      --teal: #31b7aa;
      --green: #81c84b;
      --shadow: 0 24px 70px rgba(20, 72, 100, 0.16);
      --soft-shadow: 0 12px 30px rgba(21, 90, 120, 0.13);
    }}

    * {{ box-sizing: border-box; }}

    @keyframes fadeUp {{
      from {{
        opacity: 0;
        transform: translateY(18px);
      }}
      to {{
        opacity: 1;
        transform: translateY(0);
      }}
    }}

    @keyframes lineIn {{
      from {{
        opacity: 0;
        transform: scaleX(0.96);
      }}
      to {{
        opacity: 1;
        transform: scaleX(1);
      }}
    }}

    @keyframes pulseBadge {{
      0%, 100% {{ box-shadow: var(--soft-shadow); }}
      50% {{ box-shadow: 0 14px 34px rgba(21, 90, 120, 0.22); }}
    }}

    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      font-family: "Pretendard", "Apple SD Gothic Neo", "Malgun Gothic", system-ui, sans-serif;
      color: var(--ink);
      background:
        linear-gradient(135deg, rgba(8, 120, 184, 0.12), transparent 34%),
        linear-gradient(225deg, rgba(129, 200, 75, 0.14), transparent 32%),
        var(--bg);
      line-height: 1.35;
    }}

    main {{
      width: min(100vw, 1280px);
      aspect-ratio: 16 / 4;
      margin: 0;
      padding: 0;
    }}

    .timeline {{
      position: relative;
      width: 100%;
      height: 100%;
      margin: 0;
      overflow-x: auto;
      padding: 10px;
      border: 1px solid rgba(255, 255, 255, 0.88);
      border-radius: 18px;
      background:
        linear-gradient(180deg, rgba(255, 255, 255, 0.92), rgba(247, 252, 253, 0.88)),
        var(--panel);
      box-shadow: var(--shadow);
      animation: fadeUp 700ms ease both;
    }}

    .timeline-board {{
      position: relative;
      width: 100%;
      height: 100%;
      margin: 0 auto;
      border-radius: 18px;
      background:
        linear-gradient(90deg, rgba(8, 120, 184, 0.05) 1px, transparent 1px),
        linear-gradient(180deg, rgba(8, 120, 184, 0.05) 1px, transparent 1px);
      background-size: 76px 76px;
    }}

    .timeline-board::before {{
      content: "";
      position: absolute;
      left: 24px;
      right: 24px;
      top: 47%;
      height: 52px;
      border-radius: 999px;
      background: linear-gradient(90deg, rgba(8, 120, 184, 0.16), rgba(129, 200, 75, 0.16));
      filter: blur(22px);
      opacity: 0.78;
    }}

    .segments {{
      position: absolute;
      left: 24px;
      right: 24px;
      top: 45%;
      display: grid;
      grid-template-columns: repeat(var(--count), minmax(0, 1fr));
      align-items: center;
      z-index: 2;
      transform-origin: center;
      animation: lineIn 780ms ease 120ms both;
    }}

    .segment {{
      display: grid;
      place-items: center;
      height: 38px;
      margin-left: -1px;
      color: #ffffff;
      background: linear-gradient(90deg, var(--blue), var(--cyan));
      border: 1px solid rgba(255, 255, 255, 0.36);
      border-radius: 16px;
      box-shadow: 0 14px 28px rgba(8, 120, 184, 0.19);
      transition: transform 180ms ease, filter 180ms ease;
    }}

    .segment:hover {{
      transform: translateY(-2px);
      filter: brightness(1.04);
    }}

    .segment span {{
      display: block;
      padding: 0 6px;
      font-size: 13px;
      font-weight: 900;
      line-height: 1;
      white-space: nowrap;
      text-shadow: 0 2px 8px rgba(0, 0, 0, 0.14);
    }}

    .segment-1 {{ border-radius: 26px 16px 16px 26px; }}
    .segment-12 {{ border-radius: 16px 26px 26px 16px; }}
    .segment-1, .segment-2, .segment-3 {{ background: linear-gradient(90deg, #0878b8, #1099c2); }}
    .segment-4, .segment-5, .segment-6 {{ background: linear-gradient(90deg, #14a6c3, #2fb5b1); }}
    .segment-7, .segment-8, .segment-9 {{ background: linear-gradient(90deg, #2fb5b1, #62bf77); }}
    .segment-10, .segment-11, .segment-12 {{ background: linear-gradient(90deg, #62bf77, #91cb45); }}

    .milestones {{
      position: absolute;
      inset: 0 24px;
      display: grid;
      grid-template-columns: repeat(var(--count), minmax(0, 1fr));
      z-index: 3;
    }}

    .milestone {{
      --accent: #0878b8;
      position: relative;
      grid-column: var(--i);
      display: grid;
      justify-items: center;
      min-width: 0;
      text-align: center;
      animation: fadeUp 640ms ease both;
      animation-delay: calc(var(--i) * 55ms);
    }}

    .milestone:nth-child(1),
    .milestone:nth-child(2),
    .milestone:nth-child(3) {{ --accent: #0878b8; }}
    .milestone:nth-child(4),
    .milestone:nth-child(5),
    .milestone:nth-child(6) {{ --accent: #23a9bd; }}
    .milestone:nth-child(7),
    .milestone:nth-child(8),
    .milestone:nth-child(9) {{ --accent: #44b98b; }}
    .milestone:nth-child(10),
    .milestone:nth-child(11),
    .milestone:nth-child(12) {{ --accent: #82c84d; }}

    .step-badge {{
      position: absolute;
      left: 50%;
      display: grid;
      place-items: center;
      width: 54px;
      height: 54px;
      border: 4px solid var(--accent);
      border-radius: 50%;
      background: #ffffff;
      color: var(--accent);
      font-size: 17px;
      font-weight: 950;
      letter-spacing: 0;
      box-shadow: var(--soft-shadow);
      transform: translateX(-50%);
      z-index: 4;
      transition: transform 180ms ease, box-shadow 180ms ease;
      animation: pulseBadge 3.8s ease-in-out infinite;
      animation-delay: calc(var(--i) * 120ms);
    }}

    .step-badge::before {{
      content: "";
      position: absolute;
      inset: 6px;
      border-radius: 50%;
      background: color-mix(in srgb, var(--accent), transparent 91%);
      z-index: -1;
    }}

    .top .step-badge {{ top: 26%; }}
    .bottom .step-badge {{ top: 58%; }}

    .copy {{
      position: absolute;
      left: 50%;
      width: min(148px, calc(100% + 44px));
      min-height: 62px;
      padding: 7px 9px;
      border: 1px solid rgba(218, 231, 237, 0.9);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.88);
      box-shadow: 0 10px 24px rgba(38, 84, 105, 0.09);
      transform: translateX(-50%);
      z-index: 5;
      transition: transform 180ms ease, box-shadow 180ms ease, border-color 180ms ease;
    }}

    .milestone:hover .copy {{
      border-color: color-mix(in srgb, var(--accent), #ffffff 55%);
      box-shadow: 0 16px 34px rgba(38, 84, 105, 0.16);
      transform: translateX(-50%) translateY(-4px);
    }}

    .milestone:hover .step-badge {{
      transform: translateX(-50%) translateY(-3px) scale(1.03);
      box-shadow: 0 18px 38px rgba(21, 90, 120, 0.24);
    }}

    .top .copy {{ top: 4%; }}
    .bottom .copy {{ top: 77%; }}

    h2 {{
      margin: 0 0 4px;
      color: #18303f;
      font-size: 11.5px;
      line-height: 1.12;
      letter-spacing: 0;
      font-weight: 900;
      word-break: keep-all;
    }}

    p {{
      margin: 0;
      color: #637181;
      font-size: 9px;
      font-weight: 650;
      line-height: 1.25;
      word-break: keep-all;
    }}

    @media (max-width: 900px) {{
      body {{ display: block; }}
      main {{ width: 100%; aspect-ratio: auto; padding: 12px; }}
      .timeline {{ overflow-x: visible; padding: 12px; }}
      .timeline-board {{
        min-width: 0;
        height: auto;
        background: transparent;
      }}
      .timeline-board::before {{ display: none; }}
      .segments {{ display: none; }}
      .milestones {{
        position: relative;
        inset: auto;
        height: auto;
        display: grid;
        grid-template-columns: 1fr;
        gap: 12px;
      }}
      .milestone {{
        grid-column: auto;
        grid-template-columns: 54px 1fr;
        gap: 14px;
        align-items: center;
        justify-items: start;
        min-height: 88px;
        padding: 14px;
        border: 1px solid var(--line);
        border-radius: 14px;
        background: var(--panel);
        box-shadow: 0 8px 18px rgba(20, 72, 100, 0.08);
      }}
      .step-badge {{
        position: static;
        width: 50px;
        height: 50px;
        border-width: 4px;
        font-size: 17px;
        transform: none;
      }}
      .milestone:hover .copy,
      .milestone:hover .step-badge {{ transform: none; }}
      .copy {{
        position: static;
        width: auto;
        transform: none;
        text-align: left;
      }}
      .top .copy, .bottom .copy, .top .step-badge, .bottom .step-badge {{ top: auto; }}
      h2 {{ font-size: 18px; }}
      p {{ font-size: 13px; }}
    }}

    @media (prefers-reduced-motion: reduce) {{
      *, *::before, *::after {{
        animation-duration: 1ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 1ms !important;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="timeline" aria-label="AFL 핵심 연혁">
{timeline_rows}
    </section>
  </main>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the AFL history diagram HTML.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--source-type", choices=("auto", "file", "notion"), default="auto")
    parser.add_argument("--notion-database-id", default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--no-llm", action="store_true", help="Do not call the configured internal LLM.")
    args = parser.parse_args()

    use_notion = args.source_type == "notion" or (args.source_type == "auto" and notion_configured())
    items = load_notion_items(args.notion_database_id) if use_notion else load_items(args.source)
    if not items:
        source_name = "Notion database" if use_notion else str(args.source)
        raise SystemExit(f"No history rows found in {source_name}")

    selected = select_milestones(items, args.limit)
    llm_milestones = None if args.no_llm else ask_llm_for_milestones(selected, args.limit)
    milestones = llm_milestones or fallback_milestones(selected)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_html(args.source, items, milestones, used_llm=bool(llm_milestones)), encoding="utf-8")
    source_name = "Notion database" if use_notion else str(args.source)
    print(f"Built {args.output} from {source_name}, {len(items)} rows, selected {len(milestones)} milestones.")


if __name__ == "__main__":
    main()
