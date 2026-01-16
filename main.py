from dataclasses import replace

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from typing import Any, List, Dict, Optional

from db import SessionLocal, init_db
from models import Bucket, User, Item
from auth import get_current_user, create_access_token, verify_user
from schemas import BatchIn, ItemUpdate

from search_engine import (
    Material,
    out_to_mean_x_range,
    search_bucket_all_plans,
    search_bucket_all_plans_with_crate_ratio,
)

from search import (
    search_bucket_all_plans_with_crate_ratio1,
)


from pydantic import BaseModel

app = FastAPI()
templates = Jinja2Templates(directory="templates")


@app.on_event("startup")
def startup():
    init_db()


# -------------------------
# 页面：登录页 & App 首页
# -------------------------

@app.get("/", response_class=HTMLResponse)
def root():
    return HTMLResponse('<script>window.location.href="/login_page"</script>')


@app.get("/login_page", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/app", response_class=HTMLResponse)
def app_page(request: Request):
    return templates.TemplateResponse("app.html", {"request": request})


@app.get("/ingest", response_class=HTMLResponse)
def ingest_page(request: Request):
    return templates.TemplateResponse("ingest.html", {"request": request})


@app.get("/search_page", response_class=HTMLResponse)
def search_page(request: Request):
    # 原搜索页面（保持不动）
    return templates.TemplateResponse("search.html", {"request": request})


@app.get("/search_ratio_page", response_class=HTMLResponse)
def search_ratio_page(request: Request):
    # 新增：按箱子比例搜索页面（独立）
    return templates.TemplateResponse("search_ratio.html", {"request": request})


# -------------------------
# Auth：登录接口
# -------------------------

@app.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends()):
    ok = verify_user(form.username, form.password)
    if not ok:
        raise HTTPException(status_code=400, detail="Incorrect username or password")

    token = create_access_token(form.username)
    return {"access_token": token, "token_type": "bearer"}


# -------------------------
# 初始化用户（仅本地用，建议后续删掉或加保护）
# -------------------------

@app.post("/init_user")
def init_user(username: str, password: str):
    db = SessionLocal()
    existed = db.query(User).filter(User.username == username).first()
    if existed:
        db.close()
        raise HTTPException(status_code=400, detail="user already exists")

    from auth import pwd_context
    hashed = pwd_context.hash(password)

    user = User(username=username, password_hash=hashed)
    db.add(user)
    db.commit()
    db.close()
    return {"msg": "user created"}


# -------------------------
# Buckets：受保护的接口
# -------------------------

@app.post("/buckets")
def create_bucket(name: str, user: User = Depends(get_current_user)):
    db = SessionLocal()
    existed = db.query(Bucket).filter(Bucket.name == name).first()
    if existed:
        db.close()
        raise HTTPException(status_code=400, detail="bucket already exists")

    bucket = Bucket(name=name)
    db.add(bucket)
    db.commit()
    db.refresh(bucket)
    db.close()
    return {"id": bucket.id, "name": bucket.name}


@app.get("/buckets")
def list_buckets(user: User = Depends(get_current_user)):
    db = SessionLocal()
    buckets = db.query(Bucket).all()
    db.close()
    return [{"id": b.id, "name": b.name} for b in buckets]


@app.delete("/buckets/{bucket_id}")
def delete_bucket(bucket_id: int, user: User = Depends(get_current_user)):
    db = SessionLocal()
    bucket = db.query(Bucket).filter(Bucket.id == bucket_id).first()
    if not bucket:
        db.close()
        raise HTTPException(status_code=404, detail="bucket not found")
    db.query(Item).filter(Item.bucket_id == bucket_id).delete(synchronize_session=False)
    db.delete(bucket)
    db.commit()
    db.close()
    return {"deleted": True}


# -------------------------
# Items：受保护的接口
# -------------------------

@app.get("/items")
def list_items(bucket_id: int, user: User = Depends(get_current_user)):
    db = SessionLocal()
    items = db.query(Item).filter(Item.bucket_id == bucket_id).order_by(Item.id.asc()).all()
    bucket_index_map = {it.id: idx + 1 for idx, it in enumerate(items)}
    items = sorted(items, key=lambda it: it.id, reverse=True)
    db.close()

    return [
        {
            "id": it.id,
            "bucket_index": bucket_index_map.get(it.id, 0),
            "bucket_id": it.bucket_id,
            "name": it.name,
            "rarity": it.rarity,
            "crate": it.crate,
            "in_min": it.in_min,
            "in_max": it.in_max,
            "float_value": it.float_value,
            "x_value": it.x_value,
        }
        for it in items
    ]


@app.patch("/items/{item_id}")
def update_item(item_id: int, payload: ItemUpdate, user: User = Depends(get_current_user)):
    db = SessionLocal()
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        db.close()
        raise HTTPException(status_code=404, detail="item not found")

    in_min = payload.in_min if payload.in_min is not None else item.in_min
    in_max = payload.in_max if payload.in_max is not None else item.in_max
    float_value = payload.float_value if payload.float_value is not None else item.float_value

    if in_max <= in_min:
        db.close()
        raise HTTPException(status_code=400, detail="in_max must be greater than in_min")
    if not (in_min <= float_value <= in_max):
        db.close()
        raise HTTPException(status_code=400, detail="float_value out of range")

    if payload.name is not None:
        item.name = payload.name
    if payload.rarity is not None:
        item.rarity = payload.rarity
    if payload.crate is not None:
        item.crate = payload.crate
    item.in_min = in_min
    item.in_max = in_max
    item.float_value = float_value
    item.x_value = (float_value - in_min) / (in_max - in_min) if in_max > in_min else 0.0

    db.commit()
    db.refresh(item)
    db.close()

    return {
        "id": item.id,
        "bucket_id": item.bucket_id,
        "name": item.name,
        "rarity": item.rarity,
        "crate": item.crate,
        "in_min": item.in_min,
        "in_max": item.in_max,
        "float_value": item.float_value,
        "x_value": item.x_value,
    }


@app.delete("/items/{item_id}")
def delete_item(item_id: int, user: User = Depends(get_current_user)):
    db = SessionLocal()
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        db.close()
        raise HTTPException(status_code=404, detail="item not found")
    db.delete(item)
    db.commit()
    db.close()
    return {"deleted": True}


@app.post("/items/batch")
def create_items_batch(payload: BatchIn, user: User = Depends(get_current_user)):
    db = SessionLocal()

    bucket = db.query(Bucket).filter(Bucket.id == payload.bucket_id).first()
    if not bucket:
        db.close()
        raise HTTPException(status_code=400, detail="bucket not found")

    created = 0
    for it in payload.items:
        if not (it.in_min <= it.float_value <= it.in_max):
            db.close()
            raise HTTPException(status_code=400, detail=f"float_value out of range: {it.float_value}")

        x = (it.float_value - it.in_min) / (it.in_max - it.in_min) if it.in_max > it.in_min else 0.0

        row = Item(
            bucket_id=payload.bucket_id,
            name=it.name,
            rarity=it.rarity,
            crate=it.crate,
            in_min=it.in_min,
            in_max=it.in_max,
            float_value=it.float_value,
            x_value=x,
        )
        db.add(row)
        created += 1

    db.commit()
    db.close()
    return {"created": created}


# -------------------------
# 受保护测试接口
# -------------------------

@app.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"username": user.username}


# -------------------------
# 搜索：普通版（原来的）
# -------------------------

class ProductSlot(BaseModel):
    out_min: float
    out_max: float
    target_low: float
    target_high: float

class MultiSearchRequest(BaseModel):
    bucket_id: int
    products: List[ProductSlot]


def _db_items_to_materials(items: List[Item]) -> List[Material]:
    bucket_index_map = {it.id: idx + 1 for idx, it in enumerate(sorted(items, key=lambda it: it.id))}
    materials: List[Material] = []
    for it in items:
        in_min = float(it.in_min)
        in_max = float(it.in_max)
        in_float = float(it.float_value)
        den = (in_max - in_min)
        x = 0.0 if abs(den) < 1e-12 else (in_float - in_min) / den
        bucket_index = bucket_index_map.get(it.id, 0)

        materials.append(Material(
            id=int(it.id),
            name=(it.name or f"item#{bucket_index or it.id}"),
            crate=(it.crate or ""),
            rarity=int(getattr(it, "rarity", 1) or 1),
            in_min=in_min,
            in_max=in_max,
            in_float=in_float,
            x=x
        ))
    return materials


def _material_to_dict(m: Material) -> Dict[str, Any]:
    return {
        "id": m.id,
        "name": m.name,
        "crate": m.crate,
        "rarity": m.rarity,
        "in_min": m.in_min,
        "in_max": m.in_max,
        "in_float": m.in_float,
        "x": m.x,
    }


def _unused_materials(plans: List[dict], materials: List[Material]) -> List[dict]:
    if isinstance(plans, tuple) and len(plans) > 0 and isinstance(plans[0], list):
        plans = plans[0]
    used_ids = {m["id"] for plan in plans for m in plan.get("materials", [])}
    return [_material_to_dict(m) for m in materials if m.id not in used_ids]


@app.post("/search")
def search(req: MultiSearchRequest, user: User = Depends(get_current_user)):
    bucket_id = req.bucket_id
    slots = req.products[:10]

    db = SessionLocal()
    items = db.query(Item).filter(Item.bucket_id == bucket_id).all()
    db.close()

    materials = _db_items_to_materials(items)

    slot_ranges = []
    for idx, s in enumerate(slots):
        tlo = min(s.target_low, s.target_high)
        thi = max(s.target_low, s.target_high)
        L, U = out_to_mean_x_range(s.out_min, s.out_max, tlo, thi)
        slot_ranges.append({
            "slot_index": idx,
            "out_min": s.out_min,
            "out_max": s.out_max,
            "target_low": tlo,
            "target_high": thi,
            "L": L,
            "U": U,
        })

    L_all = max(r["L"] for r in slot_ranges)
    U_all = min(r["U"] for r in slot_ranges)

    # 左闭右开：交集为空用 >=
    if L_all >= U_all:
        return {
            "input_slots": len(slots),
            "joint_mean_x_range": {"L": L_all, "U": U_all},
            "slot_ranges": slot_ranges,
            "plans": [],
            "unused_materials": [_material_to_dict(m) for m in materials],
            "notes": ["无解：mean(x) 交集为空（左闭右开）。"]
        }

    has_crates = any(m.crate for m in materials)
    if has_crates:
        plans = search_bucket_all_plans(
            materials,
            slot_ranges,
            L_all,
            U_all,
            cap=40,
            max_combo_count=2_000_000,
            right_open=True,
        )
        notes = [
            "普通搜索：只要求磨损满足；找到方案后会排除用料继续搜（提高利用率）。",
            "边界：左闭右开（target_low <= out < target_high）。"
        ]
    else:
        none_crate = "none"
        materials = [replace(m, crate=none_crate) for m in materials]
        plans, _, _ = search_bucket_all_plans_with_crate_ratio1(
            materials,
            slot_ranges,
            L_all,
            U_all,
            crate_weights={none_crate: 1.0},
            crate_order=[none_crate],
            cap=40,
            max_combo_count=2_000_000,
            right_open=True,
        )
        notes = [
            "普通搜索：桶内无箱子数据，使用概率搜索算法（视为 none 箱子填 10）。",
            "边界：左闭右开（target_low <= out < target_high）。"
        ]

    return {
        "input_slots": len(slots),
        "joint_mean_x_range": {"L": L_all, "U": U_all},
        "slot_ranges": slot_ranges,
        "plans": plans,
        "unused_materials": _unused_materials(plans, materials),
        "notes": notes,
    }


# -------------------------
# 新增：按箱子比例（概率）搜索
# -------------------------

class RatioSearchRequest(BaseModel):
    bucket_id: int
    products: List[ProductSlot]
    crate_weights: Dict[str, float]


@app.get("/bucket_crates")
def bucket_crates(bucket_id: int, user: User = Depends(get_current_user)):
    """
    返回桶里有哪些箱子以及数量，用于前端渲染 x1:x2:... 的权重输入。
    """
    db = SessionLocal()
    items = db.query(Item).filter(Item.bucket_id == bucket_id).all()
    db.close()

    counts: Dict[str, int] = {}
    for it in items:
        c = it.crate or ""
        counts[c] = counts.get(c, 0) + 1

    crate_list = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "bucket_id": bucket_id,
        "crates": [{"crate": k, "count": v} for k, v in crate_list],
    }


@app.post("/search_ratio")
def search_ratio(req: RatioSearchRequest, user: User = Depends(get_current_user)):
    bucket_id = req.bucket_id
    slots = req.products[:10]
    crate_weights = req.crate_weights or {}

    db = SessionLocal()
    items = db.query(Item).filter(Item.bucket_id == bucket_id).all()
    db.close()

    materials = _db_items_to_materials(items)

    slot_ranges = []
    for idx, s in enumerate(slots):
        tlo = min(s.target_low, s.target_high)
        thi = max(s.target_low, s.target_high)
        L, U = out_to_mean_x_range(s.out_min, s.out_max, tlo, thi)
        slot_ranges.append({
            "slot_index": idx,
            "out_min": s.out_min,
            "out_max": s.out_max,
            "target_low": tlo,
            "target_high": thi,
            "L": L,
            "U": U,
        })

    L_all = max(r["L"] for r in slot_ranges)
    U_all = min(r["U"] for r in slot_ranges)

    if L_all >= U_all:
        return {
            "input_slots": len(slots),
            "joint_mean_x_range": {"L": L_all, "U": U_all},
            "slot_ranges": slot_ranges,
            "crate_order": [],
            "crate_target_counts": {},
            "plans": [],
            "unused_materials": [_material_to_dict(m) for m in materials],
            "notes": ["无解：mean(x) 交集为空（左闭右开）。"]
        }

    # crate order = 按桶内出现数量从高到低（与 /bucket_crates 一致）
    counts: Dict[str, int] = {}
    for it in items:
        c = it.crate or ""
        counts[c] = counts.get(c, 0) + 1
    crate_order = [k for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    plans, target_counts, crate_order2 = search_bucket_all_plans_with_crate_ratio(
        materials,
        slot_ranges,
        L_all,
        U_all,
        crate_weights=crate_weights,
        crate_order=crate_order,
        cap=40,
        max_combo_count=2_000_000,
        right_open=True,
    )

    return {
        "input_slots": len(slots),
        "joint_mean_x_range": {"L": L_all, "U": U_all},
        "slot_ranges": slot_ranges,
        "crate_order": crate_order2,
        "crate_target_counts": target_counts,
        "plans": plans,
        "unused_materials": _unused_materials(plans, materials),
        "notes": [
            "概率搜索：只返回箱子数量比例严格符合你填写权重的方案。",
            "评分：在比例严格匹配前提下，最小化 sum(x) 偏离中心。",
            "边界：左闭右开（target_low <= out < target_high）。",
        ]
    }
