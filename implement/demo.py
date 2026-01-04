import itertools
import math
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional, Iterable, Set
from bisect import bisect_left

EPS = 1e-12

# ----------------------------
# Data structures
# ----------------------------

@dataclass(frozen=True)
class Material:
    id: int
    name: str
    crate: str
    rarity: int
    in_min: float
    in_max: float
    in_float: float
    x: float  # normalized float in [0,1] (usually)

def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

def out_to_mean_x_range(out_min: float, out_max: float, target_low: float, target_high: float, eps: float = 1e-12) -> Tuple[float, float]:
    """
    Map output float range -> mean(x) range, using:
      out = out_min + mean(x) * (out_max - out_min)
    Note: This returns a CLOSED interval [L,U] after clamping to [0,1].
    If you want right-open semantics on user input, handle it in "hit" check.
    """
    if out_max - out_min < eps:
        return (1.0, 0.0)  # empty
    L = (target_low - out_min) / (out_max - out_min)
    U = (target_high - out_min) / (out_max - out_min)
    L, U = min(L, U), max(L, U)
    return (clamp(L, 0.0, 1.0), clamp(U, 0.0, 1.0))

def calc_out_float(out_min: float, out_max: float, mean_x: float) -> float:
    return out_min + mean_x * (out_max - out_min)

# ----------------------------
# Meet-in-middle precomputation
# ----------------------------

@dataclass
class Precomp:
    rarity: int
    mats: List[Material]  # candidates (sorted by x)
    combos5: List[Tuple[float, int, Tuple[int, ...]]]  # (sum, mask, idxs) sorted by sum
    combo_sums: List[float]  # extracted sums (sorted)
    combo_cpacks: Optional[List[int]] = None  # only for ratio-search (optional)

def estimate_combo_count(n: int) -> int:
    # C(n,5)
    if n < 5:
        return 0
    return (n * (n - 1) * (n - 2) * (n - 3) * (n - 4)) // 120

def pick_candidates_targeted(
    mats_sorted: List[Material],
    target_mean_x: float,
    cap: int = 40,
    edge_k: int = 6,
) -> List[Material]:
    """
    Target-aware candidate picking:
    - Keep many items CLOSE to target_mean_x (to make 10-of-10 possible in that region)
    - Plus low/high edges (to allow "adjusting" the sum)
    This is crucial when the full bucket has dense region near target but cap is small.
    """
    n = len(mats_sorted)
    if n <= cap:
        return mats_sorted

    edge_k = min(edge_k, max(0, (cap - 10) // 2))
    mid_k = cap - 2 * edge_k
    if mid_k < 10:
        mid_k = 10
        edge_k = (cap - mid_k) // 2

    xs = [m.x for m in mats_sorted]
    pos = bisect_left(xs, target_mean_x)

    # take a centered window of size mid_k
    half = mid_k // 2
    start = max(0, pos - half)
    end = start + mid_k
    if end > n:
        end = n
        start = max(0, end - mid_k)

    mid = mats_sorted[start:end]
    low = mats_sorted[:edge_k]
    high = mats_sorted[-edge_k:] if edge_k > 0 else []

    seen: Set[int] = set()
    cand: List[Material] = []
    for m in (mid + low + high):
        if m.id not in seen:
            cand.append(m)
            seen.add(m.id)

    # if still not enough (due to dedup), expand mid window outwards
    if len(cand) < cap:
        l = start - 1
        r = end
        while len(cand) < cap and (l >= 0 or r < n):
            if l >= 0:
                m = mats_sorted[l]
                if m.id not in seen:
                    cand.append(m); seen.add(m.id)
            if len(cand) >= cap:
                break
            if r < n:
                m = mats_sorted[r]
                if m.id not in seen:
                    cand.append(m); seen.add(m.id)
            l -= 1
            r += 1

    # Final: keep cap and sort by x
    cand = cand[:cap]
    cand.sort(key=lambda m: m.x)
    return cand

def build_precomp_for_candidates(rarity: int, cand: List[Material]) -> Optional[Precomp]:
    cand = sorted(cand, key=lambda m: m.x)
    if len(cand) < 10:
        return None

    xs = [m.x for m in cand]
    bits = [1 << i for i in range(len(cand))]

    combos5: List[Tuple[float, int, Tuple[int, ...]]] = []
    n = len(cand)
    # micro-opt: localize for speed
    xs_local = xs
    bits_local = bits
    append = combos5.append
    for idxs in itertools.combinations(range(n), 5):
        i1, i2, i3, i4, i5 = idxs
        s = xs_local[i1] + xs_local[i2] + xs_local[i3] + xs_local[i4] + xs_local[i5]
        mask = bits_local[i1] | bits_local[i2] | bits_local[i3] | bits_local[i4] | bits_local[i5]
        append((s, mask, idxs))

    combos5.sort(key=lambda t: t[0])
    combo_sums = [t[0] for t in combos5]
    return Precomp(rarity=rarity, mats=cand, combos5=combos5, combo_sums=combo_sums, combo_cpacks=None)

def build_precomp_for_candidates_ratio(rarity: int, cand: List[Material], crate_to_idx: Dict[str, int]) -> Optional[Precomp]:
    """
    Same as build_precomp_for_candidates, but also computes a bit-packed crate-count signature for each 5-combo.
    Each crate count is stored in 3 bits (0..7). Half-combo counts are <=5 so safe (no carry).
    """
    cand = sorted(cand, key=lambda m: m.x)
    if len(cand) < 10:
        return None

    xs = [m.x for m in cand]
    bits = [1 << i for i in range(len(cand))]

    shifts = []
    for m in cand:
        idx = crate_to_idx.get(m.crate, -1)
        if idx < 0:
            shifts.append(0)
        else:
            shifts.append(1 << (3 * idx))

    combos5: List[Tuple[float, int, Tuple[int, ...]]] = []
    cpacks: List[int] = []
    n = len(cand)

    xs_local = xs
    bits_local = bits
    shifts_local = shifts
    append = combos5.append
    appendc = cpacks.append

    for idxs in itertools.combinations(range(n), 5):
        i1, i2, i3, i4, i5 = idxs
        s = xs_local[i1] + xs_local[i2] + xs_local[i3] + xs_local[i4] + xs_local[i5]
        mask = bits_local[i1] | bits_local[i2] | bits_local[i3] | bits_local[i4] | bits_local[i5]
        cpack = shifts_local[i1] + shifts_local[i2] + shifts_local[i3] + shifts_local[i4] + shifts_local[i5]
        append((s, mask, idxs))
        appendc(cpack)

    # sort by sum, keep cpacks aligned
    order = sorted(range(len(combos5)), key=lambda i: combos5[i][0])
    combos5_sorted = [combos5[i] for i in order]
    cpacks_sorted = [cpacks[i] for i in order]
    combo_sums = [t[0] for t in combos5_sorted]

    return Precomp(rarity=rarity, mats=cand, combos5=combos5_sorted, combo_sums=combo_sums, combo_cpacks=cpacks_sorted)

# ----------------------------
# Fast query: avoid sorting 650k combos each time
# ----------------------------

def iter_indices_near_target(sums: List[float], target: float, limit: int) -> Iterable[int]:
    """
    Generate indices in order of closeness to target WITHOUT sorting the whole list.
    sums must be sorted.
    """
    n = len(sums)
    r = bisect_left(sums, target)
    l = r - 1
    out = 0
    while out < limit and (l >= 0 or r < n):
        if l < 0:
            yield r; r += 1; out += 1
            continue
        if r >= n:
            yield l; l -= 1; out += 1
            continue
        if abs(sums[l] - target) <= abs(sums[r] - target):
            yield l; l -= 1; out += 1
        else:
            yield r; r += 1; out += 1

# def query_best_plan_fast(
#     pre: Precomp,
#     sum_lo: float,
#     sum_hi: float,
#     used_mask: int,
#     desired_total: float,
#     *,
#     left_limit: int = 20000,
#     probe_limit: int = 60,
#     eps: float = 1e-9,
#     right_open: bool = True,
# ):
#     """
#     Return (score, total_sum_x, chosen_materials[10], plan_mask) or None
#     """
#     combos = pre.combos5
#     sums = pre.combo_sums
#     nC = len(combos)
#     if nC == 0:
#         return None

#     left_limit = min(left_limit, nC)
#     target_left = desired_total / 2.0
#     best = None  # (score, total, chosen, mask)

#     for li in iter_indices_near_target(sums, target_left, left_limit):
#         sL, maskL, idxL = combos[li]
#         if maskL & used_mask:
#             continue

#         need_lo = sum_lo - sL
#         need_hi = sum_hi - sL
#         if need_hi < sums[0] - 1e-12 or need_lo > sums[-1] + 1e-12:
#             continue

#         targetR = desired_total - sL
#         posR = bisect_left(sums, targetR)

#         for k in range(probe_limit):
#             for j in (posR - k, posR + k):
#                 if j < 0 or j >= nC:
#                     continue
#                 if k != 0 and (posR - k) == (posR + k):
#                     continue

#                 sR = sums[j]
#                 if sR < need_lo - 1e-12 or sR > need_hi + 1e-12:
#                     continue

#                 sR2, maskR, idxR = combos[j]
#                 if maskR & used_mask:
#                     continue
#                 if maskL & maskR:
#                     continue

#                 total = sL + sR2
#                 if total < sum_lo - 1e-12:
#                     continue
#                 if right_open:
#                     if total >= sum_hi - 1e-12:
#                         continue
#                 else:
#                     if total > sum_hi + eps:
#                         continue

#                 plan_mask = maskL | maskR
#                 chosen_idxs = tuple(sorted(idxL + idxR))
#                 chosen = [pre.mats[i] for i in chosen_idxs]
#                 chosen = sorted(chosen, key=lambda m: m.id)

#                 score = abs(total - desired_total)
#                 cand = (score, total, chosen, plan_mask)
#                 if best is None or score < best[0]:
#                     best = cand
#                     if best[0] < 1e-10:
#                         return best

#     return best

from bisect import bisect_left

def _iter_window_near_target(sums, target, lo, hi, limit):
    """
    在 sums[lo:hi] 的窗口内，按接近 target 的顺序吐出 index（不排序整段）。
    """
    if lo >= hi:
        return
    pos = bisect_left(sums, target, lo, hi)
    # clamp 到窗口内
    r = min(max(pos, lo), hi - 1)
    l = r - 1
    out = 0
    while out < limit and (l >= lo or r < hi):
        if l < lo:
            yield r
            r += 1
            out += 1
            continue
        if r >= hi:
            yield l
            l -= 1
            out += 1
            continue
        if abs(sums[l] - target) <= abs(sums[r] - target):
            yield l
            l -= 1
        else:
            yield r
            r += 1
        out += 1


def query_best_plan_fast(
    pre: Precomp,
    sum_lo: float,
    sum_hi: float,
    used_mask: int,
    desired_total: float,
    *,
    left_limit: int = 20000,
    probe_limit: int = 60,
    window_scan_threshold: int = 6000,   # ⭐新增：窗口小就扫窗口，减少漏解
    eps: float = 1e-9,
    right_open: bool = True,
):
    """
    Return (score, total_sum_x, chosen_materials[10], plan_mask) or None

    改进点：
    - 右侧不再固定 probe 60；当可行窗口较小（<=window_scan_threshold）时，
      直接在窗口内按“接近 targetR”扫完整个窗口 -> 大幅降低漏第二组的概率。
    """
    combos = pre.combos5
    sums = pre.combo_sums
    nC = len(combos)
    if nC == 0:
        return None

    left_limit = min(left_limit, nC)
    target_left = desired_total / 2.0
    best = None  # (score, total, chosen, mask)

    for li in iter_indices_near_target(sums, target_left, left_limit):
        sL, maskL, idxL = combos[li]
        if maskL & used_mask:
            continue

        need_lo = sum_lo - sL
        need_hi = sum_hi - sL
        if need_hi < sums[0] - 1e-12 or need_lo > sums[-1] + 1e-12:
            continue

        j0 = bisect_left(sums, need_lo - 1e-12)
        j1 = bisect_left(sums, need_hi + 1e-12)
        if j0 >= j1:
            continue

        targetR = desired_total - sL

        # 窗口小：扫窗口（更不漏解）
        if (j1 - j0) <= window_scan_threshold:
            scan_limit = j1 - j0
            for j in _iter_window_near_target(sums, targetR, j0, j1, scan_limit):
                sR2, maskR, idxR = combos[j]
                if maskR & used_mask:
                    continue
                if maskL & maskR:
                    continue

                total = sL + sR2
                if total < sum_lo - 1e-12:
                    continue
                if right_open:
                    if total >= sum_hi - 1e-12:
                        continue
                else:
                    if total > sum_hi + eps:
                        continue

                plan_mask = maskL | maskR
                chosen_idxs = tuple(sorted(idxL + idxR))
                chosen = [pre.mats[i] for i in chosen_idxs]
                chosen = sorted(chosen, key=lambda m: m.id)

                score = abs(total - desired_total)
                cand = (score, total, chosen, plan_mask)
                if best is None or score < best[0]:
                    best = cand
                    if best[0] < 1e-10:
                        return best
        else:
            # 窗口大：保留 probe 模式（快）
            posR = bisect_left(sums, targetR)
            for k in range(probe_limit):
                for j in (posR - k, posR + k):
                    if j < j0 or j >= j1:
                        continue
                    if k != 0 and (posR - k) == (posR + k):
                        continue

                    sR2, maskR, idxR = combos[j]
                    if maskR & used_mask:
                        continue
                    if maskL & maskR:
                        continue

                    total = sL + sR2
                    if total < sum_lo - 1e-12:
                        continue
                    if right_open:
                        if total >= sum_hi - 1e-12:
                            continue
                    else:
                        if total > sum_hi + eps:
                            continue

                    plan_mask = maskL | maskR
                    chosen_idxs = tuple(sorted(idxL + idxR))
                    chosen = [pre.mats[i] for i in chosen_idxs]
                    chosen = sorted(chosen, key=lambda m: m.id)

                    score = abs(total - desired_total)
                    cand = (score, total, chosen, plan_mask)
                    if best is None or score < best[0]:
                        best = cand
                        if best[0] < 1e-10:
                            return best

    return best

# ----------------------------
# Main search (multi-plans + better utilization)
# ----------------------------

def make_plan_dict(rarity: int, mean_x: float, chosen: List[Material], slot_ranges: List[dict], *, right_open: bool = True) -> Optional[dict]:
    # slot outputs verify
    slot_outputs = []
    ok_all = True
    for r in slot_ranges:
        out_float = calc_out_float(r["out_min"], r["out_max"], mean_x)
        if right_open:
            hit = (r["target_low"] - 1e-12) <= out_float < (r["target_high"] - 1e-12)
        else:
            hit = (r["target_low"] - 1e-9) <= out_float <= (r["target_high"] + 1e-9)
        if not hit:
            ok_all = False
        slot_outputs.append({"slot_index": r["slot_index"], "out_float": out_float, "hit": hit})
    if not ok_all:
        return None

    crates: Dict[str, int] = {}
    for m in chosen:
        crates[m.crate] = crates.get(m.crate, 0) + 1

    return {
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
                "x": m.x,
            }
            for m in chosen
        ],
    }

def _popcount(x: int) -> int:
    try:
        return x.bit_count()
    except AttributeError:
        return bin(x).count("1")


def _get_seed_plans(
    pre: Precomp,
    sum_lo: float,
    sum_hi: float,
    desired_total: float,
    *,
    seed_k: int = 25,
    left_limit: int = 40000,
    probe_limit: int = 200,
    right_open: bool = True,
) -> List[tuple]:
    """
    找一批“第一组候选方案”（允许相互重叠），用于 multi-start。
    返回 [(score,total,chosen,plan_mask), ...]，按 score 排序。
    """
    combos = pre.combos5
    sums = pre.combo_sums
    nC = len(combos)
    left_limit = min(left_limit, nC)

    target_left = desired_total / 2.0
    seeds = []
    seen = set()

    for li in iter_indices_near_target(sums, target_left, left_limit):
        sL, maskL, idxL = combos[li]
        need_lo = sum_lo - sL
        need_hi = sum_hi - sL
        if need_hi < sums[0] - 1e-12 or need_lo > sums[-1] + 1e-12:
            continue

        j0 = bisect_left(sums, need_lo - 1e-12)
        j1 = bisect_left(sums, need_hi + 1e-12)
        if j0 >= j1:
            continue

        targetR = desired_total - sL
        # 在可行窗口内取接近 targetR 的若干个点
        take = min(probe_limit, j1 - j0)
        for j in _iter_window_near_target(sums, targetR, j0, j1, take):
            sR2, maskR, idxR = combos[j]
            if maskL & maskR:
                continue

            total = sL + sR2
            if total < sum_lo - 1e-12:
                continue
            if right_open:
                if total >= sum_hi - 1e-12:
                    continue
            else:
                if total > sum_hi + 1e-9:
                    continue

            plan_mask = maskL | maskR
            if plan_mask in seen:
                continue
            seen.add(plan_mask)

            chosen_idxs = tuple(sorted(idxL + idxR))
            chosen = [pre.mats[i] for i in chosen_idxs]
            chosen = sorted(chosen, key=lambda m: m.id)

            score = abs(total - desired_total)
            seeds.append((score, total, chosen, plan_mask))

            if len(seeds) >= seed_k:
                seeds.sort(key=lambda t: t[0])
                return seeds

    seeds.sort(key=lambda t: t[0])
    return seeds


def _pack_plans_multistart(
    pre: Precomp,
    slot_ranges: List[dict],
    L_all: float,
    U_all: float,
    *,
    seed_k: int = 25,
    right_open: bool = True,
) -> List[dict]:
    """
    目标：尽可能“跑完材料”（最大化方案数量；其次最大化用料数量）。
    只用于小桶（几十件）最划算。
    """
    sum_lo = 10.0 * L_all
    sum_hi = 10.0 * U_all
    desired_total = (sum_lo + sum_hi) / 2.0

    seeds = _get_seed_plans(
        pre, sum_lo, sum_hi, desired_total,
        seed_k=seed_k,
        left_limit=len(pre.combo_sums),  # 小桶：尽量不漏
        probe_limit=250,
        right_open=right_open
    )

    # 如果连 seed 都没有，就直接返回空
    if not seeds:
        return []

    best_pack = []
    best_used = -1

    # 同时加一个“从无 seed 直接贪心”的备选
    seed_options = [None] + seeds

    for seed in seed_options:
        used_mask = 0
        pack = []

        if seed is not None:
            score, total_sum, chosen, plan_mask = seed
            used_mask |= plan_mask
            mean_x = total_sum / 10.0
            plan = make_plan_dict(pre.rarity, mean_x, chosen, slot_ranges, right_open=right_open)
            if plan is not None:
                pack.append(plan)

        while True:
            best = query_best_plan_fast(
                pre, sum_lo, sum_hi, used_mask, desired_total,
                left_limit=len(pre.combo_sums),   # 小桶：全扫 left 更稳
                probe_limit=220,
                window_scan_threshold=9000,
                right_open=right_open
            )
            if best is None:
                break
            score, total_sum, chosen, plan_mask = best
            used_mask |= plan_mask

            mean_x = total_sum / 10.0
            plan = make_plan_dict(pre.rarity, mean_x, chosen, slot_ranges, right_open=right_open)
            if plan is None:
                continue
            pack.append(plan)

        used_cnt = 10 * len(pack)
        # 先最大化方案数，再最大化用料
        if (len(pack) > len(best_pack)) or (len(pack) == len(best_pack) and used_cnt > best_used):
            best_pack = pack
            best_used = used_cnt

    return best_pack


def search_plans_for_rarity(
    rarity: int,
    mats_all: List[Material],
    slot_ranges: List[dict],
    L_all: float,
    U_all: float,
    *,
    cap: int = 40,
    max_combo_count: int = 2_000_000,
    max_rounds: int = 8,
    right_open: bool = True,
) -> List[dict]:
    """
    Improve utilization:
    - Not only one fixed candidate set of size 'cap'
    - We repeatedly pick a new candidate set from remaining materials and search again,
      so a 200-item bucket can yield >4 plans (cap=40 would otherwise hard-cap to 4).
    """
    # sum range of 10 items
    sum_lo = 10.0 * L_all
    sum_hi = 10.0 * U_all
    desired_total = (sum_lo + sum_hi) / 2.0
    target_mean = (L_all + U_all) / 2.0

    mats_sorted = sorted(mats_all, key=lambda m: m.x)

    if len(mats_sorted) <= 60 and estimate_combo_count(len(mats_sorted)) <= max_combo_count:
        pre_small = build_precomp_for_candidates(rarity, mats_sorted)
        if pre_small is not None:
            return _pack_plans_multistart(
                pre_small,
                slot_ranges,
                L_all,
                U_all,
                seed_k=30,
                right_open=right_open,
            )

    global_used_ids: Set[int] = set()
    all_plans: List[dict] = []

    rounds = 0
    while rounds < max_rounds:
        remaining = [m for m in mats_sorted if m.id not in global_used_ids]
        if len(remaining) < 10:
            break

        # quick feasibility on remaining
        xs_rem = [m.x for m in remaining]
        xs_rem.sort()
        min_sum = sum(xs_rem[:10])
        max_sum = sum(xs_rem[-10:])
        if sum_hi < min_sum - 1e-7 or sum_lo > max_sum + 1e-7:
            break

        # decide candidates: if remaining small enough, use all; else targeted cap
        if estimate_combo_count(len(remaining)) <= max_combo_count:
            cand = remaining
        else:
            cand = pick_candidates_targeted(remaining, target_mean_x=target_mean, cap=cap, edge_k=6)

        pre = build_precomp_for_candidates(rarity, cand)
        if pre is None:
            break

        # local greedy extraction within this candidate set
        used_mask = 0
        local_found = 0
        while True:
            best = query_best_plan_fast(
                pre,
                sum_lo,
                sum_hi,
                used_mask,
                desired_total,
                left_limit=20000,
                probe_limit=60,
                right_open=right_open,
            )
            if best is None:
                break
            score, total_sum, chosen, plan_mask = best

            mean_x = total_sum / 10.0
            plan = make_plan_dict(rarity, mean_x, chosen, slot_ranges, right_open=right_open)
            # if numerical edge case causes miss, skip but still mark as used to avoid loops
            used_mask |= plan_mask
            if plan is None:
                continue

            all_plans.append(plan)
            local_found += 1
            for m in chosen:
                global_used_ids.add(m.id)

            # stop if candidate is exhausted
            if len([m for m in pre.mats if m.id not in global_used_ids]) < 10:
                break

        if local_found == 0:
            # If we found nothing in this candidate set, likely candidate picking missed;
            # For robustness, try once with a different target (L_all or U_all) by shifting mean.
            # If still none, stop to avoid long waits.
            if estimate_combo_count(len(remaining)) > max_combo_count:
                alt_targets = [L_all, U_all]
                got = False
                for alt in alt_targets:
                    cand2 = pick_candidates_targeted(remaining, target_mean_x=alt, cap=cap, edge_k=6)
                    pre2 = build_precomp_for_candidates(rarity, cand2)
                    if pre2 is None:
                        continue
                    used_mask2 = 0
                    best2 = query_best_plan_fast(
                        pre2, sum_lo, sum_hi, used_mask2, desired_total,
                        left_limit=20000, probe_limit=80, right_open=right_open
                    )
                    if best2 is None:
                        continue
                    score, total_sum, chosen, plan_mask = best2
                    mean_x = total_sum / 10.0
                    plan2 = make_plan_dict(rarity, mean_x, chosen, slot_ranges, right_open=right_open)
                    used_mask2 |= plan_mask
                    if plan2 is None:
                        continue
                    all_plans.append(plan2)
                    for m in chosen:
                        global_used_ids.add(m.id)
                    got = True
                    break
                if not got:
                    break
            else:
                break

        rounds += 1

    return all_plans

def search_bucket_all_plans(
    materials: List[Material],
    slot_ranges: List[dict],
    L_all: float,
    U_all: float,
    *,
    cap: int = 40,
    max_combo_count: int = 2_000_000,
    right_open: bool = True,
) -> List[dict]:
    mats_by_rarity: Dict[int, List[Material]] = {}
    for m in materials:
        mats_by_rarity.setdefault(m.rarity, []).append(m)

    plans: List[dict] = []
    for rarity, mats in sorted(mats_by_rarity.items(), key=lambda kv: kv[0]):
        if len(mats) < 10:
            continue
        plans.extend(search_plans_for_rarity(
            rarity,
            mats,
            slot_ranges,
            L_all,
            U_all,
            cap=cap,
            max_combo_count=max_combo_count,
            max_rounds=8,
            right_open=right_open,
        ))
    return plans


# ============================================================
# Ratio (crate) search
# ============================================================

def compute_target_counts(crate_order: List[str], crate_weights: Dict[str, float], total: int = 10) -> Dict[str, int]:
    """
    Normalize user weights into integer target counts summing to total.
    Largest-remainder method.
    """
    ws = {c: float(crate_weights.get(c, 0.0) or 0.0) for c in crate_order}
    s = sum(max(0.0, v) for v in ws.values())
    if s <= 0:
        ws = {c: 1.0 for c in crate_order}
        s = float(len(crate_order)) if crate_order else 1.0

    raw = {c: total * max(0.0, ws[c]) / s for c in crate_order}
    base = {c: int(math.floor(raw[c])) for c in crate_order}
    rem = total - sum(base.values())
    frac_order = sorted(crate_order, key=lambda c: (raw[c] - base[c]), reverse=True)
    for i in range(rem):
        base[frac_order[i % len(frac_order)]] += 1
    return base

def pick_candidates_ratio(
    remaining: List[Material],
    target_mean_x: float,
    cap: int,
    crate_order: List[str],
    target_counts: Dict[str, int],
) -> List[Material]:
    """
    Candidate picking for ratio-search:
    - Ensure enough materials from crates with non-zero targets (so we can hit ratio)
    - Still keep lots of items near target_mean_x for float feasibility
    - Include edges for sum adjustment
    """
    if len(remaining) <= cap:
        out = sorted(remaining, key=lambda m: m.x)
        return out

    near = sorted(remaining, key=lambda m: abs(m.x - target_mean_x))
    by_x = sorted(remaining, key=lambda m: m.x)

    seen: Set[int] = set()
    cand: List[Material] = []

    # Seed from crates with highest target first
    for crate in sorted(crate_order, key=lambda c: target_counts.get(c, 0), reverse=True):
        need = target_counts.get(crate, 0)
        if need <= 0:
            continue
        ms = [m for m in remaining if m.crate == crate]
        ms.sort(key=lambda m: abs(m.x - target_mean_x))
        quota = max(2, min(len(ms), need * 3))
        for m in ms[:quota]:
            if len(cand) >= cap:
                break
            if m.id not in seen:
                cand.append(m)
                seen.add(m.id)
        if len(cand) >= cap:
            break

    # Fill near-target, prefer crates with target>0 first
    for pass_no in (0, 1):
        for m in near:
            if len(cand) >= cap:
                break
            if m.id in seen:
                continue
            if pass_no == 0 and target_counts.get(m.crate, 0) <= 0:
                continue
            cand.append(m)
            seen.add(m.id)
        if len(cand) >= cap:
            break

    # Add edges
    edge_k = 6
    for m in (by_x[:edge_k] + by_x[-edge_k:]):
        if len(cand) >= cap:
            break
        if m.id in seen:
            continue
        cand.append(m)
        seen.add(m.id)

    # Final fill if still short
    for m in near:
        if len(cand) >= cap:
            break
        if m.id in seen:
            continue
        cand.append(m)
        seen.add(m.id)

    cand = cand[:cap]
    cand.sort(key=lambda m: m.x)
    return cand

def query_best_plan_ratio(
    pre: Precomp,
    sum_lo: float,
    sum_hi: float,
    used_mask: int,
    desired_total: float,
    target_counts_arr: List[int],
    *,
    left_limit: int = 25000,
    probe_limit: int = 80,
    right_open: bool = True,
):
    """
    Return (dist, sum_score, total_sum, chosen_materials[10], plan_mask) or None
    """
    combos = pre.combos5
    sums = pre.combo_sums
    cpacks = pre.combo_cpacks
    if cpacks is None:
        raise ValueError("pre.combo_cpacks is required for ratio search")
    nC = len(combos)
    if nC == 0:
        return None

    left_limit = min(left_limit, nC)
    target_left = desired_total / 2.0

    best = None
    best_key = None  # (dist, sum_score)

    K = len(target_counts_arr)
    shifts = [3 * i for i in range(K)]

    for li in iter_indices_near_target(sums, target_left, left_limit):
        sL, maskL, idxL = combos[li]
        if maskL & used_mask:
            continue

        need_lo = sum_lo - sL
        need_hi = sum_hi - sL
        if need_hi < sums[0] - 1e-12 or need_lo > sums[-1] + 1e-12:
            continue

        posR = bisect_left(sums, desired_total - sL)

        for k in range(probe_limit):
            for j in (posR - k, posR + k):
                if j < 0 or j >= nC:
                    continue
                if k != 0 and (posR - k) == (posR + k):
                    continue

                sR = sums[j]
                if sR < need_lo - 1e-12 or sR > need_hi + 1e-12:
                    continue

                sR2, maskR, idxR = combos[j]
                if maskR & used_mask:
                    continue
                if maskL & maskR:
                    continue

                total = sL + sR2
                if total < sum_lo - 1e-12:
                    continue
                if right_open:
                    if total >= sum_hi - 1e-12:
                        continue
                else:
                    if total > sum_hi + 1e-9:
                        continue

                # distance
                cL = cpacks[li]
                cR = cpacks[j]
                dist = 0
                for sh, tgt in zip(shifts, target_counts_arr):
                    cnt = ((cL >> sh) & 7) + ((cR >> sh) & 7)
                    dist += abs(cnt - tgt)

                sum_score = abs(total - desired_total)
                key = (dist, sum_score)

                if best_key is None or key < best_key:
                    chosen_idxs = tuple(sorted(idxL + idxR))
                    chosen = [pre.mats[i] for i in chosen_idxs]
                    chosen = sorted(chosen, key=lambda m: m.id)
                    plan_mask = maskL | maskR
                    best = (dist, sum_score, total, chosen, plan_mask)
                    best_key = key

                    if dist == 0 and sum_score < 1e-10:
                        return best

    return best

def search_plans_for_rarity_ratio(
    rarity: int,
    mats_all: List[Material],
    slot_ranges: List[dict],
    L_all: float,
    U_all: float,
    crate_order: List[str],
    crate_weights: Dict[str, float],
    *,
    cap: int = 40,
    max_combo_count: int = 2_000_000,
    max_rounds: int = 8,
    right_open: bool = True,
) -> Tuple[List[dict], Dict[str, int]]:
    sum_lo = 10.0 * L_all
    sum_hi = 10.0 * U_all
    desired_total = (sum_lo + sum_hi) / 2.0
    target_mean = (L_all + U_all) / 2.0

    target_counts = compute_target_counts(crate_order, crate_weights, total=10)
    target_arr = [target_counts.get(c, 0) for c in crate_order]
    crate_to_idx = {c: i for i, c in enumerate(crate_order)}

    mats_sorted = sorted(mats_all, key=lambda m: m.x)
    global_used_ids: Set[int] = set()
    all_plans: List[dict] = []

    rounds = 0
    while rounds < max_rounds:
        remaining = [m for m in mats_sorted if m.id not in global_used_ids]
        if len(remaining) < 10:
            break

        xs_rem = [m.x for m in remaining]
        xs_rem.sort()
        min_sum = sum(xs_rem[:10])
        max_sum = sum(xs_rem[-10:])
        if sum_hi < min_sum - 1e-7 or sum_lo > max_sum + 1e-7:
            break

        if estimate_combo_count(len(remaining)) <= max_combo_count:
            cand = remaining
        else:
            cand = pick_candidates_ratio(
                remaining,
                target_mean_x=target_mean,
                cap=cap,
                crate_order=crate_order,
                target_counts=target_counts,
            )

        pre = build_precomp_for_candidates_ratio(rarity, cand, crate_to_idx=crate_to_idx)
        if pre is None:
            break

        used_mask = 0
        local_found = 0
        while True:
            best = query_best_plan_ratio(
                pre,
                sum_lo,
                sum_hi,
                used_mask,
                desired_total,
                target_counts_arr=target_arr,
                left_limit=25000,
                probe_limit=80,
                right_open=right_open,
            )
            if best is None:
                break
            dist, sum_score, total_sum, chosen, plan_mask = best
            used_mask |= plan_mask

            mean_x = total_sum / 10.0
            plan = make_plan_dict(rarity, mean_x, chosen, slot_ranges, right_open=right_open)
            if plan is None:
                continue

            plan["crate_distance"] = int(dist)
            plan["crate_target_counts"] = target_counts
            all_plans.append(plan)
            local_found += 1

            for m in chosen:
                global_used_ids.add(m.id)

            if len([m for m in pre.mats if m.id not in global_used_ids]) < 10:
                break

        if local_found == 0:
            if estimate_combo_count(len(remaining)) > max_combo_count:
                got = False
                for alt in (L_all, U_all):
                    cand2 = pick_candidates_ratio(
                        remaining,
                        target_mean_x=alt,
                        cap=cap,
                        crate_order=crate_order,
                        target_counts=target_counts,
                    )
                    pre2 = build_precomp_for_candidates_ratio(rarity, cand2, crate_to_idx=crate_to_idx)
                    if pre2 is None:
                        continue
                    used_mask2 = 0
                    best2 = query_best_plan_ratio(
                        pre2,
                        sum_lo,
                        sum_hi,
                        used_mask2,
                        desired_total,
                        target_counts_arr=target_arr,
                        left_limit=35000,
                        probe_limit=120,
                        right_open=right_open,
                    )
                    if best2 is None:
                        continue
                    dist, sum_score, total_sum, chosen, plan_mask = best2
                    used_mask2 |= plan_mask
                    mean_x = total_sum / 10.0
                    plan2 = make_plan_dict(rarity, mean_x, chosen, slot_ranges, right_open=right_open)
                    if plan2 is None:
                        continue
                    plan2["crate_distance"] = int(dist)
                    plan2["crate_target_counts"] = target_counts
                    all_plans.append(plan2)
                    for m in chosen:
                        global_used_ids.add(m.id)
                    got = True
                    break
                if not got:
                    break
            else:
                break

        rounds += 1

    all_plans.sort(key=lambda p: (p.get("crate_distance", 10**9), abs((p.get("mean_x", 0.0) - target_mean))))
    return all_plans, target_counts

def search_bucket_all_plans_with_crate_ratio(
    materials: List[Material],
    slot_ranges: List[dict],
    L_all: float,
    U_all: float,
    crate_weights: Dict[str, float],
    *,
    crate_order: Optional[List[str]] = None,
    cap: int = 40,
    max_combo_count: int = 2_000_000,
    right_open: bool = True,
) -> Tuple[List[dict], Dict[str, int], List[str]]:
    """
    Ratio-prioritized search.
    Returns: (plans, target_counts, crate_order)
    """
    if crate_order is None:
        crate_order = sorted({m.crate for m in materials})

    mats_by_rarity: Dict[int, List[Material]] = {}
    for m in materials:
        mats_by_rarity.setdefault(m.rarity, []).append(m)

    all_plans: List[dict] = []
    final_target_counts: Dict[str, int] = compute_target_counts(crate_order, crate_weights, total=10)

    for rarity, mats in sorted(mats_by_rarity.items(), key=lambda kv: kv[0]):
        if len(mats) < 10:
            continue
        plans_r, target_counts = search_plans_for_rarity_ratio(
            rarity,
            mats,
            slot_ranges,
            L_all,
            U_all,
            crate_order=crate_order,
            crate_weights=crate_weights,
            cap=cap,
            max_combo_count=max_combo_count,
            max_rounds=8,
            right_open=right_open,
        )
        all_plans.extend(plans_r)
        final_target_counts = target_counts

    target_mean = (L_all + U_all) / 2.0
    all_plans.sort(key=lambda p: (p.get("crate_distance", 10**9), abs(p.get("mean_x", 0.0) - target_mean)))
    return all_plans, final_target_counts, crate_order