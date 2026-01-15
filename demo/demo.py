import itertools
import random
from dataclasses import dataclass
from typing import List, Dict, Tuple, Any, Optional

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field


# ----------------------------
# 数据结构
# ----------------------------

@dataclass(frozen=True)
class Material:
    id: int
    name: str
    crate: str
    rarity: int  # 1~5
    in_min: float
    in_max: float
    in_float: float
    x: float  # 归一化磨损


# ----------------------------
# 模拟数据
# ----------------------------

IN_BOUNDS_POOL = [
    (0.00, 0.07),
    (0.00, 0.15),
    (0.00, 0.38),
    (0.00, 0.45),
    (0.00, 1.00),
    (0.06, 0.80),
    (0.15, 0.38),
    (0.38, 0.45),
    (0.45, 1.00),
]

def gen_bucket_materials(n: int = 260, crates: int = 8) -> List[Material]:
    random.seed(7)
    mats: List[Material] = []
    crate_names = [f"箱子{c+1}" for c in range(crates)]

    for i in range(n):
        crate = random.choice(crate_names)
        rarity = random.randint(1, 5)  # 同稀有度约束会用到
        in_min, in_max = random.choice(IN_BOUNDS_POOL)

        u = random.random()
        beta_like = (u + random.random()) / 2.0
        in_float = in_min + beta_like * (in_max - in_min)

        if in_max - in_min < 1e-12:
            x = 0.0
        else:
            x = (in_float - in_min) / (in_max - in_min)

        mats.append(Material(
            id=i + 1,
            name=f"材料{i+1:03d}",
            crate=crate,
            rarity=rarity,
            in_min=in_min,
            in_max=in_max,
            in_float=in_float,
            x=x
        ))
    return mats


# BUCKET = gen_bucket_materials()
TEST_FLOATS = [
    0.09561502188444138, #1
    0.09976580739021301, #2
    0.09510505199432373, #3
    0.007718190550804138, #4
    0.009712093509733677, #5
    0.009986266493707302, #6
    0.009429402649402618, #7
    0.009770151227712631, #8
    0.10083526372909546, #9
    0.09341467171907425, #10
    0.08617065846920013, #11
    0.09766266494989395, #12
    0.008814231492578983, #13
    0.0083333055302500725, #14
    0.008747153915464878, #15
    0.008830181322991848, #16
    0.0935407429933548, #17
    0.00991738960146904, #18
    0.008872182108461857, #19
    0.09987716376781464, #20
    0.10947659611701965, #21
    0.10842043161392212, #22
    0.0863686129450798, #23
    0.08313878625631332, #24
    0.00997113436460495, #25
    0.09404443204402924, #26
]


# 11 12 18 19 29 21 22 24 25 26

# 6 5 4 3 2 1 17 26 10 9
BUCKET = []
for i, f in enumerate(TEST_FLOATS):
    in_min = 0.0
    in_max = 1.0
    x = f  # 因为 min=0,max=1

    BUCKET.append(Material(
        id=i + 1,
        name=f"测试材料{i+1:02d}",
        crate="测试箱子",
        rarity=1,          # 全部同稀有度，方便验证
        in_min=in_min,
        in_max=in_max,
        in_float=f,
        x=x
    ))

# ----------------------------
# 磨损公式：out = out_min + mean(x) * (out_max - out_min)
# ----------------------------

def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

def out_to_mean_x_range(out_min: float, out_max: float, target_low: float, target_high: float, eps: float = 1e-12) -> Tuple[float, float]:
    if out_max - out_min < eps:
        return (1.0, 0.0)  # 无解
    L = (target_low - out_min) / (out_max - out_min)
    U = (target_high - out_min) / (out_max - out_min)
    L, U = min(L, U), max(L, U)
    return (clamp(L, 0.0, 1.0), clamp(U, 0.0, 1.0))

def calc_out_float(out_min: float, out_max: float, mean_x: float) -> float:
    return out_min + mean_x * (out_max - out_min)


# ----------------------------
# 预计算：每个 rarity 一次
# ----------------------------

@dataclass
class Precomp:
    rarity: int
    mats: List[Material]  # 候选材料池（已裁剪）
    combos5: List[Tuple[float, int, Tuple[int, ...]]]  # (sum, mask, idxs) sorted by sum
    combo_sums: List[float]


def pick_candidates_for_rarity(all_mats: List[Material], cap: int = 30) -> List[Material]:
    """
    候选池裁剪策略（跟产物无关，按 rarity 固定一次）：
    - 用 x 的中位数作为中心，取离中心最近的 cap
    - 同时补一些边缘，避免组合空间太“集中”
    """
    mats = all_mats[:]
    mats.sort(key=lambda m: m.x)
    if not mats:
        return []
    center = mats[len(mats)//2].x

    by_center = sorted(mats, key=lambda m: abs(m.x - center))
    primary = by_center[:cap]

    edge_k = max(4, cap // 6)
    low_edge = mats[:edge_k]
    high_edge = list(reversed(mats[-edge_k:]))

    seen = set()
    out: List[Material] = []
    for m in primary + low_edge + high_edge:
        if m.id not in seen:
            out.append(m)
            seen.add(m.id)
    return out[:cap]


def bisect_left(a: List[float], x: float) -> int:
    lo, hi = 0, len(a)
    while lo < hi:
        mid = (lo + hi) // 2
        if a[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


def build_precomp_for_rarity(rarity: int, mats: List[Material]) -> Optional[Precomp]:
    cand = pick_candidates_for_rarity(mats, cap=30)
    if len(cand) < 10:
        return None

    # 固定顺序：让 bitmask 稳定（0..len(cand)-1）
    cand = sorted(cand, key=lambda m: m.x)

    combos5 = []
    n = len(cand)
    for idxs in itertools.combinations(range(n), 5):
        s = 0.0
        mask = 0
        for i in idxs:
            s += cand[i].x
            mask |= (1 << i)
        combos5.append((s, mask, idxs))

    combos5.sort(key=lambda t: t[0])
    combo_sums = [t[0] for t in combos5]

    return Precomp(
        rarity=rarity,
        mats=cand,
        combos5=combos5,
        combo_sums=combo_sums
    )



PRECOMP_BY_RARITY: Dict[int, Precomp] = {}
for r in range(1, 6):
    mats_r = [m for m in BUCKET if m.rarity == r]
    pc = build_precomp_for_rarity(r, mats_r)
    if pc:
        PRECOMP_BY_RARITY[r] = pc


# ----------------------------
# 查询：对每个槽位做区间查询（复用预计算）
# ----------------------------
def query_best_plan(pre: Precomp, sum_lo: float, sum_hi: float, used_mask: int, eps: float = 1e-9):
    """
    返回 (score, total_sum_x, chosen_materials[10], plan_mask) 或 None
    plan_mask = maskL | maskR（用于更新 used_mask）
    """
    center = (sum_lo + sum_hi) / 2.0
    combos = pre.combos5
    sums = pre.combo_sums

    best = None  # (score, total, chosen, plan_mask)

    # left 仍然按接近 center/2 优先（更容易快速找到可行方案）
    left_sorted = sorted(combos, key=lambda t: abs(t[0] - center/2))
    left_take = left_sorted[: min(25000, len(left_sorted))]

    for sL, maskL, idxL in left_take:
        if (maskL & used_mask) != 0:
            continue

        need_lo = sum_lo - sL
        need_hi = sum_hi - sL
        j = bisect_left(sums, need_lo - eps)

        scanned = 0
        scan_limit = 2500  # 桶大时你可以调小一点
        while j < len(combos) and sums[j] <= need_hi + eps and scanned < scan_limit:
            sR, maskR, idxR = combos[j]
            if (maskR & used_mask) == 0 and (maskL & maskR) == 0:
                total = sL + sR
                if sum_lo - eps <= total <= sum_hi + eps:
                    plan_mask = maskL | maskR
                    chosen_idxs = tuple(sorted(idxL + idxR))
                    chosen = [pre.mats[i] for i in chosen_idxs]
                    chosen = sorted(chosen, key=lambda m: m.id)

                    score = abs(total - center)
                    cand = (score, total, chosen, plan_mask)
                    if best is None or score < best[0]:
                        best = cand

                        # 这里可以加一个“足够好就提前退出”的阈值（可选）
                        # if score < 1e-6: return best
            j += 1
            scanned += 1

    return best






# ----------------------------
# API：支持 1~10 个槽位
# ----------------------------

app = FastAPI(title="CS 汰换磨损 Demo (10 Slots)")

class ProductSlot(BaseModel):
    out_min: float = Field(..., description="产物最低磨损")
    out_max: float = Field(..., description="产物最高磨损")
    target_low: float = Field(..., description="期望产物磨损下界")
    target_high: float = Field(..., description="期望产物磨损上界")

class MultiSearchRequest(BaseModel):
    products: List[ProductSlot] = Field(..., min_length=1, max_length=10)
    top_k: int = Field(5, ge=1, le=20)


@app.get("/", response_class=HTMLResponse)
def index():
    return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>CS 汰换磨损 Demo（10 槽位）</title>
  <style>
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial; margin:24px; color:#111;}
    h2{margin:0 0 10px 0;}
    .hint{color:#555; margin:0 0 16px 0; line-height:1.35;}
    .panel{border:1px solid #e5e5e5; border-radius:12px; padding:14px; margin:14px 0;}
    .grid{display:grid; grid-template-columns: 120px 120px 120px 120px 1fr; gap:10px; align-items:center;}
    .grid .hdr{font-size:12px; color:#666; font-weight:600;}
    input{width:110px; padding:6px 8px; border:1px solid #dcdcdc; border-radius:8px;}
    input:disabled{background:#f7f7f7;}
    button{padding:8px 12px; border:1px solid #ddd; border-radius:10px; background:#fff; cursor:pointer;}
    button:hover{background:#f6f6f6;}
    .row{display:contents;}
    .rownum{font-size:12px; color:#777;}
    .actions{display:flex; gap:10px; align-items:center; margin-top:10px;}
    .msg{margin-top:10px; color:#444;}
    .slot{border:1px solid #ededed; border-radius:12px; padding:12px; margin:12px 0;}
    .slot h3{margin:0 0 8px 0; font-size:16px;}
    .sub{color:#666; font-size:12px; margin:0 0 10px 0;}
    table{width:100%; border-collapse:collapse; margin:8px 0 0 0;}
    th,td{border-top:1px solid #efefef; padding:8px 6px; text-align:left; font-size:13px;}
    th{color:#555; font-weight:700; font-size:12px;}
    .pill{display:inline-block; padding:2px 8px; border-radius:999px; background:#f3f3f3; font-size:12px; color:#333; margin-left:8px;}
    .plan{margin-top:10px; padding:10px; border:1px solid #f0f0f0; border-radius:12px; background:#fafafa;}
    .planTitle{display:flex; gap:10px; align-items:center; justify-content:space-between;}
    .kpi{font-size:12px; color:#333;}
    .kpi code{background:#fff; padding:2px 6px; border-radius:8px; border:1px solid #eee;}
    .muted{color:#777; font-size:12px;}
    .err{color:#b00020; white-space:pre-wrap;}
  </style>
</head>
<body>
  <h2>CS 汰换磨损 Demo（10 个产物槽位）</h2>
  <p class="hint">
    规则：同稀有度（材料 rarity 必须一致），允许混箱。<br/>
    你最多填 10 条产物限制；只填 1 条也能搜索。输出仅展示“配料方案”，每件材料标明磨损与上下限。
  </p>

  <div class="panel">
    <div class="grid" id="grid">
      <div class="hdr">槽位</div>
      <div class="hdr">out_min</div>
      <div class="hdr">out_max</div>
      <div class="hdr">期望 low</div>
      <div class="hdr">期望 high</div>
    </div>

    <div class="actions">
      <button onclick="addRow()">+ 增加槽位</button>
      <button onclick="removeRow()">- 删除最后一行</button>
      <label class="muted">Top-K 方案：</label>
      <input id="topk" value="5"/>
      <button onclick="run()">搜索方案</button>
      <span class="muted" id="meta"></span>
    </div>

    <div class="msg" id="msg"></div>
  </div>

  <div id="out"></div>

<script>
let rows = 1;

function rowTemplate(i, preset){
  const v = preset || {out_min:"0.00", out_max:"1.00", target_low:"0.0699", target_high:"0.070"};
  return `
    <div class="row">
      <div class="rownum">#${i+1}</div>
      <input id="out_min_${i}" value="${v.out_min}">
      <input id="out_max_${i}" value="${v.out_max}">
      <input id="target_low_${i}" value="${v.target_low}">
      <input id="target_high_${i}" value="${v.target_high}">
    </div>`;
}

function render(){
  const grid = document.getElementById("grid");
  // reset header
  grid.innerHTML = `
    <div class="hdr">槽位</div>
    <div class="hdr">out_min</div>
    <div class="hdr">out_max</div>
    <div class="hdr">期望 low</div>
    <div class="hdr">期望 high</div>
  `;
  for(let i=0;i<rows;i++){
    // 给前两行一点不同默认值，方便你立刻看到多槽位效果
    const preset = (i===0)
      ? {out_min:"0.00", out_max:"1.00", target_low:"0.0699", target_high:"0.070"}
      : (i===1)
        ? {out_min:"0.00", out_max:"0.45", target_low:"0.10", target_high:"0.18"}
        : {out_min:"0.00", out_max:"1.00", target_low:"0.0699", target_high:"0.070"};
    grid.insertAdjacentHTML("beforeend", rowTemplate(i, preset));
  }
}
function addRow(){ if(rows<10){ rows++; render(); } }
function removeRow(){ if(rows>1){ rows--; render(); } }

function fmt(n, d=6){
  const x = Number(n);
  if(Number.isNaN(x)) return String(n);
  return x.toFixed(d);
}

function cratesText(crates){
  const parts = Object.entries(crates).sort((a,b)=>b[1]-a[1]).map(([k,v])=>`${k}:${v}`);
  return parts.join("  ");
}

async function run(){
  const out = document.getElementById("out");
  const msg = document.getElementById("msg");
  out.innerHTML = "";
  msg.textContent = "请求中...";

  try{
    const products = [];
    for(let i=0;i<rows;i++){
      const out_min = parseFloat(document.getElementById(`out_min_${i}`).value);
      const out_max = parseFloat(document.getElementById(`out_max_${i}`).value);
      const target_low = parseFloat(document.getElementById(`target_low_${i}`).value);
      const target_high = parseFloat(document.getElementById(`target_high_${i}`).value);
      if([out_min,out_max,target_low,target_high].some(x=>Number.isNaN(x))){
        throw new Error(`槽位 #${i+1} 有非数字输入`);
      }
      products.push({out_min,out_max,target_low,target_high});
    }
    const top_k = parseInt(document.getElementById("topk").value);
    if(Number.isNaN(top_k) || top_k<1) throw new Error("Top-K 必须是正整数");

    const res = await fetch("/search", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({products, top_k})
    });

    const text = await res.text();
    if(!res.ok){
      msg.innerHTML = `<div class="err">HTTP ${res.status}\\n${text}</div>`;
      return;
    }
    const data = JSON.parse(text);
    const plans = Array.isArray(data.plans) ? data.plans : [];
    msg.textContent = `完成：输入槽位 ${data.input_slots}，返回方案 ${data.plans.length}（同稀有度约束已启用）`;

    // 渲染每个槽位
    const joint = data.joint_mean_x_range;
const head = document.createElement("div");
head.className = "slot";
head.innerHTML = `
  <h3>联合限制（所有槽位 AND）</h3>
  <p class="sub">
    交集 mean(x) 区间：${fmt(joint.L,6)} ~ ${fmt(joint.U,6)}
    ｜槽位数：${data.input_slots}
  </p>
  <table>
    <thead>
      <tr>
        <th>槽位</th>
        <th>out_min</th>
        <th>out_max</th>
        <th>期望 low</th>
        <th>期望 high</th>
        <th>映射 mean(x) L</th>
        <th>映射 mean(x) U</th>
      </tr>
    </thead>
    <tbody>
      ${data.slot_ranges.map(r => `
        <tr>
          <td>#${r.slot_index+1}</td>
          <td>${fmt(r.out_min,6)}</td>
          <td>${fmt(r.out_max,6)}</td>
          <td>${fmt(r.target_low,6)}</td>
          <td>${fmt(r.target_high,6)}</td>
          <td>${fmt(r.L,6)}</td>
          <td>${fmt(r.U,6)}</td>
        </tr>
      `).join("")}
    </tbody>
  </table>
`;
out.appendChild(head);

if(plans.length === 0){
  const empty = document.createElement("div");
  empty.className = "slot";
  empty.innerHTML = `<div class="muted">没有找到可行方案。</div>`;
  out.appendChild(empty);
  return;
}

// 再展示共同方案
plans.forEach((plan, idx) => {
  const div = document.createElement("div");
  div.className = "slot";
  div.innerHTML = `
    <h3>方案 ${idx+1} <span class="pill">rarity=${plan.rarity}</span></h3>
    <p class="sub">mean(x)=${fmt(plan.mean_x,6)} ｜箱子分布：<code>${cratesText(plan.crates)}</code></p>

    <div class="plan">
      <div class="planTitle">
        <div><b>各槽位产物磨损回代</b></div>
        <div class="muted">✓=命中期望区间</div>
      </div>
      <table>
        <thead>
          <tr><th>槽位</th><th>out_float</th><th>命中</th></tr>
        </thead>
        <tbody>
          ${plan.slot_outputs.map(o => `
            <tr>
              <td>#${o.slot_index+1}</td>
              <td>${fmt(o.out_float,6)}</td>
              <td>${o.hit ? "✓" : "✗"}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>

    <div class="plan">
      <div class="planTitle">
        <div><b>配料（10 件，同稀有度）</b></div>
        <div class="muted">每件标明磨损与上下限</div>
      </div>

      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>材料</th>
            <th>箱子</th>
            <th>稀有度</th>
            <th>in_min</th>
            <th>in_max</th>
            <th>磨损(float)</th>
            <th>x(归一化)</th>
          </tr>
        </thead>
        <tbody>
          ${plan.materials.map((m, j)=>`
            <tr>
              <td>${j+1}</td>
              <td>${m.name}</td>
              <td>${m.crate}</td>
              <td>${m.rarity}</td>
              <td>${fmt(m.in_min,6)}</td>
              <td>${fmt(m.in_max,6)}</td>
              <td>${fmt(m.in_float,6)}</td>
              <td>${fmt(m.x,6)}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
  out.appendChild(div);
});

  }catch(e){
    msg.innerHTML = `<div class="err">${e?.message ?? e}</div>`;
  }
}

async function loadMeta(){
  const res = await fetch("/meta");
  const data = await res.json();
  document.getElementById("meta").textContent =
    `桶材料数=${data.bucket_size}｜rarity可用=${data.available_rarities.join(",")}（每个rarity预计算一次）`;
}

render();
loadMeta();
</script>
</body>
</html>
"""


@app.get("/meta")
def meta():
    counts = {}
    for m in BUCKET:
        counts[m.rarity] = counts.get(m.rarity, 0) + 1
    return {
        "bucket_size": len(BUCKET),
        "rarity_counts": counts,
        "available_rarities": sorted(list(PRECOMP_BY_RARITY.keys())),
        "note": "同稀有度约束：每个方案的 10 个材料 rarity 一致；允许混箱。"
    }


@app.post("/search")
def search(req: 'MultiSearchRequest'):
    slots = req.products[:10]
    top_k = req.top_k

    # 1) 计算每个槽位的 mean(x) 区间，并取交集
    slot_ranges = []
    for idx, s in enumerate(slots):
        out_min = s.out_min
        out_max = s.out_max
        tlo = min(s.target_low, s.target_high)
        thi = max(s.target_low, s.target_high)

        L, U = out_to_mean_x_range(out_min, out_max, tlo, thi)
        slot_ranges.append({
            "slot_index": idx,
            "out_min": out_min, "out_max": out_max,
            "target_low": tlo, "target_high": thi,
            "L": L, "U": U,
        })

    # 交集：L = max(L_j), U = min(U_j)
    L_all = max(r["L"] for r in slot_ranges)
    U_all = min(r["U"] for r in slot_ranges)

    # 2) 无解提前返回（区间不相交）
    if L_all > U_all:
        return JSONResponse({
            "input_slots": len(slots),
            "top_k": top_k,
            "joint_mean_x_range": {"L": L_all, "U": U_all},
            "slot_ranges": slot_ranges,
            "plans": [],
            "notes": [
                "无解：所有槽位共同约束下，mean(x) 区间交集为空（L_all > U_all）。",
                "你可以放宽某些槽位的期望磨损区间，或检查 out_min/out_max 是否正确。"
            ]
        })

    sum_lo, sum_hi = 10.0 * L_all, 10.0 * U_all

    # 3) 只搜索一次：同稀有度 -> 对每个 rarity 找 Top-K，然后全局合并取 Top-K
    plans = []
    for rarity, pre in PRECOMP_BY_RARITY.items():
        used_mask = 0
        while True:

            # 诊断：剩余材料是否还有可能组成 10 件满足区间
            available = [pre.mats[i].x for i in range(len(pre.mats)) if ((used_mask >> i) & 1) == 0]
            if len(available) < 10:
                break

            available.sort()
            min_sum = sum(available[:10])
            max_sum = sum(available[-10:])
            if sum_hi < min_sum - 1e-9 or sum_lo > max_sum + 1e-9:
            # 剩余材料无论怎么选都不可能落在区间
                break

            best = query_best_plan(pre, sum_lo, sum_hi, used_mask)
            if best is None:
                break
            score, total_sum, chosen, plan_mask = best
            used_mask |= plan_mask

            mean_x = total_sum / 10.0
        # 回代每个槽位 out_float（你原来那段保留）
            slot_outputs = []
            ok_all = True
            for r in slot_ranges:
                out_float = calc_out_float(r["out_min"], r["out_max"], mean_x)
                hit = (r["target_low"] - 1e-9) <= out_float <= (r["target_high"] + 1e-9)
                if not hit:
                    ok_all = False
                slot_outputs.append({"slot_index": r["slot_index"], "out_float": out_float, "hit": hit})
            if not ok_all:
            # 理论上不会发生；发生就跳过（或 break）
                continue

            crates = {}
            for m in chosen:
                crates[m.crate] = crates.get(m.crate, 0) + 1

            plans.append({
                "rarity": rarity,
                "mean_x": mean_x,
                "crates": crates,
                "slot_outputs": slot_outputs,
                "materials": [
                    {
                        "id": m.id,
                        "name": m.name,
                        "crate": m.crate,
                        "rarity": m.rarity,
                        "in_min": m.in_min,
                        "in_max": m.in_max,
                        "in_float": m.in_float,
                        "x": m.x
                    } for m in chosen
                ]
            })

    return JSONResponse({
        "input_slots": len(slots),
        "top_k": top_k,
        "joint_mean_x_range": {"L": L_all, "U": U_all},
        "slot_ranges": slot_ranges,  # 给前端展示每条槽位的映射区间
        "plans": plans,              # ⭐现在是“共同限制下的一组方案”
        "notes": [
            "多槽位是 AND：先把每个槽位映射成 mean(x) 区间，再取交集，只搜索一次方案。",
            "同稀有度约束启用：每个方案的 10 个材料 rarity 一致；允许混箱。",
        ]
    })


# 让类型提示不报错
MultiSearchRequest.model_rebuild()
