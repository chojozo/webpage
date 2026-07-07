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
DEFAULT_OUTPUT = ROOT / "afl" / "index.html"
DEFAULT_NOTION_DATABASE_ID = "382b89a3c13380a68912fcb8b4b74e3a"
DEFAULT_PROJECT_CONFIG = ROOT / "history-projects.json"
NOTION_API_VERSION = "2022-06-28"
SUMMARY_MAX_CHARS = 14
TITLE_MAX_CHARS = 14


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


@dataclass(frozen=True)
class ProjectConfig:
    slug: str
    name: str
    source_type: str
    notion_database_id: str | None
    source: Path
    output: Path
    style: str
    limit: int


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


PREFERRED_TITLES_10 = [
    PREFERRED_TITLES[index]
    for index in (0, 1, 2, 4, 5, 7, 8, 9, 10, 11)
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


def compact_summary(value: str, max_length: int = SUMMARY_MAX_CHARS) -> str:
    text = normalize_text(value)
    if len(text) <= max_length:
        return text

    separators = [",", "·", "/", " 및 ", " 또는 ", " 완료", " 적용", " 개선"]
    cut_points = [text.rfind(separator, 0, max_length + 1) for separator in separators]
    cut_at = max(cut_points)
    if cut_at >= max_length // 2:
        return text[:cut_at].strip(" ,·/")
    return text[:max_length].rstrip(" ,·/")


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


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def resolve_project_path(value: str | None, default: Path) -> Path:
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def project_from_dict(raw: dict) -> ProjectConfig:
    slug = str(raw.get("slug", "")).strip()
    if not slug:
        raise ValueError("Project config entry is missing slug.")
    name = str(raw.get("name") or slug).strip()
    source_type = str(raw.get("source_type") or "notion").strip()
    notion_database_id = raw.get("notion_database_id")
    source = resolve_project_path(raw.get("source"), DEFAULT_SOURCE)
    output = resolve_project_path(raw.get("output"), ROOT / slug / "index.html")
    style = str(raw.get("style") or "branch-clean").strip()
    limit = int(raw.get("limit") or 10)
    return ProjectConfig(
        slug=slug,
        name=name,
        source_type=source_type,
        notion_database_id=str(notion_database_id).strip() if notion_database_id else None,
        source=source,
        output=output,
        style=style,
        limit=limit,
    )


def load_project_configs(path: Path) -> dict[str, ProjectConfig]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_projects = data.get("projects", data if isinstance(data, list) else [])
    projects = [project_from_dict(raw) for raw in raw_projects]
    return {project.slug: project for project in projects}


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

    preferred_titles = PREFERRED_TITLES_10 if limit == 10 else PREFERRED_TITLES
    preferred = [item for title in preferred_titles for item in items if item.title == title]
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
    numbers = re.findall(r"\d+", raw_date)
    if len(numbers) >= 2 and month:
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
                summary=compact_summary(summary),
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
        fallback_model = os.environ.get("AFL_LLM_FALLBACK_MODEL", "default")
        print(f"Could not discover LLM model, trying '{fallback_model}': {error}")
        return fallback_model

    models = data.get("data", [])
    if not models:
        fallback_model = os.environ.get("AFL_LLM_FALLBACK_MODEL", "default")
        print(f"Could not discover LLM model, trying '{fallback_model}': empty model list")
        return fallback_model
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
        date = compact_date(items[index].raw_date, sort_keys[index])
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
                title=compact_label(title, 18),
                summary=compact_summary(summary),
                sort_key=sort_keys[index],
            )
        )

    return milestones if len(milestones) == limit else None


def ask_project_llm_for_milestones(items: list[HistoryItem], limit: int, project_name: str) -> list[Milestone] | None:
    if not llm_configured():
        return None

    base_url = os.environ["AFL_LLM_BASE_URL"].rstrip("/")
    api_key = os.environ["AFL_LLM_API_KEY"]
    model = discover_model(base_url, api_key)
    if not model:
        return None

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
        "task": f"{project_name} 히스토리 데이터를 발표용 타임라인 라벨로 축약한다.",
        "rules": [
            f"입력 {limit}개 항목을 모두 사용한다. 누락하거나 새 항목을 추가하지 않는다.",
            "입력 순서를 유지한다.",
            "date는 입력 값을 그대로 쓴다.",
            "title은 14자 이내의 자연스러운 한국어 라벨로 쓴다.",
            "summary는 14자 이내의 구체적인 근거 문장으로 쓴다.",
            "한국어 띄어쓰기를 반드시 지킨다. 단어를 억지로 붙여 쓰지 않는다.",
            "JSON만 반환한다. 형식: {\"milestones\":[{\"date\":\"YYYY-MM 또는 YYYY년\",\"title\":\"...\",\"summary\":\"...\"}]}",
        ],
        "rows": rows,
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You return compact Korean JSON for a business history timeline. Return JSON only."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "temperature": 0.2,
    }

    try:
        data = request_json(f"{base_url}/v1/chat/completions", api_key, payload)
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
        date = compact_date(items[index].raw_date, sort_keys[index])
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
                title=compact_summary(title, TITLE_MAX_CHARS),
                summary=compact_summary(summary),
                sort_key=sort_keys[index],
            )
        )

    return milestones if len(milestones) == limit else None


def source_digest(source: Path) -> str:
    return hashlib.sha256(source.read_bytes()).hexdigest()[:12]


def render_timeline_rows(milestones: list[Milestone], project_name: str = "AFL") -> str:
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
        <div class="segments" aria-label="{html.escape(project_name)} 핵심 연혁 연도">
{segments}
        </div>
        <div class="milestones">
{''.join(nodes)}
        </div>
      </div>"""


def render_html(source: Path, items: list[HistoryItem], milestones: list[Milestone], used_llm: bool, project_name: str = "AFL") -> str:
    project_label = html.escape(project_name)
    timeline_rows = render_timeline_rows(milestones, project_name=project_name)

    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{project_label} 핵심 히스토리 타임라인</title>
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
    <section class="timeline" aria-label="{project_label} 핵심 연혁">
{timeline_rows}
    </section>
  </main>
</body>
</html>
"""


BRANCH_ICONS = {
    "need": """<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v18M5 8h14M6 16h12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" stroke-width="2"/></svg>""",
    "build": """<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 17h10M8 17l1-9h6l1 9M9 8l3-4 3 4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>""",
    "sales": """<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 17h16M7 17V9h4v8M13 17V5h4v12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>""",
    "elements": """<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M12 5v14M7 7l10 10M17 7 7 17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>""",
    "slam": """<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 17l7-10 7 10M8 13h8M12 7v10" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>""",
    "roadmap": """<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 17c4-6 10-6 14-10M6 7h5v5H6zM13 14h5v5h-5z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>""",
    "plan": """<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4h10v16H7zM10 8h4M10 12h4M10 16h2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>""",
    "kickoff": """<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 17h10M8 17l1-9h6l1 9M12 8V4M9 7l3-3 3 3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>""",
    "master": """<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6h12v12H6zM9 9h6v6H9zM4 10h2M4 14h2M18 10h2M18 14h2M10 4v2M14 4v2M10 18v2M14 18v2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>""",
    "assembly": """<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 8l6-3 6 3v8l-6 3-6-3zM6 8l6 3 6-3M12 11v8" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>""",
    "request": """<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4h7l3 3v13H7zM14 4v4h4M9 12h6M9 16h4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>""",
    "prototype": """<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 8l5-3 5 3v6l-5 3-5-3zM7 8l5 3 5-3M12 11v6M5 19h14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>""",
    "test": """<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 18c3-6 9-6 12 0M8 14l-2-4h12l-2 4M10 10V6h4v4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>""",
    "durability": """<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l7 4v5c0 5-3 8-7 9-4-1-7-4-7-9V7zM9 12l2 2 4-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>""",
    "mold": """<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 7h14v10H5zM8 10h3v4H8zM13 10h3v4h-3zM8 4v3M16 4v3M8 17v3M16 17v3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>""",
    "design": """<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 16l6-6 4 4-6 6H4zM13 7l4-4 4 4-4 4zM14 14l4 4M16 12l4 4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>""",
    "production": """<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 18h16M6 18V9l4 3V9l4 3V7h4v11M8 15h2M13 15h2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>""",
    "feedback": """<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 6h14v10H9l-4 4zM8 10h8M8 13h5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>""",
}


def branch_icon_for(item: Milestone) -> str:
    text = f"{item.title} {item.summary}".lower()
    rules = [
        ("production", ("양산", "납품", "주문", "100개", "100ea")),
        ("design", ("디자인", "도출", "설계", "알루미늄", "고무패드")),
        ("feedback", ("피드백", "개선", "협의")),
        ("durability", ("내구", "마모", "10배", "7배")),
        ("mold", ("금형", "cavity", "배합", "소재")),
        ("test", ("테스트", "주행", "mvp")),
        ("prototype", ("시제품", "제작")),
        ("request", ("의뢰", "접수", "개발 의뢰")),
        ("assembly", ("조립", "모델 조립", "자체 모델")),
        ("master", ("마스터플랜", "master")),
        ("kickoff", ("kick-off", "kickoff", "킥오프")),
        ("plan", ("추진계획", "계획")),
        ("roadmap", ("로드맵", "버전", "v1.5", "v3.0")),
        ("slam", ("slam", "이식")),
        ("elements", ("요소", "정의", "3d 핵심")),
        ("sales", ("매출", "pjt", "프로젝트")),
        ("build", ("feasibility", "개발")),
        ("need", ("필요", "필요성")),
    ]
    for icon_key, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return BRANCH_ICONS[icon_key]
    return BRANCH_ICONS["need"]


def render_branch_rows(milestones: list[Milestone], clean: bool = False, project_name: str = "AFL") -> str:
    colors = (
        ["#334155", "#0f766e", "#2563eb", "#7c3aed", "#0e7490"]
        if clean
        else ["#6f7d94", "#77c9c9", "#f3bf2e", "#ee6d59", "#86c9a3", "#6f9fd8"]
    )
    segments = []
    nodes = []
    for index, item in enumerate(milestones):
        side = "top" if index % 2 else "bottom"
        color = colors[index % len(colors)]
        segments.append(
            f"""
          <div class="branch-segment" style="--accent: {color};"></div>"""
        )
        nodes.append(
            f"""
          <article class="branch-item {side}" style="--i: {index + 1}; --accent: {color};">
            <div class="branch-copy">
              <h2>{html.escape(compact_label(item.title, 24))}</h2>
              <p>{html.escape(item.summary)}</p>
            </div>
            <div class="branch-date">{html.escape(item.raw_date)}</div>
            <div class="branch-stem" aria-hidden="true"></div>
            <div class="branch-icon" aria-hidden="true">{branch_icon_for(item)}</div>
            <div class="branch-dot" aria-hidden="true"></div>
          </article>"""
        )

    return f"""
      <div class="branch-board" style="--count: {len(milestones)};">
        <div class="branch-axis" aria-label="{html.escape(project_name)} 핵심 연혁 연도">
{''.join(segments)}
        </div>
        <div class="branch-items">
{''.join(nodes)}
        </div>
      </div>"""


def render_branch_html(source: Path, items: list[HistoryItem], milestones: list[Milestone], used_llm: bool, clean: bool = False, project_name: str = "AFL") -> str:
    project_label = html.escape(project_name)
    branch_rows = render_branch_rows(milestones, clean=clean, project_name=project_name)
    aspect_ratio = "16 / 4.16" if clean else "16 / 4"
    body_background = "#f8fafc" if clean else "radial-gradient(circle at 12% 24%, rgba(255,255,255,0.54), transparent 24%), linear-gradient(135deg, #fbf3e6, #efe4d4)"
    paper = "#ffffff" if clean else "#f5ecdf"
    line = "rgba(15, 23, 42, 0.08)" if clean else "rgba(93, 111, 126, 0.16)"
    shadow = "0 18px 45px rgba(15, 23, 42, 0.10)" if clean else "0 22px 60px rgba(82, 67, 48, 0.13)"
    board_background = "#ffffff" if clean else "linear-gradient(90deg, var(--line) 1px, transparent 1px), linear-gradient(180deg, rgba(93,111,126,0.08) 1px, transparent 1px)"
    board_padding = "18px 28px 18px" if clean else "28px 32px 24px"
    axis_height = "20px" if clean else "24px"
    date_size = "14px" if clean else "17px"
    top_date = "calc(50% + 22px)" if clean else "calc(50% + 25px)"
    bottom_date = "calc(50% - 37px)" if clean else "calc(50% - 47px)"
    copy_width = "min(174px, calc(100% + 58px))" if clean else "min(164px, calc(100% + 64px))"
    copy_h2_size = "13px" if clean else "14px"
    copy_p_size = "10.5px" if clean else "10.2px"
    copy_p_color = "rgba(30, 41, 59, 0.74)" if clean else "rgba(54, 65, 66, 0.68)"
    icon_size = "44px" if clean else "52px"
    icon_inset = "6px" if clean else "7px"
    icon_svg_size = "22px" if clean else "26px"
    top_icon = "57px" if clean else "78px"
    bottom_icon = "57px" if clean else "78px"
    stem_offset = "101px" if clean else "130px"
    dot_size = "7px" if clean else "8px"

    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{project_label} 핵심 히스토리 타임라인</title>
  <style>
    @import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css");

    :root {{
      color-scheme: light;
      --ink: #334052;
      --muted: #8d968f;
      --paper: {paper};
      --line: {line};
      --shadow: {shadow};
    }}

    * {{ box-sizing: border-box; }}

    @keyframes rise {{
      from {{ opacity: 0; transform: translateY(16px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}

    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      font-family: "Pretendard", "Apple SD Gothic Neo", "Malgun Gothic", system-ui, sans-serif;
      color: var(--ink);
      background: {body_background};
      line-height: 1.32;
    }}

    main {{
      width: min(100vw, 1280px);
      aspect-ratio: {aspect_ratio};
      margin: 0;
      padding: 0;
    }}

    .timeline {{
      width: 100%;
      height: 100%;
      overflow: hidden;
      border: 1px solid rgba(226, 232, 240, 0.9);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.18), rgba(255,255,255,0)),
        var(--paper);
      box-shadow: var(--shadow);
    }}

    .branch-board {{
      position: relative;
      width: 100%;
      height: 100%;
      padding: {board_padding};
      background: {board_background};
      background-size: 72px 72px;
    }}

    .branch-axis {{
      position: absolute;
      left: 36px;
      right: 36px;
      top: 50%;
      display: grid;
      grid-template-columns: repeat(var(--count), minmax(0, 1fr));
      height: {axis_height};
      transform: translateY(-50%);
      z-index: 2;
    }}

    .branch-segment {{
      position: relative;
      display: grid;
      place-items: center;
      margin-left: -1px;
      color: #fff;
      background: var(--accent);
      clip-path: polygon(0 0, calc(100% - 14px) 0, 100% 50%, calc(100% - 14px) 100%, 0 100%, 12px 50%);
      box-shadow: 0 8px 14px rgba(80, 68, 48, 0.09);
    }}

    .branch-segment:first-child {{
      clip-path: polygon(0 0, calc(100% - 14px) 0, 100% 50%, calc(100% - 14px) 100%, 0 100%);
      border-radius: 4px 0 0 4px;
    }}

    .branch-segment:last-child {{
      border-radius: 0 4px 4px 0;
    }}

    .branch-date {{
      position: absolute;
      left: 50%;
      display: block;
      padding: 0 4px;
      color: var(--accent);
      font-size: {date_size};
      line-height: 1;
      font-weight: 950;
      letter-spacing: 0;
      text-shadow: 0 2px 0 rgba(255,255,255,0.5);
      white-space: nowrap;
      transform: translateX(-50%);
      z-index: 4;
    }}

    .bottom .branch-date {{
      top: {bottom_date};
    }}

    .top .branch-date {{
      top: {top_date};
    }}

    .branch-items {{
      position: absolute;
      inset: 0 36px;
      display: grid;
      grid-template-columns: repeat(var(--count), minmax(0, 1fr));
      z-index: 3;
    }}

    .branch-item {{
      position: relative;
      grid-column: var(--i);
      display: grid;
      justify-items: center;
      min-width: 0;
      color: var(--accent);
      text-align: center;
      animation: rise 520ms ease both;
      animation-delay: calc(var(--i) * 45ms);
    }}

    .branch-copy {{
      position: absolute;
      left: 50%;
      width: {copy_width};
      transform: translateX(-50%);
      color: #51605d;
    }}

    .top .branch-copy {{ top: 11px; }}
    .bottom .branch-copy {{ bottom: 12px; }}

    .branch-copy h2 {{
      margin: 0 0 6px;
      color: var(--accent);
      font-size: {copy_h2_size};
      line-height: 1.14;
      font-weight: 950;
      letter-spacing: 0;
      white-space: nowrap;
      word-break: keep-all;
    }}

    .branch-copy p {{
      margin: 0;
      color: {copy_p_color};
      font-size: {copy_p_size};
      line-height: 1.3;
      font-weight: 760;
      word-break: keep-all;
    }}

    .branch-icon {{
      position: absolute;
      left: 50%;
      display: grid;
      place-items: center;
      width: {icon_size};
      height: {icon_size};
      border-radius: 50%;
      background: color-mix(in srgb, var(--accent), white 16%);
      color: #fff;
      box-shadow:
        inset 0 0 0 {icon_inset} rgba(255,255,255,0.25),
        0 12px 22px rgba(80, 68, 48, 0.14);
      transform: translateX(-50%);
    }}

    .branch-icon svg {{
      width: {icon_svg_size};
      height: {icon_svg_size};
      display: block;
    }}

    .top .branch-icon {{ top: {top_icon}; }}
    .bottom .branch-icon {{ bottom: {bottom_icon}; }}

    .branch-stem {{
      position: absolute;
      left: 50%;
      width: 3px;
      background: color-mix(in srgb, var(--accent), white 24%);
      transform: translateX(-50%);
      opacity: 0.72;
    }}

    .top .branch-stem {{ top: {stem_offset}; height: calc(50% - {stem_offset}); }}
    .bottom .branch-stem {{ bottom: {stem_offset}; height: calc(50% - {stem_offset}); }}

    .branch-dot {{
      position: absolute;
      left: 50%;
      top: 50%;
      width: {dot_size};
      height: {dot_size};
      border: 2px solid rgba(255,255,255,0.8);
      border-radius: 50%;
      background: #fff;
      transform: translate(-50%, -50%);
      box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent), white 20%);
    }}

    @media (max-width: 900px) {{
      body {{ display: block; }}
      main {{ width: 100%; aspect-ratio: auto; }}
      .timeline {{ overflow: visible; }}
      .branch-board {{
        height: auto;
        padding: 14px;
        background: var(--paper);
      }}
      .branch-axis {{ display: none; }}
      .branch-items {{
        position: relative;
        inset: auto;
        display: grid;
        grid-template-columns: 1fr;
        gap: 10px;
      }}
      .branch-item {{
        grid-column: auto;
        display: grid;
        grid-template-columns: 56px 1fr;
        gap: 12px;
        align-items: center;
        min-height: 82px;
        padding: 12px;
        border-radius: 12px;
        background: rgba(255,255,255,0.48);
        text-align: left;
      }}
      .branch-copy,
      .top .branch-copy,
      .bottom .branch-copy {{
        position: static;
        width: auto;
        transform: none;
      }}
      .branch-icon,
      .top .branch-icon,
      .bottom .branch-icon {{
        position: static;
        transform: none;
      }}
      .branch-stem,
      .branch-dot {{ display: none; }}
      .branch-copy h2 {{ font-size: 16px; }}
      .branch-copy p {{ font-size: 12px; }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="timeline" aria-label="{project_label} 핵심 연혁">
{branch_rows}
    </section>
  </main>
</body>
</html>
"""


def build_history(
    source: Path,
    source_type: str,
    notion_database_id: str | None,
    output: Path,
    style: str,
    limit: int,
    project_name: str,
    no_llm: bool,
) -> None:
    use_notion = source_type == "notion" or (source_type == "auto" and notion_configured())
    items = load_notion_items(notion_database_id) if use_notion else load_items(source)
    if not items:
        source_name = "Notion database" if use_notion else str(source)
        raise SystemExit(f"No history rows found in {source_name}")

    selected = select_milestones(items, limit)
    llm_milestones = None if no_llm else ask_project_llm_for_milestones(selected, limit, project_name)
    milestones = llm_milestones or fallback_milestones(selected)
    output.parent.mkdir(parents=True, exist_ok=True)
    if style == "branch-clean":
        rendered = render_branch_html(source, items, milestones, used_llm=bool(llm_milestones), clean=True, project_name=project_name)
    elif style == "branch":
        rendered = render_branch_html(source, items, milestones, used_llm=bool(llm_milestones), project_name=project_name)
    else:
        rendered = render_html(source, items, milestones, used_llm=bool(llm_milestones), project_name=project_name)
    output.write_text(rendered, encoding="utf-8")
    source_name = "Notion database" if use_notion else str(source)
    print(f"Built {output} from {source_name}, {len(items)} rows, selected {len(milestones)} milestones.")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Build history diagram HTML pages.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--source-type", choices=("auto", "file", "notion"), default="auto")
    parser.add_argument("--notion-database-id", default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--style", choices=("classic", "branch", "branch-clean"), default="classic")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--project-name", default="AFL")
    parser.add_argument("--project", help="Project slug from history-projects.json, or all.")
    parser.add_argument("--project-config", type=Path, default=DEFAULT_PROJECT_CONFIG)
    parser.add_argument("--no-llm", action="store_true", help="Do not call the configured internal LLM.")
    args = parser.parse_args()

    if args.project:
        projects = load_project_configs(resolve_project_path(str(args.project_config), DEFAULT_PROJECT_CONFIG))
        if not projects:
            raise SystemExit(f"No projects found in {args.project_config}")
        selected_projects = list(projects.values()) if args.project == "all" else [projects.get(args.project)]
        if any(project is None for project in selected_projects):
            available = ", ".join(sorted(projects))
            raise SystemExit(f"Unknown project '{args.project}'. Available: {available}")
        for project in selected_projects:
            assert project is not None
            build_history(
                source=project.source,
                source_type=project.source_type,
                notion_database_id=project.notion_database_id,
                output=project.output,
                style=project.style,
                limit=project.limit,
                project_name=project.name,
                no_llm=args.no_llm,
            )
        return

    build_history(
        source=args.source,
        source_type=args.source_type,
        notion_database_id=args.notion_database_id,
        output=args.output,
        style=args.style,
        limit=args.limit,
        project_name=args.project_name,
        no_llm=args.no_llm,
    )


if __name__ == "__main__":
    main()

