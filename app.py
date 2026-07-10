from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from flask import Flask, abort, jsonify, request, send_from_directory


BASE_DIR = Path(__file__).resolve().parent
PAGE_DIR = BASE_DIR
ASSET_DIR = BASE_DIR / "assets"
DATA_DIR = BASE_DIR / "data"
SEED_PATH = DATA_DIR / "korea_oem_odm_seed.csv"
JOB_DB_PATH = DATA_DIR / "simulate_jobs.sqlite3"


def env_positive_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            os.environ.setdefault(key, value)


load_dotenv(BASE_DIR / ".env")

SAM_BASE_URL = os.getenv("SAM_BASE_URL", "https://sam.soonsoon.ai").rstrip("/")
SAM_API_KEY = os.getenv("SAM_API_KEY", "sam-25d2caf63494334fc56b40b93b09589b152026c501a7ab34")
SAM_MODEL = os.getenv("SAM_MODEL", "az-deepseek-v4-flash")
SIMULATE_MAX_WORKERS = env_positive_int("SIMULATE_MAX_WORKERS", 8)
LOCAL_DEV_ORIGINS = {
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5500",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:5500",
}
USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
STALE_JOB_ERROR = "서버 재시작으로 작업이 중단되었습니다. 다시 실행해 주세요."

PACKAGE_LABELS = {
    "pouch": "파우치",
    "stick": "스틱",
    "cup": "컵/용기",
    "tray": "트레이",
    "bottle": "병/캔",
}

PACKAGE_MULTIPLIER = {
    "pouch": 1.00,
    "stick": 1.18,
    "cup": 1.12,
    "tray": 1.22,
    "bottle": 1.16,
}

CLAIM_LABELS = {
    "lowSugar": "저당",
    "lowSodium": "나트륨 감소",
    "protein": "고단백",
    "vegan": "비건",
}

PACKAGE_KEYWORDS = {
    "pouch": ["파우치", "스파우트파우치", "레토르트파우치"],
    "stick": ["스틱", "스틱포", "포장"],
    "cup": ["컵", "용기", "소분"],
    "tray": ["트레이", "실링", "밀키트"],
    "bottle": ["병", "캔", "병입", "PET", "PET병"],
}

CATEGORY_META: dict[str, dict[str, Any]] = {
    "sauce": {
        "label": "소스/양념",
        "unit": "kg",
        "min_qty": 1000,
        "base_unit_cost": 1480,
        "sample_cost": 450000,
        "test_cost": 280000,
        "aliases": ["소스", "양념", "드레싱", "육수", "시즈닝", "레시피개발"],
        "required_certifications": ["HACCP"],
        "default_process": [
            {"name": "배합", "note": "향, 염도, 점도 목표를 맞추는 단계입니다."},
            {"name": "가열/살균", "note": "상온 또는 냉장 유통을 전제로 미생물 기준을 맞춥니다."},
            {"name": "충진", "note": "점도에 맞는 충진 속도와 수율을 확인합니다."},
            {"name": "포장", "note": "포장재 적합성과 라벨 문구를 점검합니다."},
        ],
        "checkpoints": ["살균 조건", "점도와 충진성", "나트륨 저감 표시", "유통기한 시험"],
    },
    "powder": {
        "label": "분말/스틱",
        "unit": "포",
        "min_qty": 5000,
        "base_unit_cost": 620,
        "sample_cost": 380000,
        "test_cost": 340000,
        "aliases": ["분말", "스틱", "파우더", "프리믹스", "과립", "건강기능식품"],
        "required_certifications": ["HACCP", "GMP"],
        "default_process": [
            {"name": "원료계량", "note": "기능성 원료와 향미 원료의 사용 범위를 정리합니다."},
            {"name": "혼합/과립", "note": "흡습과 분산성을 고려해 분말 흐름성을 맞춥니다."},
            {"name": "스틱 충진", "note": "목표 중량과 충진 오차를 관리합니다."},
            {"name": "검수/포장", "note": "시험 기준과 외포장 규격을 확인합니다."},
        ],
        "checkpoints": ["흡습 안정성", "스틱 충진 적합성", "중금속 시험", "제조 기준 확인"],
    },
    "meal": {
        "label": "간편식/밀키트",
        "unit": "팩",
        "min_qty": 3000,
        "base_unit_cost": 2350,
        "sample_cost": 520000,
        "test_cost": 420000,
        "aliases": ["간편식", "밀키트", "냉장", "냉동", "HMR", "도시락"],
        "required_certifications": ["HACCP"],
        "default_process": [
            {"name": "원료 전처리", "note": "원재료 보관과 전처리 기준을 먼저 맞춥니다."},
            {"name": "조리", "note": "가열 조건과 배치 수율을 확인합니다."},
            {"name": "충진/실링", "note": "트레이 또는 파우치 실링 안정성을 검토합니다."},
            {"name": "냉장/냉동 출고", "note": "콜드체인과 유통기한 시험 계획을 잡습니다."},
        ],
        "checkpoints": ["위생 기준", "미생물 기준", "포장 실링", "콜드체인"],
    },
    "snack": {
        "label": "건강간식",
        "unit": "개",
        "min_qty": 3000,
        "base_unit_cost": 920,
        "sample_cost": 360000,
        "test_cost": 310000,
        "aliases": ["건강간식", "스낵", "바", "쿠키", "베이커리", "곡물스낵"],
        "required_certifications": ["HACCP"],
        "default_process": [
            {"name": "배합", "note": "곡물, 단백질원, 감미료의 배합 방향을 잡습니다."},
            {"name": "성형", "note": "바삭함 또는 점착성에 맞는 물성을 만듭니다."},
            {"name": "굽기/건조", "note": "수분활성도와 식감 유지 포인트를 확인합니다."},
            {"name": "개별포장", "note": "산패, 파손, 영양표시 작업 범위를 정리합니다."},
        ],
        "checkpoints": ["알레르기 표시", "영양성분", "수분활성도", "개별포장 기준"],
    },
    "drink": {
        "label": "병/캔 음료",
        "unit": "병",
        "min_qty": 2500,
        "base_unit_cost": 1180,
        "sample_cost": 480000,
        "test_cost": 390000,
        "aliases": ["음료", "액상", "주스", "커피", "드링크", "병입"],
        "required_certifications": ["HACCP", "FSSC22000"],
        "default_process": [
            {"name": "배합", "note": "산도, 당도, 향미를 목표 가격대에 맞춥니다."},
            {"name": "여과/균질", "note": "침전과 분리 가능성을 먼저 줄입니다."},
            {"name": "살균", "note": "충진 방식에 맞는 살균 조건을 확인합니다."},
            {"name": "충진/라벨링", "note": "병입 또는 캔 충진 라인과 표시 작업 범위를 봅니다."},
        ],
        "checkpoints": ["산도/당도", "살균 안정성", "병/캔 충진", "표시사항"],
    },
}

app = Flask(__name__, static_folder=None)
SIMULATE_EXECUTOR = ThreadPoolExecutor(max_workers=SIMULATE_MAX_WORKERS, thread_name_prefix="simulate-job")


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def open_job_db() -> sqlite3.Connection:
    conn = sqlite3.connect(JOB_DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_job_store() -> None:
    JOB_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    now = utc_now_text()
    with open_job_db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS simulate_jobs (
                job_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                result_json TEXT,
                error_text TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_simulate_jobs_user_created
            ON simulate_jobs (user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_simulate_jobs_status_created
            ON simulate_jobs (status, created_at DESC);
            """
        )
        conn.execute(
            """
            UPDATE simulate_jobs
            SET status = ?, error_text = ?, completed_at = ?, updated_at = ?
            WHERE status IN ('queued', 'running')
            """,
            ("failed", STALE_JOB_ERROR, now, now),
        )


def normalize_user_id(value: Any) -> str:
    user_id = str(value or "").strip()
    return user_id if USER_ID_PATTERN.fullmatch(user_id) else ""


def resolve_user_id(body: dict[str, Any]) -> str:
    return normalize_user_id(body.get("user_id")) or f"ff-{uuid4().hex}"


def create_simulation_job(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    job_id = uuid4().hex
    now = utc_now_text()
    with open_job_db() as conn:
        conn.execute(
            """
            INSERT INTO simulate_jobs (job_id, user_id, status, payload_json, created_at, updated_at)
            VALUES (?, ?, 'queued', ?, ?, ?)
            """,
            (job_id, user_id, json.dumps(payload, ensure_ascii=False), now, now),
        )
    try:
        SIMULATE_EXECUTOR.submit(run_simulation_job, job_id)
    except RuntimeError as exc:
        mark_job_failed(job_id, f"{type(exc).__name__}: {exc}")
    return {
        "job_id": job_id,
        "user_id": user_id,
        "status": "queued",
        "poll_url": f"/api/simulate/{job_id}?user_id={user_id}",
    }


def mark_job_failed(job_id: str, error_text: str) -> None:
    now = utc_now_text()
    with open_job_db() as conn:
        conn.execute(
            """
            UPDATE simulate_jobs
            SET status = 'failed', error_text = ?, completed_at = ?, updated_at = ?
            WHERE job_id = ?
            """,
            (error_text[:500], now, now, job_id),
        )


def run_simulation_job(job_id: str) -> None:
    payload_json = ""
    started_at = utc_now_text()
    with open_job_db() as conn:
        row = conn.execute("SELECT payload_json FROM simulate_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            return
        updated = conn.execute(
            """
            UPDATE simulate_jobs
            SET status = 'running', started_at = ?, updated_at = ?, error_text = NULL
            WHERE job_id = ? AND status = 'queued'
            """,
            (started_at, started_at, job_id),
        )
        if updated.rowcount != 1:
            return
        payload_json = str(row["payload_json"])

    try:
        payload = json.loads(payload_json)
        result = build_response(payload)
    except Exception as exc:
        mark_job_failed(job_id, f"{type(exc).__name__}: {exc}")
        return

    completed_at = utc_now_text()
    with open_job_db() as conn:
        conn.execute(
            """
            UPDATE simulate_jobs
            SET status = 'completed', result_json = ?, completed_at = ?, updated_at = ?
            WHERE job_id = ?
            """,
            (json.dumps(result, ensure_ascii=False), completed_at, completed_at, job_id),
        )


def get_simulation_job(job_id: str, user_id: str) -> sqlite3.Row | None:
    with open_job_db() as conn:
        return conn.execute(
            """
            SELECT job_id, user_id, status, result_json, error_text, created_at, started_at, completed_at
            FROM simulate_jobs
            WHERE job_id = ? AND user_id = ?
            """,
            (job_id, user_id),
        ).fetchone()


def serialize_simulation_job(row: sqlite3.Row) -> dict[str, Any]:
    payload = {
        "job_id": str(row["job_id"]),
        "user_id": str(row["user_id"]),
        "status": str(row["status"]),
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
    }
    if row["status"] == "completed" and row["result_json"]:
        payload["result"] = json.loads(str(row["result_json"]))
    if row["status"] == "failed":
        payload["error"] = str(row["error_text"] or "생성 중 오류가 발생했습니다.")
    return payload


init_job_store()


def is_allowed_cors_origin(origin: str) -> bool:
    value = (origin or "").strip().rstrip("/")
    if not value:
        return False
    if value in LOCAL_DEV_ORIGINS:
        return True
    # Allow production domains
    if "vercel.app" in value or "food-flow" in value:
        return True
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {"localhost", "127.0.0.1"}


@app.after_request
def add_local_dev_cors(response):
    origin = request.headers.get("Origin", "")
    if request.path.startswith("/api/") and is_allowed_cors_origin(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Vary"] = "Origin"
    return response


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").lower())


def compact_items(values: list[str], limit: int = 3) -> list[str]:
    return [value.strip() for value in values if value and value.strip()][:limit]


def money_text(value: float) -> str:
    return f"{round(value):,}원"


def safe_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def certification_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    for raw in re.split(r"[,/|·\s]+", value or ""):
        token = raw.strip()
        upper = token.upper()
        mapped = ""
        if "HACCP" in upper:
            mapped = "HACCP"
        elif "GMP" in upper:
            mapped = "GMP"
        elif "FSSC" in upper:
            mapped = "FSSC22000"
        elif upper.startswith("ISO"):
            mapped = "ISO"
        elif token in {"비건", "할랄"}:
            mapped = token
        if mapped and mapped not in tokens:
            tokens.append(mapped)
    return tokens


@lru_cache(maxsize=1)
def load_factories() -> list[dict[str, str]]:
    if not SEED_PATH.is_file():
        return []
    with SEED_PATH.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def estimate_vendor_moq(base_qty: int, text: str, score: float) -> int:
    factor = 1.0 if any(word in text for word in ["소량", "샘플", "스타트업"]) else 1.3
    if any(word in text for word in ["대량", "대형", "글로벌"]):
        factor += 0.3
    if score >= 88:
        factor -= 0.1
    raw = max(base_qty, int(base_qty * factor))
    return int(round(raw / 100.0) * 100)


def estimate_lead_time(rank: int, score: float, text: str) -> int:
    days = 17 + rank * 2
    if any(word in text for word in ["소량", "샘플", "스타트업"]):
        days -= 2
    if score < 82:
        days += 2
    return max(days, 12)


def required_certifications(meta: dict[str, Any], claims: list[str], package_type: str) -> list[str]:
    certs = list(meta["required_certifications"])
    if "vegan" in claims:
        certs.append("비건")
    if package_type == "bottle" and meta["label"] == "병/캔 음료" and "FSSC22000" not in certs:
        certs.append("FSSC22000")
    return list(dict.fromkeys(certs))


def score_factory(factory: dict[str, str], meta: dict[str, Any], package_type: str, qty: int, claims: list[str]) -> tuple[float, list[str]]:
    certs = certification_tokens(factory.get("certification_signal", ""))
    source_text = " ".join(
        [
            factory.get("company_name", ""),
            factory.get("primary_category", ""),
            factory.get("product_keywords", ""),
            factory.get("notes", ""),
            factory.get("verification_status", ""),
        ]
    )
    haystack = normalize_text(source_text)
    score = 0.0
    reasons: list[str] = []

    if any(normalize_text(alias) in haystack for alias in meta["aliases"]):
        score += 34
        reasons.append("제품군 키워드 일치")
    if any(normalize_text(keyword) in haystack for keyword in PACKAGE_KEYWORDS.get(package_type, [])):
        score += 14
        reasons.append("포장 방식 대응 신호")
    if "OEM" in source_text.upper() or "ODM" in source_text.upper():
        score += 8
        reasons.append("OEM/ODM 운영 공개")
    if "HACCP" in certs:
        score += 8
        reasons.append("HACCP 확인")
    if "GMP" in certs and meta["label"] == "분말/스틱":
        score += 8
        reasons.append("분말류 GMP 적합")
    if "FSSC22000" in certs and meta["label"] == "병/캔 음료":
        score += 8
        reasons.append("음료 라인 인증 보유")
    if "비건" in certs and "vegan" in claims:
        score += 7
        reasons.append("비건 인증 신호")
    if any(word in source_text for word in ["소량", "샘플", "스타트업"]) and qty <= int(meta["min_qty"] * 1.5):
        score += 10
        reasons.append("초도 테스트 대응 가능성")
    if "공식" in factory.get("verification_status", "") or "공개정보확인" in factory.get("verification_status", ""):
        score += 5
        reasons.append("공개 출처 확인")
    if any(flag in claims for flag in ["lowSugar", "lowSodium"]) and any(word in source_text for word in ["저당", "제로슈가", "레시피", "품질"]):
        score += 4
        reasons.append("표시 문구 검토 경험 신호")
    return min(round(score, 1), 98.0), reasons


def pick_vendors(category: str, package_type: str, qty: int, claims: list[str], idea: str) -> list[dict[str, Any]]:
    meta = CATEGORY_META[category]
    picked: list[dict[str, Any]] = []
    for factory in load_factories():
        score, reasons = score_factory(factory, meta, package_type, qty, claims)
        if score < 30 or not factory.get("source_url"):
            continue
        text = " ".join([factory.get("primary_category", ""), factory.get("product_keywords", ""), factory.get("notes", "")])
        certs = certification_tokens(factory.get("certification_signal", ""))
        picked.append(
            {
                "company_name": factory.get("company_name", "후보 업체"),
                "score": score,
                "source_url": factory.get("source_url", ""),
                "summary": " · ".join(
                    compact_items(
                        [
                            factory.get("primary_category", ""),
                            factory.get("verification_status", ""),
                            ", ".join(certs[:2]) if certs else "",
                        ]
                    )
                ),
                "product_keywords": compact_items(re.split(r"[,/]", factory.get("product_keywords", "")), 4),
                "certifications": certs,
                "verification_status": factory.get("verification_status", "미확인"),
                "match_basis": reasons,
                "min_qty": estimate_vendor_moq(meta["min_qty"], text, score),
            }
        )
    picked.sort(key=lambda item: item["score"], reverse=True)
    for index, vendor in enumerate(picked[:4], start=1):
        vendor["lead_time_days"] = estimate_lead_time(index, vendor["score"], " ".join(vendor["product_keywords"]))
    return picked[:4]


def build_costs(category: str, package_type: str, qty: int, budget: int, claims: list[str]) -> dict[str, Any]:
    meta = CATEGORY_META[category]
    unit_cost = meta["base_unit_cost"] * PACKAGE_MULTIPLIER.get(package_type, 1)
    if qty >= meta["min_qty"] * 2:
        unit_cost *= 0.94
    elif qty < meta["min_qty"]:
        unit_cost *= 1.08
    unit_cost *= 1 + (0.015 * len(claims))
    supply_cost = qty * unit_cost
    sample_cost = meta["sample_cost"] + meta["test_cost"] + len(claims) * 40000
    brokerage_rate = 0.08
    brokerage_cost = (supply_cost + sample_cost) * brokerage_rate
    total_cost = supply_cost + sample_cost + brokerage_cost
    ratio = (total_cost / budget) if budget > 0 else 0
    if budget <= 0:
        status = "예산 미입력"
    elif ratio <= 0.88:
        status = "예산 여유"
    elif ratio <= 1.0:
        status = "예산내"
    elif ratio <= 1.08:
        status = "예산 근접"
    else:
        status = "예산 초과"
    drivers = [
        f"{PACKAGE_LABELS.get(package_type, package_type)} 포장 가중치 {PACKAGE_MULTIPLIER.get(package_type, 1):.2f} 적용",
        f"초도 수량 {qty:,}{meta['unit']} 기준 직접 공급가 산정",
        f"샘플비·시험비 {money_text(sample_cost)}와 중개 수수료 8% 포함",
    ]
    if claims:
        drivers.append(f"검토 문구 {', '.join(CLAIM_LABELS[item] for item in claims if item in CLAIM_LABELS)} 반영")
    return {
        "unit_cost": round(unit_cost),
        "supply_cost": round(supply_cost),
        "sample_cost": round(sample_cost),
        "brokerage_cost": round(brokerage_cost),
        "total_cost": round(total_cost),
        "budget_gap": round(budget - total_cost),
        "budget_status": status,
        "drivers": drivers,
        "brokerage_rate": brokerage_rate,
    }


def build_risks(category: str, package_type: str, qty: int, budget: int, claims: list[str], costs: dict[str, Any], vendors: list[dict[str, Any]]) -> list[dict[str, str]]:
    meta = CATEGORY_META[category]
    top_certs = {cert for vendor in vendors for cert in vendor["certifications"]}
    needed_certs = required_certifications(meta, claims, package_type)
    missing_certs = [cert for cert in needed_certs if cert not in top_certs]
    risks: list[dict[str, str]] = []

    qty_ok = qty >= meta["min_qty"]
    risks.append(
        {
            "category": "생산/MOQ 리스크",
            "severity": "green" if qty_ok else "yellow",
            "title": "초도 생산 수량 확인",
            "detail": (
                f"현재 수량은 최소 기준 {meta['min_qty']:,}{meta['unit']} 이상입니다."
                if qty_ok
                else f"현재 수량은 기준 {meta['min_qty']:,}{meta['unit']}보다 낮아 MOQ 재협의가 필요합니다."
            ),
            "action": "후보 업체에 MOQ와 샘플 배치 수량을 같은 문구로 확인하세요.",
        }
    )

    if budget <= 0:
        budget_severity = "yellow"
        budget_detail = "예산이 입력되지 않아 가격 수용 범위와 조정 여지를 판단할 수 없습니다."
    elif costs["budget_status"] == "예산 초과":
        budget_severity = "red"
        budget_detail = "예상 총액이 현재 예산을 초과해 수량, 포장, 샘플 범위 중 하나를 조정해야 합니다."
    elif costs["budget_status"] == "예산 근접":
        budget_severity = "yellow"
        budget_detail = "예산 여유가 작아 샘플 수정 1회만 있어도 추가 비용이 발생할 수 있습니다."
    else:
        budget_severity = "green"
        budget_detail = "샘플비와 수수료를 포함해도 1차 비교 견적 범위 안입니다."
    risks.append(
        {
            "category": "예산 여유범위",
            "severity": budget_severity,
            "title": "예산 완충 구간",
            "detail": budget_detail,
            "action": "총액뿐 아니라 재시험 가능성까지 포함한 상한 예산을 같이 적어 두세요.",
        }
    )

    if any(claim in claims for claim in ["lowSugar", "lowSodium", "protein", "vegan"]):
        points = []
        if "lowSugar" in claims:
            points.append("저당은 영양성분 시험 후 기준 충족 여부를 확인해야 합니다.")
        if "lowSodium" in claims:
            points.append("나트륨 감소 문구는 비교 기준과 표기 형식을 먼저 맞춰야 합니다.")
        if "protein" in claims:
            points.append("고단백 표현은 단백질 함량 산출과 표시 기준이 필요합니다.")
        if "vegan" in claims:
            points.append("비건 표현은 원료 증빙과 교차오염 관리 범위를 확인해야 합니다.")
        risks.append(
            {
                "category": "표시문구 리스크",
                "severity": "yellow",
                "title": "강조 문구 확정 보류",
                "detail": " ".join(points),
                "action": "라벨 시안 확정 전에는 시험성적서와 원료 증빙 확보 여부를 먼저 보세요.",
            }
        )

    risks.append(
        {
            "category": "법률 리스크",
            "severity": "red" if category in {"meal", "drink"} else "yellow",
            "title": "유형·보존 기준 사전 검토",
            "detail": "살균 조건, 유통기한, 식품 유형 판정은 LLM 초안이 아니라 공장과 라벨 검수 단계에서 다시 확인해야 합니다.",
            "action": "상온/냉장 기준과 표시 문안을 공장 확인 질문에 명시하세요.",
        }
    )

    risks.append(
        {
            "category": "인증 리스크",
            "severity": "green" if not missing_certs else "red" if len(missing_certs) >= 2 else "yellow",
            "title": "필수 인증 범위 점검",
            "detail": (
                f"상위 후보에서 필요한 인증 {', '.join(needed_certs)} 신호가 확인됩니다."
                if not missing_certs
                else f"현재 상위 후보에서 {', '.join(missing_certs)} 신호가 약해 인증 범위를 별도 확인해야 합니다."
            ),
            "action": "후보 페이지 방문 후 실제 보유 인증서와 적용 라인을 캡처 기준으로 확인하세요.",
        }
    )
    return risks


def fallback_process_summary(meta: dict[str, Any], claims: list[str], package_type: str) -> tuple[str, list[str]]:
    claim_text = ", ".join(CLAIM_LABELS[item] for item in claims if item in CLAIM_LABELS) or "기본 사양"
    summary = f"{meta['label']}을 {PACKAGE_LABELS.get(package_type, package_type)} 기준으로 검토했고, {claim_text} 조건 때문에 공정과 라벨 확인이 같이 필요합니다."
    notes = [stage["note"] for stage in meta["default_process"]]
    return summary, notes


def fallback_vendor_reason(vendor: dict[str, Any], meta: dict[str, Any], claims: list[str]) -> str:
    claim_text = ", ".join(CLAIM_LABELS[item] for item in claims if item in CLAIM_LABELS) or "기본 제품"
    return f"{meta['label']}과 {claim_text} 조건에서 {', '.join(vendor['match_basis'][:3]) or '기본 적합도'} 신호가 보여 1차 컨택 후보로 올렸습니다."


def fallback_price_reason(costs: dict[str, Any], meta: dict[str, Any], package_type: str, qty: int) -> dict[str, Any]:
    return {
        "summary": f"{qty:,}{meta['unit']} 기준 직접 공급가에 {PACKAGE_LABELS.get(package_type, package_type)} 포장 비용, 샘플/시험비, 수수료를 합산한 값입니다.",
        "drivers": list(costs["drivers"]),
    }


def fallback_order_summary(meta: dict[str, Any], qty: int, package_type: str, vendors: list[dict[str, Any]], costs: dict[str, Any]) -> dict[str, Any]:
    vendor_name = vendors[0]["company_name"] if vendors else "후보 업체"
    return {
        "summary": f"{meta['label']} {qty:,}{meta['unit']} 초도 발주안을 기준으로 {vendor_name}부터 MOQ, 샘플비, 표시 문구 검수 범위를 확인하는 순서입니다.",
        "checks": [
            f"희망 포장: {PACKAGE_LABELS.get(package_type, package_type)}",
            f"예상 단가: {money_text(costs['unit_cost'])}",
            f"예상 총액: {money_text(costs['total_cost'])}",
        ],
    }


def call_llm_bundle(idea: str, category: str, package_type: str, qty: int, claims: list[str], vendors: list[dict[str, Any]], costs: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    if not SAM_API_KEY:
        return None, "SAM_API_KEY 없음"

    meta = CATEGORY_META[category]
    prompt = {
        "idea": idea,
        "category": meta["label"],
        "package_type": PACKAGE_LABELS.get(package_type, package_type),
        "qty": f"{qty:,}{meta['unit']}",
        "claims": [CLAIM_LABELS[item] for item in claims if item in CLAIM_LABELS],
        "process_names": [stage["name"] for stage in meta["default_process"]],
        "top_vendors": [
            {
                "company_name": vendor["company_name"],
                "score": vendor["score"],
                "basis": vendor["match_basis"][:3],
                "certifications": vendor["certifications"][:3],
            }
            for vendor in vendors[:4]
        ],
        "price_basis": {
            "unit_cost": money_text(costs["unit_cost"]),
            "sample_cost": money_text(costs["sample_cost"]),
            "brokerage_cost": money_text(costs["brokerage_cost"]),
            "total_cost": money_text(costs["total_cost"]),
            "drivers": costs["drivers"][:4],
        },
    }
    schema_hint = {
        "process_banner_note": "string",
        "process_stage_notes": ["string"],
        "vendor_reasons": [{"company_name": "string", "match_reason": "string"}],
        "price_reason": {"summary": "string", "drivers": ["string"]},
        "order_draft": {"summary": "string", "checks": ["string"]},
    }
    body = {
        "model": SAM_MODEL,
        "stream": False,
        "temperature": 0.2,
        "max_tokens": 900,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": "너는 한국 식품 OEM/ODM 발주 근거를 짧고 명확하게 정리하는 전문가다. 법률 확정 판단을 하지 말고 검토 포인트만 설명한다.",
            },
            {
                "role": "user",
                "content": (
                    "랜딩 페이지용 식품 OEM/ODM 결과를 JSON으로 작성해라. "
                    "업체 근거는 1~2문장, 공정 설명은 쉬운 한국어, 가격 근거는 수량·포장·샘플·수수료 중심으로 적는다. "
                    f"입력: {json.dumps(prompt, ensure_ascii=False)} "
                    f"출력 스키마: {json.dumps(schema_hint, ensure_ascii=False)}"
                ),
            },
        ],
    }
    request_obj = Request(
        f"{SAM_BASE_URL}/openai/v1/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {SAM_API_KEY}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urlopen(request_obj, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
        parsed = json.loads(content) if isinstance(content, str) else content
        if not isinstance(parsed, dict):
            return None, "JSON 객체 아님"
        return parsed, "ok"
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def build_response(payload: dict[str, Any]) -> dict[str, Any]:
    category = payload["category"]
    package_type = payload["package_type"]
    qty = payload["qty"]
    budget = payload["budget"]
    claims = payload["claims"]
    idea = payload["idea"]
    meta = CATEGORY_META[category]
    vendors = pick_vendors(category, package_type, qty, claims, idea)
    costs = build_costs(category, package_type, qty, budget, claims)
    risks = build_risks(category, package_type, qty, budget, claims, costs, vendors)
    llm_bundle, llm_error = call_llm_bundle(idea, category, package_type, qty, claims, vendors, costs)

    process_summary, process_notes = fallback_process_summary(meta, claims, package_type)
    price_reason = fallback_price_reason(costs, meta, package_type, qty)
    order_draft = fallback_order_summary(meta, qty, package_type, vendors, costs)
    llm_mode = "fallback"
    llm_reasons: dict[str, str] = {}
    if llm_bundle:
        llm_mode = "llm"
        process_summary = str(llm_bundle.get("process_banner_note") or process_summary)
        stage_notes = llm_bundle.get("process_stage_notes")
        if isinstance(stage_notes, list) and stage_notes:
            process_notes = [str(item) for item in stage_notes][: len(meta["default_process"])]
        llm_price = llm_bundle.get("price_reason")
        if isinstance(llm_price, dict):
            price_reason = {
                "summary": str(llm_price.get("summary") or price_reason["summary"]),
                "drivers": [str(item) for item in llm_price.get("drivers", [])][:4] or price_reason["drivers"],
            }
        llm_order = llm_bundle.get("order_draft")
        if isinstance(llm_order, dict):
            order_draft = {
                "summary": str(llm_order.get("summary") or order_draft["summary"]),
                "checks": [str(item) for item in llm_order.get("checks", [])][:4] or order_draft["checks"],
            }
        llm_reasons = {
            str(item.get("company_name")): str(item.get("match_reason"))
            for item in llm_bundle.get("vendor_reasons", [])
            if isinstance(item, dict)
        }

    for vendor in vendors:
        vendor["llm_reason"] = llm_reasons.get(vendor["company_name"]) or fallback_vendor_reason(vendor, meta, claims)

    process_lines = [
        {
            "order": index + 1,
            "name": stage["name"],
            "summary": process_notes[index] if index < len(process_notes) else stage["note"],
        }
        for index, stage in enumerate(meta["default_process"])
    ]

    execution_steps = [
        {
            "title": "발주안 초안 확정",
            "detail": f"{meta['label']} {qty:,}{meta['unit']} 기준으로 제품 개요, 포장, 강조 문구를 한 장으로 고정합니다.",
        },
        {
            "title": "상위 후보 1차 문의",
            "detail": "기업 페이지에서 인증 범위를 확인한 뒤 MOQ, 샘플비, 리드타임, 표시 검수 범위를 같은 질문으로 보냅니다.",
        },
        {
            "title": "가격/리스크 비교",
            "detail": "총액과 단가뿐 아니라 법률·인증·표시 문구 리스크 보완 항목을 함께 비교합니다.",
        },
        {
            "title": "샘플 후 발주 전환",
            "detail": "샘플 피드백, 시험성적서, 라벨 검수 범위를 반영해 반복 발주 조건으로 전환합니다.",
        },
    ]

    return {
        "model": {
            "mode": llm_mode,
            "note": "AI 보강 초안 사용" if llm_mode == "llm" else f"AI 미연결로 규칙 기반 폴백 사용 ({llm_error})",
        },
        "title": f"{meta['label']} 발주안 초안",
        "decision_badge": "수정 필요" if any(item["severity"] == "red" for item in risks) else "전송 가능",
        "fit_pill": costs["budget_status"],
        "costs": costs,
        "claims_text": ", ".join(CLAIM_LABELS[item] for item in claims if item in CLAIM_LABELS) or "강조 문구 없음",
        "idea": idea,
        "package_label": PACKAGE_LABELS.get(package_type, package_type),
        "process_banner": {
            "headline": "LLM 공정 초안" if llm_mode == "llm" else "공정 초안",
            "chain": " → ".join(stage["name"] for stage in meta["default_process"]),
            "summary": process_summary,
        },
        "process_lines": process_lines,
        "vendors": vendors,
        "risks": risks,
        "price_reason": price_reason,
        "order_draft": order_draft,
        "execution_steps": execution_steps,
        "checks": meta["checkpoints"],
    }


def parse_request_payload(body: dict[str, Any]) -> dict[str, Any]:
    category = str(body.get("category") or "sauce")
    if category not in CATEGORY_META:
        category = "sauce"
    package_type = str(body.get("package_type") or "pouch")
    if package_type not in PACKAGE_LABELS:
        package_type = "pouch"
    raw_claims = body.get("claims", [])
    claims = raw_claims if isinstance(raw_claims, list) else []
    return {
        "idea": str(body.get("idea") or "").strip() or "초도 샘플 테스트를 위한 식품 OEM/ODM 발주안을 검토하고 싶다.",
        "category": category,
        "package_type": package_type,
        "qty": max(100, safe_int(body.get("qty"), CATEGORY_META[category]["min_qty"])),
        "budget": max(0, safe_int(body.get("budget"), 0)),
        "claims": [item for item in claims if item in CLAIM_LABELS],
    }


@app.get("/")
def index():
    return send_from_directory(str(PAGE_DIR), "index.html")


@app.get("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "page_dir": str(PAGE_DIR),
            "seed_path": str(SEED_PATH),
            "factory_count": len(load_factories()),
            "sam_enabled": bool(SAM_API_KEY),
            "job_store": str(JOB_DB_PATH),
            "max_workers": SIMULATE_MAX_WORKERS,
        }
    )


@app.get("/assets/<path:filename>")
def assets(filename: str):
    asset_path = ASSET_DIR / filename
    if not asset_path.is_file():
        abort(404)
    return send_from_directory(str(ASSET_DIR), filename)


@app.route("/api/simulate", methods=["POST", "OPTIONS"])
def simulate():
    if request.method == "OPTIONS":
        return ("", 204)
    body = request.get_json(silent=True) or {}
    payload = parse_request_payload(body)
    user_id = resolve_user_id(body)
    return jsonify(create_simulation_job(user_id, payload)), 202


@app.get("/api/simulate/<job_id>")
def simulate_job(job_id: str):
    user_id = normalize_user_id(request.args.get("user_id"))
    if not user_id:
        abort(400, description="user_id is required")
    row = get_simulation_job(job_id, user_id)
    if row is None:
        abort(404)
    status_code = 200 if row["status"] in {"completed", "failed"} else 202
    return jsonify(serialize_simulation_job(row)), status_code


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8002))
    app.run(host="0.0.0.0", port=port, debug=False)
