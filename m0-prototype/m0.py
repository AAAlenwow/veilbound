#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M0 原型 · AI 无限流游戏最小可玩切片
=====================================

目标：在本地跑通一个「双盲 Encounter」，并自动验证三通道隔离。
不等同于产品，只用来验证三件事是否成立：

  1. 三通道信息隔离（performance / intent / judge）是否零泄露  →  --redteam 自动验证
  2. intent 三层博弈（填真 / 填假 / 不填）是否有体验差异        →  --play 手动验证
  3. Encounter 状态机是否会卡死                                →  --play 跑通即证明

设计上刻意做的裁剪（留给 M1）：
  · 意图解析：当前用「动作类型选项」代替自由文本 → 结构化，先把机制跑通
  · AI 兜底：只有极简 mock 版，不做真实 LLM 扮演
  · 持久化：内存态，进程退出即丢

用法：
    python3 m0.py --play            # 双人同机轮流（hot-seat）
    python3 m0.py --play --solo     # 单人，B 由 mock 自动回应
    python3 m0.py --redteam         # 泄露红队测试（自动化）
    python3 m0.py --selftest        # 判定函数与状态机自检

接入真实 LLM（可选）：
    export LLM_BASE_URL=https://api.deepseek.com/v1
    export LLM_API_KEY=sk-xxx
    export LLM_MODEL=deepseek-chat
    未设置时自动降级为模板叙事，功能不受影响。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import textwrap
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------------------
# 1. 世界种子（L0）—— 官方副本定义，AI 不碰这一层
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Script:
    script_id: str
    title: str
    role_a: str
    role_b: str
    premise_a: str          # A 的开场（A 的视角）
    premise_b: str          # B 的开场（B 的视角）
    dc: dict[str, int]
    max_turns: int = 10
    item_pool: tuple[str, ...] = ()
    # 线索标签：玩家填「内心」时可选一个 tag。
    # 被看穿时，判定层发的是这里**预设**的线索文本，而不是把自由文本截断——
    # 后者等于 AI 现场编线索，会让「真相唯一」失效。
    clue_tags: dict = field(default_factory=dict)   # tag -> 线索文本
    tag_labels: dict = field(default_factory=dict)  # tag -> 给玩家看的短标签
    tag_owner: dict = field(default_factory=dict)   # tag -> "A"/"B"/None（None=双方通用）


SCRIPT_ROAD = Script(
    script_id="ln-road-01",
    title="雨夜驿道",
    role_a="行商",
    role_b="重伤的军人",
    premise_a=(
        "你是个走南闯北的行商，雨夜里牵着驮马在驿道上赶路。\n"
        "前方山道边倒着一个人，浑身是伤，一动不动。\n"
        "他身上的军服，和你上周在城里见过的一批人很像——那批人正在被通缉。"
    ),
    premise_b=(
        "你遭到伏击，跌落山崖。不知过了多久才醒来，肋骨像是断了几根。\n"
        "雨还在下。你听见马蹄声和车轴声由远及近，一个牵着驮马的行商停在了十步开外。\n"
        "他正打量你，没有下马。"
    ),
    dc={
        "persuade": 13,
        "threaten": 15,
        "observe": 12,
        "attack": 14,
        "help": 10,
        "leave": 5,
    },
    max_turns=10,
    item_pool=("bandage", "dagger", "coin_pouch"),
    clue_tags={
        "hiding": "他攥着的那块碎布下面，是一把刀的柄。他不是手无寸铁。",
        "fake": "他说「被自己人卖的」时，声音在“自己人”三个字上顿了一下。",
        "money": "他的靴筒里塞着东西，形状像一卷纸。",
    },
    tag_labels={
        "hiding": "我在袖子里藏了刀",
        "fake": "被自己人卖是编的",
        "money": "我身上带着钱",
    },
)

# ---------------------------------------------------------------------------
# 2. 数据模型（L1）
# ---------------------------------------------------------------------------

@dataclass
class Performance:
    """表演通道：玩家「做出来的」言行，公开，双方都可见。"""
    turn: int
    actor: str              # "A" / "B"
    action_type: str
    text: str
    judged: Optional[dict] = None


@dataclass
class Intent:
    """内心通道：玩家自述的真实打算。只有裁判能读，永不进对方侧 narrator。"""
    turn: int
    actor: str
    text: str
    tag: Optional[str] = None      # 可选：标记「我在藏什么」，被看穿时按 tag 发线索


@dataclass
class Encounter:
    enc_id: str
    script: Script
    seed: bytes
    state: str = "PENDING_A"          # PENDING_A / PENDING_B / RESOLVED
    turn: int = 1
    performances: list[Performance] = field(default_factory=list)
    intents: list[Intent] = field(default_factory=list)
    # 授权泄露：观察判定成功后，允许进对方视图的「摘要」（不是 intent 原文）
    disclosed: dict[str, list[str]] = field(default_factory=lambda: {"A": [], "B": []})
    leverage: dict[str, int] = field(default_factory=lambda: {"A": 0, "B": 0})
    realness: dict[str, str] = field(default_factory=lambda: {"A": "human", "B": "human"})
    history_note: list[str] = field(default_factory=list)
    outcome: Optional[str] = None
    # 双向盲写：已提交、尚未结算的行动。两边都到齐才一起揭示。
    staged: dict[str, dict] = field(default_factory=dict)
    # 撞车记录：同一轮双方做了同一类动作（同时拔刀 / 同时示弱）
    collisions: list[dict] = field(default_factory=list)

    # -- 当前该谁行动 ---------------------------------------------------
    @property
    def pending(self) -> Optional[str]:
        """双向盲写下：下一个还没提交的一方。两边都提交了返回 None。"""
        for s in ("A", "B"):
            if s not in self.staged:
                return s
        return None

    def other(self, side: str) -> str:
        return "B" if side == "A" else "A"

    # -- 确定性骰子：可复现、不可预测、AI 无法影响 --------------------
    def roll(self, actor: str, action_type: str) -> int:
        msg = f"{self.enc_id}:{self.turn}:{actor}:{action_type}".encode()
        digest = hmac.new(self.seed, msg, hashlib.sha256).digest()
        return int.from_bytes(digest[:4], "big") % 20 + 1

    def intent_of(self, actor: str, turn: int) -> Optional[Intent]:
        for it in self.intents:
            if it.actor == actor and it.turn == turn:
                return it
        return None

    def latest_intent_of(self, actor: str, up_to_turn: int) -> Optional[Intent]:
        """该行动方在「up_to_turn 之前」最近一次填写的 intent。"""
        cands = [i for i in self.intents if i.actor == actor and i.turn <= up_to_turn]
        return cands[-1] if cands else None

    def intents_of(self, actor: str) -> list[Intent]:
        return [i for i in self.intents if i.actor == actor]


# ---------------------------------------------------------------------------
# 3. 三通道隔离（核心安全边界）
#
#    关键：build_opponent_view 的签名里根本没有 Intent。
#    靠函数签名防守，不靠提示词防守。
# ---------------------------------------------------------------------------

class Channels:
    """
    三通道隔离。所有跨视角内容必须经过这里。

    ★ 两条硬边界（实测踩过）：
      1. 函数签名里没有 Intent —— 靠类型系统防守，不靠提示词。
      2. 对方的 action_type / judged **一律不下发**。
         玩家该从「他说了什么」里自己推断他在威胁还是在示弱，
         告诉他「对方（威胁/施压）」等于直接公布答案。
    """

    @staticmethod
    def build_opponent_view(enc: Encounter, viewer: str, up_to_turn: int) -> dict:
        """
        viewer 能感知到的、关于「对面」的一切。

        只给表演的**文本**。不给动作类型，不给判定结果，不给对方的任何数值。
        """
        opp = enc.other(viewer)
        visible_perf = [
            {"turn": p.turn, "text": p.text}
            for p in enc.performances
            if p.actor == opp and p.turn <= up_to_turn
        ]
        return {
            "role": getattr(enc.script, f"role_{opp.lower()}"),
            "premise": getattr(enc.script, f"premise_{viewer.lower()}"),
            "their_performances": visible_perf,
            "your_clues": list(enc.disclosed.get(viewer, [])),
            # 只给 viewer 自己的气势，对方的不露
            "your_leverage": enc.leverage.get(viewer, 0),
        }

    @staticmethod
    def build_self_view(enc: Encounter, viewer: str, up_to_turn: int) -> dict:
        """viewer 看到自己的完整状态，含自己的 intent 历史。"""
        my_perf = [
            {"turn": p.turn, "action_type": p.action_type, "text": p.text,
             "judged": p.judged}
            for p in enc.performances
            if p.actor == viewer and p.turn <= up_to_turn
        ]
        return {
            "role": getattr(enc.script, f"role_{viewer.lower()}"),
            "premise": getattr(enc.script, f"premise_{viewer.lower()}"),
            "your_performances": my_perf,
            "your_intents": [
                {"turn": i.turn, "text": i.text}
                for i in enc.intents_of(viewer) if i.turn <= up_to_turn
            ],
            "your_clues": list(enc.disclosed.get(viewer, [])),
            "your_leverage": enc.leverage.get(viewer, 0),
        }


# ---------------------------------------------------------------------------
# 4. 裁判（L1，确定性，零 LLM）
# ---------------------------------------------------------------------------

ACTION_LABELS = {
    "persuade": "说服 / 许诺",
    "threaten": "威胁 / 施压",
    "observe": "观察 / 试探",
    "attack":  "动手",
    "help":    "直接施以援手",
    "leave":   "离开",
}


def run_judgement(enc: Encounter, actor: str, action_type: str) -> dict:
    """
    纯代码判定。AI 拿不到 seed，无法影响结果。

    四档而不是二值：
      critical (margin>=5) 大成功
      success  (margin>=0) 成功
      partial  (margin>=-4) 差一点 —— 观察类只给模糊线索
      fail     失败
    「差一点」这一档，是让推理副本有「你摸到边了但没抓住」的中间态。
    """
    r = enc.roll(actor, action_type)
    dc = enc.script.dc.get(action_type, 13)
    mod = enc.leverage.get(actor, 0)
    total = r + mod
    margin = total - dc
    if margin >= 5:
        band = "critical"
    elif margin >= 0:
        band = "success"
    elif margin >= -4:
        band = "partial"
    else:
        band = "fail"
    verdict = {
        "roll": r,
        "dc": dc,
        "modifier": mod,
        "modifier_source": "前序动作积累" if mod else "无",
        "total": total,
        "margin": margin,
        "band": band,
        "success": band in ("critical", "success"),
    }
    if verdict["success"]:
        enc.leverage[actor] = min(enc.leverage[actor] + 1, 3)
    return verdict


def apply_effects(enc: Encounter, actor: str, action_type: str, verdict: dict) -> None:
    """判定结果 → 状态变更 + 授权泄露。可安全重复调用（幂等）。"""
    opp = enc.other(actor)
    band = verdict.get("band", "success" if verdict.get("success") else "fail")

    # 观察：只有对方标记了 tag，才可能被看穿
    if action_type == "observe" and band in ("critical", "success", "partial"):
        opp_intent = enc.latest_intent_of(opp, enc.turn)
        clue = None
        if opp_intent and opp_intent.tag:
            clue = enc.script.clue_tags.get(opp_intent.tag)
            if clue and band == "partial":
                clue = clue + "　（你不很确定）"
        if clue and clue not in enc.disclosed[actor]:
            enc.disclosed[actor].append(clue)

    if action_type == "attack" and not verdict.get("success"):
        enc.leverage[actor] = max(enc.leverage[actor] - 1, -2)

    if action_type == "leave":
        enc.outcome = f"{actor}_left"


def summarize_intent(text: str) -> str:
    """
    把 intent 压成「线索摘要」。
    M0 用粗暴截断模拟；真实系统这里应是分类器/LLM 摘要 + 原文不外泄。
    红队测试断言的是：intent 原文的独特标记不得出现在对方视图里。
    """
    text = text.strip().replace("\n", " ")
    return "你隐约察觉到对方的真实想法：……" + (text[:14] + "…" if len(text) > 14 else text)


# ---------------------------------------------------------------------------
# 5. 叙事层（L2）—— 可替换：模板 / LLM
# ---------------------------------------------------------------------------

class Narrator:
    def render(self, enc: Encounter, viewer: str, action_type: str,
               verdict: Optional[dict], actor_text: str) -> str:
        raise NotImplementedError


class MockNarrator(Narrator):
    """无 LLM 时的模板叙事。功能完整，只是文笔平。"""

    TEMPLATES = {
        ("persuade", True):  "你的话似乎打动了他。他的神情松动了一些，没有立刻回绝。",
        ("persuade", False): "他听完，眉头没动。你的话没有落到他心里。",
        ("threaten", True):  "他下意识往后退了半步。你压住了场面。",
        ("threaten", False): "他盯着你，反而挺直了背。气氛僵住了。",
        ("observe", True):   "你不动声色地打量他，看出了一些别人看不到的东西。",
        ("observe", False):  "你看了许久，只看出一身伤和一身泥。",
        ("attack", True):    "你动了手，对方没能完全避开。",
        ("attack", False):   "你出手，却被对方架住。你失了先机。",
        ("help", True):      "你伸出了手。他犹豫了一瞬，还是接住了。",
        ("help", False):     "你伸手，他却往后缩了一下。",
        ("leave", True):     "你转身离开。身后没有声音。",
    }

    def render(self, enc, viewer, action_type, verdict, actor_text) -> str:
        if action_type == "leave":
            return self.TEMPLATES[("leave", True)]
        key = (action_type, bool(verdict and verdict["success"]))
        base = self.TEMPLATES.get(key, "你们沉默地对峙着。")
        return f"「{actor_text}」\n\n{base}"


class LLMNarrator(Narrator):
    """接入 OpenAI 兼容 API（MiniMax / DeepSeek 等）。未配置则自动降级。"""

    def __init__(self):
        self.base = os.environ.get("LLM_BASE_URL", "").rstrip("/")
        self.key = os.environ.get("LLM_API_KEY", "")
        self.model = os.environ.get("LLM_MODEL", "MiniMax-M2")
        self.last_sensitive: Optional[dict] = None     # 最近一次调用的内容安全标记
        self.think_tokens = 0                          # 推理块消耗（成本监控用）

    @property
    def available(self) -> bool:
        return bool(self.base and self.key)

    def render(self, enc, viewer, action_type, verdict, actor_text) -> str:
        # ★ 关键：只喂 opponent_view，绝不喂 Intent。
        # build_opponent_view 里的 premise 是 viewer 自己的开场，
        # their_performances 是「对方」的表演 —— 正是渲染所需。
        view = Channels.build_opponent_view(enc, viewer, enc.turn)

        my_role = getattr(enc.script, f"role_{viewer.lower()}")
        opp_role = getattr(enc.script, f"role_{enc.other(viewer).lower()}")

        system = (
            "你在为一名玩家渲染他视角下的场景。\n"
            f"玩家此刻的身份是：{my_role}。\n"
            f"他遇到的人是：{opp_role}。\n"
            f"严格区分这两者：说话、行动的是{opp_role}，不是玩家自己。玩家是{my_role}。\n"
            "只描述玩家能感知到的事。不要写玩家的内心活动，不要替玩家做决定。\n"
            "用第二人称「你」来指代玩家。2~3 句，克制、有画面感。\n"
            "直接输出正文，不要解释你的写作思路。"
        )
        user = json.dumps({
            "场景": view.get("premise", ""),
            f"对方（{opp_role}）做了什么": (
                view.get("their_performances", [])[-1:] or actor_text
            ),
            # 不喂原始骰值：那会让模型把「掷骰 17」写进叙事里，
            # 把内部机制暴露给玩家。只给「奏效/没奏效」的写作指导。
            "对方这一手": "奏效了" if verdict and verdict.get("success") else "没有达到目的",
            "你察觉到的线索": view.get("your_clues", []),
        }, ensure_ascii=False)

        last_err = ""
        for _ in range(2):                       # 空正文/拒答/乱码 → 重试一次
            try:
                clean, reasoning = self._call(system, user)
            except Exception as exc:
                last_err = f"{type(exc).__name__}: {str(exc)[:80]}"
                continue
            issue = output_issue(clean)
            if issue is None:
                return clean
            last_err = issue
        # 两次都没拿到合格正文 → 显式降级，绝不静默
        return (f"[叙事层降级] "
                f"{MockNarrator().render(enc, viewer, action_type, verdict, actor_text)}\n"
                f"({last_err})")

    def _call(self, system: str, user: str) -> tuple[str, str]:
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.8,
            "max_tokens": 1000,
            # MiniMax-M2.x 关不掉 thinking（会忽略 enable_thinking=false），
            # 只能用 reasoning_split 把推理分离到 reasoning_content，
            # 否则 <think> 块会混在 content 里污染叙事。
            "reasoning_split": True,
        }).encode()
        req = urllib.request.Request(
            f"{self.base}/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.key}"},
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read())

        # MiniMax 把内容安全判定直接放进响应，必须监控
        self.last_sensitive = {
            "in": data.get("input_sensitive"),
            "out": data.get("output_sensitive"),
            "in_type": data.get("input_sensitive_type"),
            "out_type": data.get("output_sensitive_type"),
        }
        msg = data["choices"][0]["message"]
        raw = msg.get("content") or ""
        reasoning = (msg.get("reasoning_content") or msg.get("reasoning") or "")
        self.think_tokens = (data.get("usage") or {}).get("completion_tokens", 0)
        # reasoning_split 生效时 raw 已是正文；strip_think 作兜底
        return strip_think(raw), reasoning


def strip_think(text: str) -> str:
    """
    MiniMax-M2 是带推理的模型，会把 <think>...</think> 混在 content 里。
    不剥掉会直接污染玩家看到的叙事。同时处理被截断的不闭合情况。
    """
    import re
    if not text:
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
    lower = text.lower()
    if "<think>" in lower:                 # 未闭合（被 max_tokens 截断）
        text = text[: lower.rfind("<think>")]
    return text.strip()


# --- 输出质量闸门 ----------------------------------------------------------
# 实测结论：MiniMax 的 input_sensitive / output_sensitive 字段【不可信】——
# 拒答时它也返回 False。所以必须在文本层面自己判。

REFUSAL_MARKERS = (
    "我无法", "我不能", "我没办法", "作为AI", "作为 AI", "作为一个AI",
    "抱歉", "很遗憾", "换个话题", "不合适", "不便",
    "I cannot", "I can't", "I'm sorry", "As an AI", "I won't",
)


def looks_like_refusal(text: str) -> bool:
    """
    拒答不一定短，但通常是「短 + 含标志词」。
    过长文本里出现「我无法」多半是 NPC 的台词，不算拒答。
    """
    t = text.strip()
    if not t:
        return True
    if len(t) > 60:
        return False
    return any(m in t for m in REFUSAL_MARKERS)


def latin_garbage(text: str) -> Optional[str]:
    """
    中文叙事里冒出的英文小写词是模型幻觉 token（实测出现过 hbarsole、_body）。
    全大写缩写（NPC/DC）放行。
    """
    import re
    for m in re.finditer(r"[A-Za-z]{2,}", text):
        w = m.group()
        if w.isupper() and len(w) <= 5:
            continue
        return w
    return None


def output_issue(text: str) -> Optional[str]:
    """返回问题描述；None 表示通过。"""
    if not text or not text.strip():
        return "正文为空"
    if looks_like_refusal(text):
        return f"疑似拒答：{text.strip()[:40]!r}"
    g = latin_garbage(text)
    if g:
        return f"幻觉乱码：{g!r}"
    if len(text.strip()) < 8:
        return f"正文过短：{text.strip()!r}"
    return None


def load_dotenv(path: Optional[str] = None) -> None:
    """
    极简 .env 加载，不引入依赖。真实环境变量优先。

    查找顺序（未显式指定时）：
      1. 脚本同目录的 .env
      2. 脚本上级目录的 .env（项目根）
      3. 当前工作目录的 .env
    这样无论从项目根还是从 m0-prototype/ 里跑，都能读到。
    """
    if path is None:
        here = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.join(here, ".env"),
            os.path.join(here, os.pardir, ".env"),
            os.path.join(os.getcwd(), ".env"),
        ]
        for cand in candidates:
            if os.path.exists(cand):
                path = cand
                break
        else:
            return
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k and k not in os.environ:
            os.environ[k] = v.strip().strip('"').strip("'")


def make_narrator() -> Narrator:
    llm = LLMNarrator()
    if llm.available:
        print(f"叙事层：LLM ({llm.model})")
        return llm
    print("叙事层：模板（未检测到 LLM_BASE_URL / LLM_API_KEY，功能不受影响）")
    return MockNarrator()


# ---------------------------------------------------------------------------
# 6. 一回合驱动
# ---------------------------------------------------------------------------

def apply_action(enc: Encounter, actor: str, action_type: str, text: str,
                 intent_text: str, forced_verdict: Optional[dict] = None,
                 intent_tag: Optional[str] = None) -> dict:
    """
    只做这一方的判定与状态变更，**不推进回合**。
    双向盲写要靠它把两边各算一次，再统一揭示。
    """
    perf = Performance(turn=enc.turn, actor=actor, action_type=action_type, text=text)

    if intent_text.strip() or intent_tag:
        enc.intents.append(Intent(turn=enc.turn, actor=actor,
                                  text=intent_text.strip() or "（未写明，只标记了在藏什么）",
                                  tag=intent_tag))

    verdict = forced_verdict or run_judgement(enc, actor, action_type)
    perf.judged = verdict
    enc.performances.append(perf)
    apply_effects(enc, actor, action_type, verdict)

    return {"verdict": verdict, "perf": perf}


def submit(enc: Encounter, actor: str, action_type: str, text: str,
           intent_text: str, narrator: Narrator,
           forced_verdict: Optional[dict] = None,
           intent_tag: Optional[str] = None) -> dict:
    """
    单边提交 + 推进回合（旧接口，测试与回放用）。
    正式对局走 stage / resolve_round 的双向盲写流程。
    forced_verdict 用于测试注入确定判定，正式对局不要传。
    """
    r = apply_action(enc, actor, action_type, text, intent_text,
                     forced_verdict, intent_tag)

    # 渲染「对面」看到的东西
    opp = enc.other(actor)
    narrative = narrator.render(enc, opp, action_type, r["verdict"], text)

    # 状态推进
    if enc.outcome is None and enc.turn >= enc.script.max_turns:
        enc.outcome = "timeout"
    enc.turn += 1
    enc.state = "RESOLVED" if enc.outcome else "AWAITING"

    return {"verdict": r["verdict"], "narrative": narrative,
            "note": f"已推进到第 {enc.turn} 回合"}


# ---- 双向盲写 ------------------------------------------------------------

def stage(enc: Encounter, side: str, action_type: str, text: str,
          intent: str = "", tag: Optional[str] = None) -> dict:
    """
    暂存一方的行动。**不结算、不揭示**。
    双方都提交后才一起翻开 —— 交出去的那一刻就收不回来了。
    """
    if enc.outcome:
        return {"ok": False, "error": "本局已结束"}
    if action_type not in ACTION_LABELS:
        return {"ok": False, "error": f"动作类型必须是 {list(ACTION_LABELS)}"}
    if side in enc.staged:
        return {"ok": False, "error": f"{side} 本轮已经提交过了"}
    enc.staged[side] = {"type": action_type, "text": text or "（沉默不语）",
                        "intent": intent, "tag": tag}
    return {"ok": True, "staged": side, "waitingFor": enc.pending}


def both_staged(enc: Encounter) -> bool:
    return "A" in enc.staged and "B" in enc.staged


def resolve_round(enc: Encounter) -> dict:
    """
    双方都提交后，一起结算这一轮。
    判定顺序固定 A→B，保证可复现。
    返回每方各自能看到的东西（互不包含对方的判定）。
    """
    if not both_staged(enc):
        return {"ok": False, "error": f"还差一方未提交：{enc.pending}"}

    order = ("A", "B")
    results = {}
    for side in order:
        st = enc.staged[side]
        results[side] = apply_action(enc, side, st["type"], st["text"],
                                     st["intent"], intent_tag=st.get("tag"))

    # 撞车检测：同一轮双方做了同一类动作
    if results["A"]["perf"].action_type == results["B"]["perf"].action_type:
        enc.collisions.append({
            "turn": enc.turn,
            "action_type": results["A"]["perf"].action_type,
        })

    enc.staged.clear()
    if enc.outcome is None and enc.turn >= enc.script.max_turns:
        enc.outcome = "timeout"
    enc.turn += 1
    enc.state = "RESOLVED" if enc.outcome else "AWAITING"

    return {"ok": True, "results": results,
            "collision": bool(enc.collisions and enc.collisions[-1]["turn"] == enc.turn - 1),
            "turn": enc.turn, "outcome": enc.outcome}


BAND_LABEL = {"critical": "大成功", "success": "成功",
              "partial": "差一点", "fail": "失败"}


def available_tags(script: Script, side: str) -> list[str]:
    """该方可以标记的「我在藏什么」。按归属过滤，防止乱选导致信息错位。"""
    out = []
    for tag in script.clue_tags:
        owner = script.tag_owner.get(tag)
        if owner in (None, side):
            out.append(tag)
    return out


def render_verdict(verdict: dict) -> str:
    """判定透明化：玩家可以不接受运气，但必须能接受明账。"""
    src = f"（{verdict['modifier_source']}）" if verdict["modifier"] else ""
    band = verdict.get("band")
    label = BAND_LABEL.get(band) if band else ("成功" if verdict["success"] else "失败")
    return (f"掷骰 {verdict['roll']}   DC {verdict['dc']}   "
            f"加值 {verdict['modifier']:+d}{src}  →  {label}")


# ---------------------------------------------------------------------------
# 7. 交互界面
#
#    设计原则：一屏之内必须能回答四个问题
#      我是谁？现在什么情况？对方说过什么？我掌握了什么？
#    凡是「该由玩家自己推断」的东西，界面一律不给。
# ---------------------------------------------------------------------------

W = 62


def clear() -> None:
    """真 tty 时用 ANSI 清屏；管道输出时用换行，避免脏字符。"""
    if sys.stdout.isatty():
        print("\033[2J\033[H", end="")
    else:
        print("\n" * 50)


def header(text: str) -> None:
    print("═" * W)
    print(f" {text}")
    print("═" * W)


def section(title: str) -> None:
    print(f"\n〔{title}〕")


def para(text: str, mark: Optional[str] = None) -> None:
    """按宽度折行。mark 给第一行加前缀，后续行对齐。"""
    text = (text or "").strip()
    pad = f"  {mark} " if mark else "  "
    cont = " " * (len(pad) - 1) + " "
    lines = textwrap.wrap(text, W - len(pad)) or [""]
    for i, line in enumerate(lines):
        print((pad if i == 0 else cont) + line)


def render_screen(enc: Encounter, side: str, narrative: Optional[str]) -> None:
    """
    单方视角界面。

    只呈现 side 有权知道的事：
      对方说过的话（纯文本，不给动作类型）、对方的判定结果、对方的气势，一律不出现。
    """
    role = getattr(enc.script, f"role_{side.lower()}")
    selfv = Channels.build_self_view(enc, side, enc.turn - 1)
    oppv = Channels.build_opponent_view(enc, side, enc.turn - 1)

    header(f"{enc.script.title} · 第 {enc.turn} 回合 · 你是「{role}」")

    section("此刻")
    para(selfv["premise"])

    if narrative:
        section("刚才")
        para(narrative)

    their = oppv["their_performances"]
    if their:
        section("对方说过 / 做过的")
        for p in their[-4:]:
            para(f"「{p['text']}」", "·")

    if selfv["your_clues"]:
        section("你察觉到的")
        for c in selfv["your_clues"]:
            para(c, "·")

    if selfv["your_intents"]:
        section("你没说出口的")
        for i in selfv["your_intents"][-2:]:
            para(i["text"], "·")

    print()
    print("─" * W)
    lev = selfv["your_leverage"]
    print(f" 你的气势 {'+' if lev >= 0 else ''}{lev}"
          f"     行动 {enc.turn}/{enc.script.max_turns}")
    print("─" * W)


def player_input(side: str, enc: Encounter) -> tuple[str, str, str, Optional[str]]:
    keys = list(ACTION_LABELS)
    print("\n【你这一回合怎么做？】")
    for i, k in enumerate(keys, 1):
        print(f"  {i}. {ACTION_LABELS[k]:　<10} DC {enc.script.dc.get(k, 13)}")
    print("  （动作类型只用于判定；具体怎么做，写在下面。）")
    while True:
        sel = input("\n选择 1-6：").strip()
        if sel.isdigit() and 1 <= int(sel) <= len(keys):
            action_type = keys[int(sel) - 1]
            break
        print("  请输入 1-6。")
    text = input("你怎么做 / 说什么：").strip() or "（沉默不语）"

    intent = input("你真实的打算（可留空；留空=没人能看穿你）：").strip()
    tag = None
    tags = available_tags(enc.script, side)
    if intent and tags:
        print("\n  你具体在藏哪一件事？（如果他识破你，可能被看出来）")
        print("    0. 不标记")
        for i, k in enumerate(tags, 1):
            print(f"    {i}. {enc.script.tag_labels.get(k, k)}")
        while True:
            sel = input(f"  选 0-{len(tags)}：").strip() or "0"
            if sel.isdigit() and 0 <= int(sel) <= len(tags):
                if int(sel) > 0:
                    tag = tags[int(sel) - 1]
                break
            print(f"  请输入 0-{len(tags)}。")
    return action_type, text, intent, tag


MOCK_AI_B = [
    ("threaten", "别过来！你身上带着刀，我看得出来。", "他是通缉犯的人，先吓住他", "hiding"),
    ("observe", "……你到底想从我这儿得到什么？", "得先看清他有没有同伙", "fake"),
    ("persuade", "救我，到了镇上我给你双倍的钱。", "先许个空头，等伤好再说", "money"),
    ("threaten", "你要真敢动手，那个山洞里的人不会放过你。", "我在诈他，赌他不敢赌", "fake"),
    ("help", "……水囊能给我吗？", "", None),
    ("persuade", "你不像坏人。但你也不像好人。", "试探他到底图什么", "money"),
]


def play(solo: bool, script: Optional[Script] = None) -> None:
    """
    双向盲写对局。
    每一轮：双方各提交一次（互相看不见）→ 一起翻开。
    """
    script = script or SCRIPT_ROAD
    narrator = make_narrator()
    enc = Encounter(
        enc_id=f"enc-{os.urandom(3).hex()}",
        script=script,
        seed=os.urandom(32),
    )
    if solo:
        enc.realness["B"] = "ai_fallback"
    ai_idx = 0
    last: dict[str, Optional[str]] = {"A": None, "B": None}

    clear()
    header(enc.script.title)
    print()
    para("这是一次「匿名对位」——你和另一个人被困在同一个场景里。")
    para("你们看到的东西不一样，谁也不知道对面是谁，甚至不知道对面是不是真人。")
    para("每一轮你们同时行动：交出去之后，就收不回来了。")
    if solo:
        para("你的对手由 AI 扮演。")
    else:
        para("双人同机：每次行动后把屏幕交给对方。往上滚看历史算作弊。")
    input("\n  按 Enter 开始……")

    while not enc.outcome and enc.turn <= enc.script.max_turns:
        # ---------- A 方提交 ----------
        clear()
        render_screen(enc, "A", last["A"])
        last["A"] = None
        at, txt, it, tg = player_input("A", enc)
        r = stage(enc, "A", at, txt, it, tg)
        if not r["ok"]:
            continue

        # ---------- B 方提交 ----------
        if solo:
            at2, txt2, it2, tg2 = MOCK_AI_B[min(ai_idx, len(MOCK_AI_B) - 1)]
            ai_idx += 1
            stage(enc, "B", at2, txt2, it2, tg2)
        else:
            clear()
            print("\n" * 6)
            header("请把屏幕交给 B")
            print("\n  A 已经写好了。按 Enter 交给对方……")
            input()
            clear()
            render_screen(enc, "B", last["B"])
            last["B"] = None
            at2, txt2, it2, tg2 = player_input("B", enc)
            r2 = stage(enc, "B", at2, txt2, it2, tg2)
            if not r2["ok"]:
                continue

        # ---------- 一起翻开 ----------
        res = resolve_round(enc)
        if not res["ok"]:
            continue
        pa = res["results"]["A"]["perf"]
        pb = res["results"]["B"]["perf"]

        def reveal(side: str, mine, theirs, handoff: str) -> None:
            clear()
            header(f"第 {res['turn'] - 1} 轮翻开" + ("  · 撞车" if res["collision"] else ""))
            print()
            section("你做了什么")
            para(f"「{mine.text}」", "·")
            print()
            section("他做了什么")
            para(f"「{theirs.text}」", "·")
            print()
            print("─" * W)
            print(" " + render_verdict(mine.judged))
            print("─" * W)
            if res["collision"]:
                print()
                para("（你们俩这一轮做了同一类的事。）")
            if handoff:
                input(f"\n  {handoff}")

        reveal("A", pa, pb, "" if solo else "请把屏幕交给 B，按 Enter……")
        if not solo:
            reveal("B", pb, pa, "请把屏幕交回 A，按 Enter……")

        last["A"] = pb.text
        last["B"] = pa.text

    # ---- 结算 + 揭面 ----
    clear()
    header("遭遇结束")
    print()
    para(f"结果：{enc.outcome or '时间耗尽'}")
    print(f"  判定记录 {len(enc.performances)} 次"
          f"     撞车 {len(enc.collisions)} 次")
    print()
    print("─" * W)
    print(" 揭面（可选）—— 这是本作唯一的社交展示位")
    print("─" * W)
    para("A：行商 · 深灰粗呢斗篷 / 皮质护腕 / 未换的旧靴", "·")
    para("B：" + ("（AI 扮演，无形象）" if solo else "军人 · 残破护甲 / 沾泥的绑腿"), "·")
    print()
    para("[原始文本已全量落库（当前为内存态，M1 接 MySQL）]")


# ---------------------------------------------------------------------------
# 8. 红队测试：自动化验证三通道零泄露
# ---------------------------------------------------------------------------

def redteam() -> int:
    """
    核心断言：在【未授权】状态下，A 的 intent 原文标记
    不得出现在任何发往 B 侧的视图 / prompt 中。
    """
    print("=" * 64)
    print("  红队测试：三通道隔离")
    print("=" * 64)

    failures = []

    # --- Case 1: 未授权时 intent 原文零出现 ---
    enc = Encounter(enc_id="redteam-1", script=SCRIPT_ROAD, seed=b"\x01" * 32)
    marker = "SECRET_MARKER_X7K9Q"
    enc.performances.append(
        Performance(turn=1, actor="A", action_type="persuade", text="我可以带你出山。")
    )
    enc.intents.append(Intent(turn=1, actor="A", text=f"我其实打算等他好了就抢他 {marker}"))

    for viewer in ("B",):
        for t in range(1, 4):
            view = Channels.build_opponent_view(enc, viewer, t)
            blob = json.dumps(view, ensure_ascii=False)
            if marker in blob:
                failures.append(f"Case1: intent 原文泄露到 {viewer} 的视图 (turn={t})")

    # --- Case 2: 观察失败也不得泄露 ---
    # 语义：B 藏了心思 → A 观察 B 但失败 → A 的视图里不能有 B 的 intent 原文
    enc2 = Encounter(enc_id="redteam-2", script=SCRIPT_ROAD, seed=b"\x02" * 32)
    enc2.intents.append(Intent(turn=1, actor="B",
                               text=f"B 的真实打算 {marker}", tag="hiding"))
    enc2.performances.append(
        Performance(turn=1, actor="A", action_type="observe", text="我看看他。",
                    judged={"success": False})
    )
    view = Channels.build_opponent_view(enc2, "A", 2)
    if marker in json.dumps(view, ensure_ascii=False):
        failures.append("Case2: 观察失败时 intent 仍泄露")
    if view["your_clues"]:
        failures.append("Case2: 观察失败却产生了线索")

    # --- Case 3: 观察成功只泄露摘要，不泄露原文标记 ---
    enc3 = Encounter(enc_id="redteam-3", script=SCRIPT_ROAD, seed=b"\x03" * 32)
    enc3.intents.append(Intent(turn=1, actor="B",
                               text=f"B 的真实打算 {marker} 收尾", tag="hiding"))
    enc3.performances.append(
        Performance(turn=1, actor="A", action_type="observe", text="我看看他。",
                    judged={"success": True})
    )
    apply_effects(enc3, "A", "observe", {"success": True})
    view = Channels.build_opponent_view(enc3, "A", 2)
    blob = json.dumps(view, ensure_ascii=False)
    if marker in blob:
        failures.append("Case3: 观察成功后泄露了 intent 原文（只应给摘要）")
    if not view["your_clues"]:
        failures.append("Case3: 观察成功后未产生任何线索（功能缺失）")
    # 观察方拿到线索后，被观察方不得反向看到任何东西
    back = Channels.build_opponent_view(enc3, "B", 2)
    if back["your_clues"]:
        failures.append("Case3: 线索反向泄露给了被观察方")

    # --- Case 4: build_opponent_view 的签名里不能有 intents ---
    import inspect
    params = inspect.signature(Channels.build_opponent_view).parameters
    if "intents" in params or "intent" in params:
        failures.append("Case4: build_opponent_view 签名接受 intent —— 隔离被绕过")

    # --- Case 5: 骰子可复现且与 actor 相关 ---
    e1 = Encounter(enc_id="dice", script=SCRIPT_ROAD, seed=b"\x04" * 32)
    a = e1.roll("A", "persuade")
    b = e1.roll("A", "persuade")
    c = e1.roll("B", "persuade")
    if a != b:
        failures.append("Case5: 骰子不可复现")
    if a == c:
        failures.append("Case5: 骰子与行动方无关（可被预测）")
    if not (1 <= a <= 20):
        failures.append("Case5: 骰子越界")

    # --- Case 6: 对方的动作类型不得下发给玩家 ---
    #     玩家该从「他说了什么」自己推断他在威胁还是在示弱。
    enc6 = Encounter(enc_id="redteam-6", script=SCRIPT_ROAD, seed=b"\x06" * 32)
    enc6.performances.append(
        Performance(turn=1, actor="B", action_type="threaten",
                    text="那个山洞里的人不会放过你。",
                    judged={"roll": 15, "dc": 15, "modifier": 0,
                            "modifier_source": "无", "total": 15, "success": True})
    )
    v6 = Channels.build_opponent_view(enc6, "A", 2)
    if any("action_type" in p for p in v6["their_performances"]):
        failures.append("Case6: 对方视图泄露了 action_type（等于公布答案）")
    if any("judged" in p for p in v6["their_performances"]):
        failures.append("Case6: 对方视图泄露了判定结果")
    if not v6["their_performances"]:
        failures.append("Case6: 对方表演文本丢失（隔离过度）")

    # --- Case 7: 不得看到对方的气势 ---
    enc7 = Encounter(enc_id="redteam-7", script=SCRIPT_ROAD, seed=b"\x07" * 32)
    enc7.leverage = {"A": 2, "B": 3}
    v7a = Channels.build_opponent_view(enc7, "A", 1)
    v7s = Channels.build_self_view(enc7, "A", 1)
    if v7a.get("your_leverage") != 2:
        failures.append("Case7: 自己的气势取值错误")
    blob7 = json.dumps(v7a, ensure_ascii=False) + json.dumps(v7s, ensure_ascii=False)
    if "leverage\": {" in blob7 or "'B': 3" in blob7:
        failures.append("Case7: 泄露了对方的 leverage 结构")
    if 3 in v7a.values() or 3 in v7s.values():
        failures.append("Case7: 对方的 leverage 数值 3 出现在 A 的视图里")

    # --- Case 8: 完整枚举对比 —— 视图字段白名单 ---
    allowed_opp = {"role", "premise", "their_performances", "your_clues", "your_leverage"}
    allowed_self = {"role", "premise", "your_performances", "your_intents",
                    "your_clues", "your_leverage"}
    if set(v7a) - allowed_opp:
        failures.append(f"Case8: opponent_view 出现未预期字段 {set(v7a) - allowed_opp}")
    if set(v7s) - allowed_self:
        failures.append(f"Case8: self_view 出现未预期字段 {set(v7s) - allowed_self}")

    print()
    if failures:
        for f in failures:
            print(f"  [FAIL] {f}")
        return 1
    print("  全部通过：")
    print("    · 未授权时 intent 原文零泄露")
    print("    · 观察失败不泄露 / 观察成功只给摘要")
    print("    · 视图构建函数签名不接受 intent")
    print("    · 骰子可复现、与行动方相关、值域正确")
    print("    · 对方的 action_type 与判定结果不下发")
    print("    · 对方的气势不泄露，只给自己的")
    print("    · 视图字段白名单，无未预期字段")
    return 0


def guard_test() -> int:
    """
    输出质量闸门单测。
    用例全部来自真实实测故障（MiniMax 拒答伪装成 200、幻觉乱码 hbarsole/_body）。
    """
    print("=" * 64)
    print("  输出质量闸门（因为 sensitive 字段不可信，只能自己判）")
    print("=" * 64)
    cases = [
        ("我无法创作这个内容。",                   True,  "拒答（实测 L2 场景真实返回）"),
        ("很抱歉，我不能继续这个场景。",             True,  "拒答"),
        ("As an AI, I can't write that.",        True,  "拒答（英文）"),
        ("雨夜的山道上，你听见_body滚落的声音。",     True,  "幻觉乱码（实测真实出现）"),
        ("他hbarsole凭借最后一丝力气伸出手。",       True,  "幻觉乱码（实测真实出现）"),
        ("",                                     True,  "空正文"),
        ("好。",                                  True,  "过短"),
        ("雨点砸在脸上，你勉强睁开眼。远处有人牵着马。", False, "正常中文叙事"),
        ("NPC 站在 DC 15 的房门前，没有回头。",      False, "全大写缩写应放行"),
        ("你没法确定他说的是真是假，但他的手在抖。",    False, "含「没法」但非拒答（长句）"),
    ]
    failures = []
    for text, expect_bad, why in cases:
        issue = output_issue(text)
        got_bad = issue is not None
        mark = "OK " if got_bad == expect_bad else "FAIL"
        if got_bad != expect_bad:
            failures.append(f"{why}: 期望{'拦截' if expect_bad else '放行'}，实得 {issue!r}")
        show = (text[:28] + "…") if len(text) > 28 else text
        print(f"  [{mark}] {'拦' if got_bad else '放'} | {why:<26} | {show!r}")
        if got_bad:
            print(f"       └─ {issue}")
    print()
    if failures:
        for f in failures:
            print(f"  [FAIL] {f}")
        return 1
    print("  全部通过：拒答、幻觉乱码、空/过短都能拦住；正常叙事不误伤。")
    return 0


def matrix_test() -> int:
    """
    验证 intent 三层博弈的机制联动：
      线索 = 对方【填了 intent】 AND 我方【观察判定成功】
    这是「填 intent 是自愿下的赌注」这一设计成立的必要条件。
    """
    print("=" * 64)
    print("  机制矩阵：intent 填写 × 观察判定 → 是否产生线索")
    print("=" * 64)
    failures = []

    def run(fill_intent: bool, observe_success: bool) -> list[str]:
        enc = Encounter(enc_id=f"mx-{fill_intent}-{observe_success}",
                        script=SCRIPT_ROAD, seed=b"\x09" * 32)
        # 第 1 回合：B 行动并可选填 intent
        enc.state = "PENDING_B"
        submit(enc, "B", "persuade", "救我，到了镇上给你双倍的钱。",
               "我身上一个铜板都没有，等他走了再说" if fill_intent else "",
               MockNarrator(),
               forced_verdict={"roll": 12, "dc": 13, "modifier": 0,
                               "modifier_source": "无", "total": 12,
                               "margin": -1, "band": "partial", "success": False},
               intent_tag="money" if fill_intent else None)
        # 第 2 回合：A 观察 B，判定结果由参数控制
        enc.state = "PENDING_A"
        submit(enc, "A", "observe", "我不动声色地打量他。", "", MockNarrator(),
               forced_verdict={"roll": 15, "dc": 12, "modifier": 0,
                               "modifier_source": "无", "total": 15,
                               "margin": 3 if observe_success else -6,
                               "band": "success" if observe_success else "fail",
                               "success": observe_success})
        return enc.disclosed.get("A", [])

    print()
    print(f"{'intent':<10}{'观察':<8}{'线索':<32}期望")
    for fill in (False, True):
        for ok in (False, True):
            clues = run(fill, ok)
            expect = fill and ok
            got = bool(clues)
            mark = "OK " if got == expect else "FAIL"
            print(f"{'填了' if fill else '没填':<10}{'成功' if ok else '失败':<8}"
                  f"{('有 ' + str(len(clues)) + ' 条') if clues else '（无）':<32}"
                  f"{'有' if expect else '无'}  [{mark}]")
            if got != expect:
                failures.append(f"intent={fill} observe={ok} 期望{'有' if expect else '无'}线索，实得{got}")
            if len(clues) > 1:
                failures.append(f"intent={fill} observe={ok} 线索重复追加 {len(clues)} 条")

    print()
    if failures:
        for f in failures:
            print(f"  [FAIL] {f}")
        return 1
    print("  联动成立：线索 = 「对方填了 intent」且「我方观察成功」")
    print("  → 填 intent 是自愿下的赌注；不填则无从被识破。")
    return 0


# ---------------------------------------------------------------------------
# 9. 自检
# ---------------------------------------------------------------------------

def selftest() -> int:
    enc = Encounter(enc_id="self", script=SCRIPT_ROAD, seed=b"\x05" * 32)
    narrator = MockNarrator()
    ok = True
    for i in range(12):
        if enc.outcome:
            break
        side = enc.pending
        submit(enc, side, "observe", f"第{i}次", "", narrator)
        if enc.state not in ("AWAITING", "PENDING_A", "PENDING_B", "RESOLVED"):
            print(f"  [FAIL] 非法状态 {enc.state}")
            ok = False
            break
    print(f"  回合推进至 {enc.turn}，状态 {enc.state}，结局 {enc.outcome}")
    print(f"  判定记录 {len(enc.performances)} 条")
    print("  [OK] 状态机未卡死" if ok else "  [FAIL] 状态机异常")
    return 0 if ok else 1


def blind_test() -> int:
    """
    双向盲写测试。
    验证：提交即锁定、双方都到齐才揭示、判定可复现、撞车能被识别。
    """
    print("=" * 64)
    print("  双向盲写（Simultaneous Blind Write）")
    print("=" * 64)
    failures = []

    # --- 1. 单方提交后不揭示 ---
    enc = Encounter(enc_id="blind-1", script=SCRIPT_ROAD, seed=b"\x0b" * 32)
    r1 = stage(enc, "A", "threaten", "我握住了刀柄。")
    if not r1["ok"]:
        failures.append(f"stage A 失败：{r1}")
    if both_staged(enc):
        failures.append("只提交一方就判定双方到齐")
    if enc.performances:
        failures.append("单方提交后就已经结算（应暂存）")
    if r1.get("waitingFor") != "B":
        failures.append(f"waitingFor 应为 B，实得 {r1.get('waitingFor')}")

    # --- 2. 同一方重复提交应被拒 ---
    r2 = stage(enc, "A", "persuade", "我又说了一句。")
    if r2["ok"]:
        failures.append("同一方重复提交未被拒绝")

    # --- 3. 双方到齐后一起结算 ---
    stage(enc, "B", "threaten", "我不退。")
    if not both_staged(enc):
        failures.append("双方都提交后未判定为到齐")
    res = resolve_round(enc)
    if not res["ok"]:
        failures.append(f"resolve_round 失败：{res}")
    if len(enc.performances) != 2:
        failures.append(f"结算后应有 2 条表演记录，实得 {len(enc.performances)}")
    if res.get("collision") is not True:
        failures.append("双方都用了 threaten，撞车未被识别")
    if enc.staged:
        failures.append("结算后暂存未清空")

    # --- 4. 双方各自只拿到自己的判定 ---
    if "results" not in res or set(res["results"]) != {"A", "B"}:
        failures.append("结算结果未按方返回")
    else:
        for side in ("A", "B"):
            v = res["results"][side]["verdict"]
            expect = {"roll", "dc", "modifier", "modifier_source",
                      "total", "margin", "band", "success"}
            if set(v) != expect:
                failures.append(f"{side} 的判定结构异常 {set(v)}")

    # --- 5. 同样的提交顺序得到同样的结果（可复现）---
    e1 = Encounter(enc_id="blind-r", script=SCRIPT_ROAD, seed=b"\x0c" * 32)
    stage(e1, "A", "persuade", "x"); stage(e1, "B", "observe", "y")
    r_a = resolve_round(e1)["results"]["A"]["verdict"]["roll"]

    e2 = Encounter(enc_id="blind-r", script=SCRIPT_ROAD, seed=b"\x0c" * 32)
    stage(e2, "A", "persuade", "x"); stage(e2, "B", "observe", "y")
    r_b = resolve_round(e2)["results"]["A"]["verdict"]["roll"]
    if r_a != r_b:
        failures.append("相同 seed 与提交得到不同骰值（不可复现）")

    # --- 6. 回合只推进一次，不是两次 ---
    e3 = Encounter(enc_id="blind-t", script=SCRIPT_ROAD, seed=b"\x0d" * 32)
    stage(e3, "A", "help", "x"); stage(e3, "B", "help", "y")
    resolve_round(e3)
    if e3.turn != 2:
        failures.append(f"一轮双向应只推进 1 回合，实得 turn={e3.turn}")

    print()
    if failures:
        for f in failures:
            print(f"  [FAIL] {f}")
        return 1
    print("  全部通过：")
    print("    · 单方提交只暂存，不结算不揭示")
    print("    · 同一方不能重复提交")
    print("    · 双方到齐才一起翻开，撞车可识别")
    print("    · 每方只拿到自己的判定，互不含对方")
    print("    · 相同 seed + 相同提交 → 相同结果（可复现）")
    print("    · 一轮只推进 1 个回合")
    return 0


# ---------------------------------------------------------------------------
# 10. Agent 模式
#
#     让任意 coding agent（Claude Code / DeepSeek Harness / WorkBuddy / ...）
#     都能主持一局。引擎只输出**结构化状态**，叙事由 agent 自己写。
#
#     这样做的两个好处：
#       1. 不需要 API key —— 玩家用自己的 agent 订阅
#       2. agent 就是 GM，天然有自然语言能力
#
#     代价：agent 同时知道双方视角，它的"自觉"是隔离的最后一环。
#     缓解：view 命令只返回该方可见的东西，agent 拿不到多余信息。
# ---------------------------------------------------------------------------

SCRIPT_MANOR = Script(
    script_id="ln-manor-01",
    title="雨夜·十九点四十",
    role_a="槐荫路17号守夜人",
    role_b="寻人的妹妹",
    premise_a=(
        "雨从六月十号下到现在，槐荫路的积水漫过了第三级台阶。你是这栋楼的守夜人，\n"
        "在这儿看了十九年门。楼里原本八户，现在只剩两户还亮着灯。\n"
        "三楼西头那间空了五年，你仍然每天晚上十九点四十上去，把台灯拧亮，\n"
        "坐十七分钟，再拧灭。有人问起，你就说是在照看线路。\n"
        "今夜十九点三十五分，楼下铁门被人拍响了——不喊名字，只一下一下地拍，像在数。\n"
        "你凑到猫眼前，看见一件湿透的男式雨衣，帽檐压得很低，脚上是一双沾了红泥的胶鞋。\n"
        "你手里有一把黄铜钥匙，能开这栋楼除三楼西头以外的每一道门。"
    ),
    premise_b=(
        "你姐姐沈砚五年前在这栋楼里失踪，警方说她是自己走的。\n"
        "每年六月十二，你都收到一封从槐荫路17号寄出的空白信——信封有邮戳，里面一个字都没有。\n"
        "今年你没有等信，你自己来了，站在雨里看三楼西头的窗。\n"
        "十九点三十九，那盏灯亮了；十九点五十六，灭了。一分不差。\n"
        "你口袋里有两样东西：姐姐最后一封信，落款「六月十二，十九点四十」，\n"
        "末行写着「这天晚上我没下来，就别再等我了」；\n"
        "还有一小罐糖水山楂——她每年冬天都寄两罐，说是你小时候爱吃的那个牌子。\n"
        "你要问的是：那天十九点四十，三楼到底有没有人。"
    ),
    dc={"persuade": 15, "threaten": 12, "observe": 13,
        "attack": 11, "help": 9, "leave": 11},
    max_turns=12,
    item_pool=("黄铜钥匙", "煤油灯", "座钟", "消防梯", "糖水山楂罐头",
               "租客登记簿", "空白信封", "站前广场汽车票根", "男式雨衣",
               "摔碎的手表", "储物间"),
    clue_tags={
        # —— 守夜人（A）的秘密 ——
        "lamp_deal": "他每晚十九点四十点灯，不是为了线路。那个钟点，是别人交代给他的。",
        "deposit": "楼里的押金账对不上，少了八千。这笔钱有人动过。",
        "body": "她不是倒在那儿被发现的。有人动过她。",
        "watch": "她腕上那块表，表带扣是反的——不是她自己戴上去的。",
        "ladder": "后院消防梯的第七级踏板，1997 年就锈透了。报修过一次，被楼里驳了。",
        # —— 妹妹（B）的秘密 ——
        "letter_date": "那封信的纸边发脆，折痕很深，不是今年的纸。落款日期是后改的。",
        "station": "她那天确实到过城里。站前广场。",
        "redclay": "她鞋底沾的是站前广场的红胶泥。槐荫路这一带没有这种土。",
        "blank_letter": "那五年的空白信，她一直收着。她没告诉过任何人。",
        # —— 双方都可能触及 ——
        "hawthorn": "储物间角落有一罐糖水山楂，放了很久，标签都褪了，没人动过。",
        "raincoat": "那件男式雨衣的尺码，和她的身量不符。",
    },
    tag_labels={
        "lamp_deal": "点灯是别人交代的",
        "deposit": "我动过那八千块押金",
        "body": "我拖过她",
        "watch": "那块表是我给她戴上的",
        "ladder": "第七级踏板是我没修成的",
        "letter_date": "信上的日期是我改的",
        "station": "那天我在站前广场",
        "redclay": "我鞋上沾了站前广场的泥",
        "blank_letter": "五年的空白信我都收着",
        "hawthorn": "我在找那罐糖水山楂",
        "raincoat": "那件雨衣不是她的",
    },
    tag_owner={
        "lamp_deal": "A", "deposit": "A", "body": "A", "watch": "A", "ladder": "A",
        "letter_date": "B", "station": "B", "redclay": "B", "blank_letter": "B",
        "hawthorn": None, "raincoat": None,
    },
)

SCRIPTS = {SCRIPT_ROAD.script_id: SCRIPT_ROAD, SCRIPT_MANOR.script_id: SCRIPT_MANOR}


def enc_to_dict(enc: Encounter) -> dict:
    return {
        "encId": enc.enc_id,
        "scriptId": enc.script.script_id,
        "seed": base64.b64encode(enc.seed).decode(),
        "state": enc.state,
        "turn": enc.turn,
        "outcome": enc.outcome,
        "leverage": enc.leverage,
        "realness": enc.realness,
        "disclosed": enc.disclosed,
        "performances": [
            {"turn": p.turn, "actor": p.actor, "action_type": p.action_type,
             "text": p.text, "judged": p.judged}
            for p in enc.performances
        ],
        "intents": [
            {"turn": i.turn, "actor": i.actor, "text": i.text} for i in enc.intents
        ],
        "staged": enc.staged,
        "collisions": enc.collisions,
    }


def enc_from_dict(d: dict) -> Encounter:
    script = SCRIPTS.get(d["scriptId"])
    if script is None:
        raise SystemExit(f"未知脚本 {d['scriptId']}，可用：{list(SCRIPTS)}")
    enc = Encounter(
        enc_id=d["encId"], script=script,
        seed=base64.b64decode(d["seed"]),
        state=d["state"], turn=d["turn"],
    )
    enc.outcome = d.get("outcome")
    enc.leverage = d["leverage"]
    enc.realness = d.get("realness", enc.realness)
    enc.disclosed = d.get("disclosed", {"A": [], "B": []})
    enc.performances = [Performance(**p) for p in d.get("performances", [])]
    enc.intents = [Intent(**i) for i in d.get("intents", [])]
    enc.staged = d.get("staged", {})
    enc.collisions = d.get("collisions", [])
    return enc


def _last_verdict_text(enc: Encounter, side: str) -> Optional[str]:
    """该方最近一次已结算行动的判定明账。只含自己的。"""
    mine = [p for p in enc.performances if p.actor == side and p.judged]
    if not mine:
        return None
    return render_verdict(mine[-1].judged)


def agent_view(enc: Encounter, side: str) -> dict:
    """
    只返回 side 有权知道的事。
    不含：对方的动作类型、对方的判定结果、对方的气势、任何 Intent。
    """
    opp = enc.other(side)
    oppv = Channels.build_opponent_view(enc, side, enc.turn - 1)
    selfv = Channels.build_self_view(enc, side, enc.turn - 1)
    return {
        "title": enc.script.title,
        "turn": enc.turn,
        "maxTurns": enc.script.max_turns,
        "you": side,
        "yourRole": getattr(enc.script, f"role_{side.lower()}"),
        "theirRole": getattr(enc.script, f"role_{opp.lower()}"),
        "premise": selfv["premise"],
        "theirPerformances": [p["text"] for p in oppv["their_performances"]],
        "yourClues": selfv["your_clues"],
        "yourIntents": [i["text"] for i in selfv["your_intents"]],
        "yourLeverage": selfv["your_leverage"],
        "pending": enc.pending,
        "waitingFor": enc.pending,
        "yourLastVerdict": _last_verdict_text(enc, side),
        "finished": bool(enc.outcome),
        "outcome": enc.outcome,
        "dc": enc.script.dc,
        "actionTypes": list(ACTION_LABELS),
        "tagOptions": [
            {"tag": tg, "label": enc.script.tag_labels.get(tg, tg)}
            for tg in available_tags(enc.script, side)
        ],
    }


def agent_cmd(cmd: str, state_path: str, side: str, action_type: Optional[str],
              text: Optional[str], intent: str, realness: str,
              args_tag: Optional[str] = None, script_id: str = SCRIPT_ROAD.script_id) -> int:
    if cmd == "start":
        script = SCRIPTS.get(script_id, SCRIPT_ROAD)
        enc = Encounter(
            enc_id=f"enc-{os.urandom(3).hex()}",
            script=script,
            seed=os.urandom(32),
        )
        enc.realness[enc.other(side)] = realness
        json.dump(enc_to_dict(enc), open(state_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(json.dumps({"ok": True, "cmd": "start", "view": agent_view(enc, side)},
                         ensure_ascii=False, indent=1))
        return 0

    if not os.path.exists(state_path):
        print(json.dumps({"ok": False, "error": f"状态文件不存在：{state_path}。先跑 --agent start"},
                         ensure_ascii=False))
        return 1
    enc = enc_from_dict(json.load(open(state_path, encoding="utf-8")))

    if cmd == "view":
        print(json.dumps({"ok": True, "cmd": "view", "view": agent_view(enc, side)},
                         ensure_ascii=False, indent=1))
        return 0

    if cmd == "status":
        print(json.dumps({"ok": True, "cmd": "status", "state": enc_to_dict(enc)},
                         ensure_ascii=False, indent=1))
        return 0

    if cmd == "stage":
        r = stage(enc, side, action_type or "", text or "", intent, args_tag)
        if not r.get("ok"):
            print(json.dumps(r, ensure_ascii=False))
            return 1
        json.dump(enc_to_dict(enc), open(state_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(json.dumps({
            "ok": True, "cmd": "stage", "staged": side,
            "waitingFor": r["waitingFor"],
            "note": ("双方都已提交，可以 commit 揭示" if r["waitingFor"] is None
                     else f"等待 {r['waitingFor']} 提交"),
        }, ensure_ascii=False, indent=1))
        return 0

    if cmd == "commit":
        r = resolve_round(enc)
        if not r.get("ok"):
            print(json.dumps(r, ensure_ascii=False))
            return 1
        json.dump(enc_to_dict(enc), open(state_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(json.dumps({
            "ok": True, "cmd": "commit",
            "turn": r["turn"], "outcome": r["outcome"],
            "collision": r["collision"],
            "note": ("双方这一轮做了同一类动作，会撞车" if r["collision"] else None),
            "nextView": agent_view(enc, side),
        }, ensure_ascii=False, indent=1))
        return 0

    if cmd == "act":
        if enc.outcome:
            print(json.dumps({"ok": False, "error": "本局已结束"}, ensure_ascii=False))
            return 1
        if enc.pending != side:
            print(json.dumps({"ok": False,
                              "error": f"现在轮到 {enc.pending}，不是 {side}"},
                             ensure_ascii=False))
            return 1
        if action_type not in ACTION_LABELS:
            print(json.dumps({"ok": False,
                              "error": f"动作类型必须是 {list(ACTION_LABELS)}"},
                             ensure_ascii=False))
            return 1

        res = submit(enc, side, action_type, text or "（沉默不语）", intent,
                     MockNarrator())
        json.dump(enc_to_dict(enc), open(state_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(json.dumps({
            "ok": True,
            "cmd": "act",
            "actor": side,
            "verdict": res["verdict"],
            "verdictText": render_verdict(res["verdict"]),
            "nextTurn": enc.pending,
            "nextView": agent_view(enc, enc.pending),
        }, ensure_ascii=False, indent=1))
        return 0

    print(json.dumps({"ok": False, "error": f"未知命令 {cmd}；可用 start/view/act/status"},
                     ensure_ascii=False))
    return 1


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description="M0 原型：三通道双盲 Encounter")
    ap.add_argument("--play", action="store_true", help="双人 hot-seat 游玩")
    ap.add_argument("--solo", action="store_true", help="单人，B 由 mock 回应")
    ap.add_argument("--redteam", action="store_true", help="三通道隔离红队测试")
    ap.add_argument("--matrix", action="store_true", help="intent 联动机制矩阵测试")
    ap.add_argument("--blindtest", action="store_true", help="双向盲写测试")
    ap.add_argument("--guardtest", action="store_true", help="输出质量闸门测试")
    ap.add_argument("--selftest", action="store_true", help="状态机与判定自检")
    ap.add_argument("--agent", metavar="CMD",
                    help="agent 模式：start | stage | commit | view | act | status（输出 JSON）")
    ap.add_argument("--state", default=".enc_state.json", help="agent 模式状态文件")
    ap.add_argument("--side", default="A", help="人类玩家是哪一方（A/B）")
    ap.add_argument("--type", dest="action_type", help="动作类型")
    ap.add_argument("--text", help="表演内容")
    ap.add_argument("--intent", default="", help="内心（可选）")
    ap.add_argument("--tag", default=None, help="你在藏什么（可选，决定被看穿时暴露什么）")
    ap.add_argument("--script", default="ln-road-01", help="副本 ID，见 SCRIPTS")
    ap.add_argument("--realness", default="ai_fallback", help="对方是真人是机")
    args = ap.parse_args()

    if args.agent:
        return agent_cmd(args.agent, args.state, args.side, args.action_type,
                         args.text, args.intent, args.realness, args.tag,
                         args.script)

    if args.redteam:
        return redteam()
    if args.matrix:
        return matrix_test()
    if args.blindtest:
        return blind_test()
    if args.guardtest:
        return guard_test()
    if args.selftest:
        return selftest()
    if args.play or args.solo:
        play(args.solo, SCRIPTS.get(args.script, SCRIPT_ROAD))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
