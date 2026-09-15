# -*- coding: utf-8 -*-
"""
正心 · 聊天式觉察日记（地基 MVP）
主线：聊天 → DeepSeek 提取结构化字段 → 合并写进 Obsidian 每日日志
启动：uvicorn main:app --port 8900
"""
import os
import json
import datetime
import collections
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = APP_DIR / "data"
CONFIG_FILE = APP_DIR / "config.json"

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-v4-flash-vision-exp"

DATA_DIR.mkdir(exist_ok=True)

app = FastAPI(title="正心")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

SYSTEM_PROMPT = """你是「正心」，佳慧的私人觉察日记陪伴者。你不是来评判她的，而是陪她看见今天。

铁律（最高优先级）：
- 只提取她【明确说出】的信息。她没说到的字段一律保持 null / 空数组，绝不推理、猜测、编造。
- 金额尤其严格：只有她亲口说出具体数字时才记金额；她只说「吃了小笼包」没提价格，就不记金额、不编。

原则：
- 温暖、简短、口语化，像朋友聊天，不说教、不评判、不制造焦虑
- 她没提到的字段就留空，不要追问、不要强迫填满
- 回复控制在 3 句话以内，除非她在深入聊某件事
- 全程中文

字段规则：
- 饱腹感只从：空 / 舒适 / 很饱 / 撑 里选
- 精气神是 1-5 的整数，5 最好
- 记账：她明确说花了/赚了多少钱（有数字）才记；金额必须是她亲口说的数字，绝不编；只说吃了什么没提价格就不进记账
- 记账分类：支出=餐饮/交通/购物/娱乐/居住/通讯/医疗/学习/人情/其他；收入=工资/兼职/红包/其他
- 绝对不要在回复里心算或报总支出/总收入，只逐笔提取金额，总额由系统求和
- memory：从她的话里提取【值得长期记住】的信息——她的偏好、目标、习惯、重要事件、反复提到的事。只有跨天有用、未来对话会用到才记（如「喜欢驴火和煎饼果子」「在准备投 AI 解决方案岗」）；当天的一次性琐事（如「今天吃了小笼包」）不记。没有就返回空数组。分类只用：偏好/目标/习惯/事件。

示例（照这个标准提取）：
她说「昨晚3点睡7点半起，早餐吃的小笼包配桃子」→ 睡眠记 3:00/7:30/4.5小时，饮食记早餐，记账空数组（没提价格），memory 空数组（一次性琐事）。
她说「午饭花了30，买书50」→ 记账记两笔：支出30餐饮、支出50学习。
她说「我最近在准备投 AI 解决方案岗」→ memory 记一条：{"content":"在准备投 AI 解决方案岗","category":"目标"}。

只返回 JSON，不要任何 JSON 以外的文字，也不要代码块：
{
  "reply": "你的回复",
  "extracted": {
    "睡眠": {"入睡": null, "起床": null, "时长": null, "精气神": null},
    "饮食": [{"餐": "早餐", "内容": null, "饱腹": null}],
    "情绪": null,
    "正向链接": {"利益他人": null, "被帮助": null, "成长": null, "友善": null},
    "好事发生": null,
    "关于今天": null,
    "记账": [{"类型": "支出", "金额": 0, "分类": "餐饮", "备注": null}]
  },
  "memory": [{"content": "长期记忆内容", "category": "偏好"}]
}"""


# ---------- 配置 / key ----------
def load_config():
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_api_key():
    return os.environ.get("DEEPSEEK_API_KEY", "") or load_config().get("deepseek_api_key", "")


def get_vault_dir():
    """Obsidian 每日日志目录：从 config.json 的 obsidian_vault 读，没配就落到本地 ./每日日志"""
    vault = load_config().get("obsidian_vault", "").strip()
    if vault:
        return Path(vault)
    return APP_DIR / "每日日志"


# ---------- 今日状态 ----------
def date_key():
    return datetime.date.today().isoformat()  # 2026-09-14


def state_file():
    return DATA_DIR / f"{date_key()}.json"


def load_state():
    f = state_file()
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state):
    state_file().write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- 累积式长期记忆 ----------
MEMORY_FILE = DATA_DIR / "memory.json"


def load_memory():
    if MEMORY_FILE.exists():
        try:
            return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {"entries": []}
    return {"entries": []}


def save_memory(data):
    MEMORY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def add_memories(items):
    """追加长期记忆条目，按内容去重"""
    mem = load_memory()
    existing = {e.get("content") for e in mem.get("entries", []) if isinstance(e, dict)}
    for item in items:
        if not isinstance(item, dict):
            continue
        content = (item.get("content") or "").strip()
        if content and content not in existing:
            mem.setdefault("entries", []).append({
                "content": content,
                "category": item.get("category", "其他"),
                "date": datetime.date.today().isoformat(),
            })
            existing.add(content)
    save_memory(mem)
    return mem


def memory_context(limit=50):
    """把长期记忆转成给 AI 的上下文片段"""
    mem = load_memory()
    entries = mem.get("entries", [])[-limit:]
    if not entries:
        return ""
    lines = ["【长期记忆】她的一些偏好/目标/习惯（供参考，自然接续，不要刻意引用）："]
    for e in entries:
        lines.append(f"- [{e.get('category', '其他')}] {e.get('content', '')}")
    return "\n".join(lines)


def obsidian_filename():
    d = datetime.date.today()
    return f"{d.month}.{d.day}.md"


# ---------- 合并（新提取的非空字段覆盖旧的） ----------
def merge_extracted(old, new):
    if not isinstance(old, dict):
        old = {}
    if not isinstance(new, dict):
        new = {}
    out = dict(old)

    # 标量字段：新值非空才覆盖
    for key in ("情绪", "好事发生", "关于今天"):
        if new.get(key):
            out[key] = new[key]

    # 睡眠：逐子字段覆盖
    sleep_old = out.get("睡眠") if isinstance(out.get("睡眠"), dict) else {}
    sleep_new = new.get("睡眠") if isinstance(new.get("睡眠"), dict) else {}
    sleep = dict(sleep_old)
    for k, v in sleep_new.items():
        if v not in (None, ""):
            sleep[k] = v
    out["睡眠"] = sleep

    # 正向链接：逐 key 覆盖
    links_old = out.get("正向链接") if isinstance(out.get("正向链接"), dict) else {}
    links_new = new.get("正向链接") if isinstance(new.get("正向链接"), dict) else {}
    links = dict(links_old)
    for k, v in links_new.items():
        if v not in (None, ""):
            links[k] = v
    out["正向链接"] = links

    # 饮食：按「餐」合并
    meals = {}
    for m in out.get("饮食", []) if isinstance(out.get("饮食"), list) else []:
        if isinstance(m, dict) and m.get("餐"):
            meals[m["餐"]] = dict(m)
    for m in new.get("饮食", []) if isinstance(new.get("饮食"), list) else []:
        if isinstance(m, dict) and m.get("餐"):
            cur = meals.get(m["餐"], {"餐": m["餐"]})
            for k in ("内容", "饱腹"):
                if m.get(k) not in (None, ""):
                    cur[k] = m[k]
            meals[m["餐"]] = cur
    out["饮食"] = [meals[k] for k in ("早餐", "午餐", "晚餐", "加餐") if k in meals]

    # 记账：追加（同一笔不覆盖，保留全天每一笔）
    if isinstance(new.get("记账"), list) and new["记账"]:
        out["记账"] = (out.get("记账") if isinstance(out.get("记账"), list) else []) + new["记账"]

    return out


# ---------- 渲染成 Obsidian Markdown ----------
def render_markdown(state):
    if not state:
        return ""
    lines = ["<!-- 正心觉察 start -->", "", "## 正心觉察 · 今日", ""]

    sleep = state.get("睡眠", {})
    if sleep and any(sleep.get(k) for k in ("入睡", "起床", "时长", "精气神")):
        parts = []
        if sleep.get("入睡"):
            parts.append(f"入睡 {sleep['入睡']}")
        if sleep.get("起床"):
            parts.append(f"起床 {sleep['起床']}")
        if sleep.get("时长"):
            parts.append(f"时长 {sleep['时长']}")
        if sleep.get("精气神"):
            parts.append(f"精气神 {sleep['精气神']}/5")
        lines.append("**睡眠**：" + " · ".join(parts))
        lines.append("")

    meals = state.get("饮食", [])
    if meals:
        lines.append("**饮食**")
        for m in meals:
            text = m.get("内容") or "—"
            if m.get("饱腹"):
                text += f"（{m['饱腹']}）"
            lines.append(f"- {m.get('餐', '')}：{text}")
        lines.append("")

    links = state.get("正向链接", {})
    link_parts = [f"{k}：{v}" for k, v in links.items() if v]
    if link_parts:
        lines.append("**正向链接**：" + " · ".join(link_parts))
        lines.append("")

    if state.get("情绪"):
        lines.append(f"**情绪**：{state['情绪']}")
        lines.append("")

    if state.get("好事发生"):
        lines.append(f"**好事发生**：{state['好事发生']}")
        lines.append("")

    if state.get("关于今天"):
        lines.append(f"**关于今天**：{state['关于今天']}")
        lines.append("")

    txns = state.get("记账")
    if isinstance(txns, list) and txns:
        lines.append("**记账**")
        total_exp = total_inc = 0.0
        for t in txns:
            amt = t.get("金额")
            try:
                amt = float(amt)
            except (TypeError, ValueError):
                amt = 0.0
            if t.get("类型") == "收入":
                total_inc += amt
                sign = "+"
            else:
                total_exp += amt
                sign = "-"
            note = f"（{t['备注']}）" if t.get("备注") else ""
            lines.append(f"- {sign}{amt:g} {t.get('分类', '')}{note}")
        lines.append(f"今日支出 ¥{total_exp:g} · 收入 ¥{total_inc:g}")
        lines.append("")

    lines.append("<!-- 正心觉察 end -->")
    return "\n".join(lines)


def write_to_obsidian(state):
    md = render_markdown(state)
    if not md:
        return
    vault = get_vault_dir()
    f = vault / obsidian_filename()
    vault.mkdir(parents=True, exist_ok=True)
    existing = f.read_text(encoding="utf-8") if f.exists() else ""
    start, end = "<!-- 正心觉察 start -->", "<!-- 正心觉察 end -->"
    if start in existing and end in existing:
        i = existing.index(start)
        j = existing.index(end) + len(end)
        new_content = existing[:i] + md + existing[j:]
    else:
        new_content = (existing.rstrip() + "\n\n" + md + "\n") if existing.strip() else (md + "\n")
    f.write_text(new_content, encoding="utf-8")


# ---------- 复盘聚合 ----------
def list_daily_states():
    """读 data/ 下所有 YYYY-MM-DD.json，返回 [(date, state)]，按日期升序"""
    rows = []
    for f in DATA_DIR.glob("*.json"):
        try:
            d = datetime.date.fromisoformat(f.stem)
        except ValueError:
            continue
        try:
            state = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows.append((d, state))
    rows.sort(key=lambda r: r[0])
    return rows


def build_memory(days=7):
    """跨天记忆：最近 N 天（不含今天）的摘要，让对话更懂她、能接续习惯"""
    today = datetime.date.today()
    recent = [(d, s) for d, s in list_daily_states() if 1 <= (today - d).days <= days]
    if not recent:
        return ""
    lines = [f"【最近 {days} 天的记忆】仅供参考，让对话更懂她、能接续她的习惯："]
    for d, s in recent:
        if not isinstance(s, dict) or not s:
            continue
        parts = []
        sleep = s.get("睡眠") or {}
        if sleep.get("入睡") and sleep.get("起床"):
            parts.append(f"睡{sleep['入睡']}-{sleep['起床']}")
        if sleep.get("精气神"):
            parts.append(f"精气神{sleep['精气神']}/5")
        txns = [t for t in (s.get("记账") or []) if isinstance(t, dict)]
        if txns:
            exp = sum(float(t.get("金额") or 0) for t in txns if t.get("类型") != "收入")
            if exp:
                parts.append(f"支出{exp:g}")
        if s.get("情绪"):
            parts.append(s["情绪"])
        if parts:
            lines.append(f"- {d.month}月{d.day}日：{'，'.join(parts)}")
    return "\n".join(lines)


def window_cutoff(months):
    """回到 (months-1) 个月前的月初"""
    today = datetime.date.today()
    y, m = today.year, today.month
    for _ in range(months - 1):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return datetime.date(y, m, 1)


def aggregate(rows, months):
    total_exp = total_inc = 0.0
    cat_exp = collections.defaultdict(float)
    cat_inc = collections.defaultdict(float)
    spirit = []
    satiety = collections.Counter()
    meal_count = 0
    links = collections.Counter()
    good_days = 0
    active_days = 0

    for _d, state in rows:
        if not isinstance(state, dict) or not state:
            continue
        active_days += 1
        for t in state.get("记账") or []:
            if not isinstance(t, dict):
                continue
            try:
                amt = float(t.get("金额"))
            except (TypeError, ValueError):
                amt = 0.0
            cat = t.get("分类") or "其他"
            if t.get("类型") == "收入":
                total_inc += amt
                cat_inc[cat] += amt
            else:
                total_exp += amt
                cat_exp[cat] += amt
        sleep = state.get("睡眠") or {}
        if sleep.get("精气神"):
            try:
                spirit.append(int(sleep["精气神"]))
            except (TypeError, ValueError):
                pass
        for m in state.get("饮食") or []:
            if isinstance(m, dict) and m.get("饱腹"):
                satiety[m["饱腹"]] += 1
                meal_count += 1
        for k, v in (state.get("正向链接") or {}).items():
            if v:
                links[k] += 1
        if state.get("好事发生"):
            good_days += 1

    return {
        "months": months,
        "active_days": active_days,
        "记账": {
            "总支出": round(total_exp, 2),
            "总收入": round(total_inc, 2),
            "结余": round(total_inc - total_exp, 2),
            "支出分类": dict(sorted(cat_exp.items(), key=lambda kv: -kv[1])),
            "收入分类": dict(sorted(cat_inc.items(), key=lambda kv: -kv[1])),
        },
        "睡眠": {
            "平均精气神": round(sum(spirit) / len(spirit), 1) if spirit else None,
            "记录天数": len(spirit),
        },
        "饮食": {"记录餐数": meal_count, "饱腹分布": dict(satiety)},
        "正向链接": dict(links),
        "好事天数": good_days,
    }


@app.get("/api/review")
def review(months: int = 1):
    months = max(1, min(int(months), 12))
    cutoff = window_cutoff(months)
    rows = [(d, s) for d, s in list_daily_states() if d >= cutoff]
    result = aggregate(rows, months)
    result["窗口起始"] = cutoff.isoformat()
    result["窗口结束"] = datetime.date.today().isoformat()
    return result


# ---------- 路由 ----------
@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/state")
def get_state():
    return {"date": date_key(), "state": load_state(), "saved_file": str(get_vault_dir() / obsidian_filename())}


@app.post("/api/config")
async def set_config(payload: dict):
    cfg = load_config()
    if "deepseek_api_key" in payload:
        key = (payload.get("deepseek_api_key") or "").strip()
        if key:
            cfg["deepseek_api_key"] = key
        else:
            cfg.pop("deepseek_api_key", None)
    if "obsidian_vault" in payload:
        vault = (payload.get("obsidian_vault") or "").strip()
        if vault:
            cfg["obsidian_vault"] = vault
        else:
            cfg.pop("obsidian_vault", None)
    save_config(cfg)
    return {"ok": True, "has_key": bool(get_api_key()), "obsidian_vault": cfg.get("obsidian_vault", "")}


@app.get("/api/config")
def get_config_status():
    cfg = load_config()
    return {"has_key": bool(get_api_key()), "obsidian_vault": cfg.get("obsidian_vault", "")}


@app.post("/api/chat")
async def chat(payload: dict):
    api_key = get_api_key()
    if not api_key:
        return JSONResponse({"error": "未配置 DeepSeek API Key"}, status_code=400)

    history = payload.get("messages") or []
    if not isinstance(history, list) or not history:
        return JSONResponse({"error": "缺少消息"}, status_code=400)

    # 带上今天已有的状态，让 AI 能接续她之前记的
    today_state = load_state()
    context_note = ""
    if today_state:
        context_note = "她今天已经记录的觉察（供你参考、接续，不要重复追问）：\n" + json.dumps(today_state, ensure_ascii=False)

    # 跨天记忆：最近几天的摘要，让 AI 更懂她的作息/习惯
    memory_note = build_memory()
    # 累积式长期记忆：偏好/目标/习惯
    long_memory_note = memory_context()

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if long_memory_note:
        messages.append({"role": "system", "content": long_memory_note})
    if memory_note:
        messages.append({"role": "system", "content": memory_note})
    if context_note:
        messages.append({"role": "system", "content": context_note})
    messages.extend(history)

    try:
        async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
            resp = await client.post(
                DEEPSEEK_URL,
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
                json={"model": MODEL, "messages": messages, "temperature": 0.6, "response_format": {"type": "json_object"}},
            )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return JSONResponse({"error": f"DeepSeek 调用失败：{e}"}, status_code=502)

    # 解析模型返回的 JSON（容忍代码围栏）
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:]
        content = content.strip()
    try:
        parsed = json.loads(content)
    except Exception:
        return JSONResponse({"reply": content, "extracted": {}, "parse_error": True})

    reply = parsed.get("reply", "")
    extracted = parsed.get("extracted") or {}
    memory_items = parsed.get("memory") or []

    # 合并 + 落盘
    merged = merge_extracted(load_state(), extracted)
    save_state(merged)
    write_to_obsidian(merged)
    # 累积式长期记忆
    if memory_items:
        add_memories(memory_items)

    return {"reply": reply, "extracted": merged, "parse_error": False}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8900)
