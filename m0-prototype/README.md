# M0 原型 · 三通道双盲 Encounter

最小可玩切片，只用来验证三个假设。**不是产品**，M1 会用 Spring Boot 重建。

## 跑起来

```bash
cd m0-prototype

python3 m0.py --play          # 双人同机轮流（hot-seat，每次行动后把屏幕交给对方）
python3 m0.py --play --solo   # 单人，B 由 mock 自动回应
```

五项自动化测试（**都不需要 LLM、不需要联网**）：

```bash
python3 m0.py --selftest    # 状态机会不会卡死
python3 m0.py --redteam     # 三通道是否零泄露
python3 m0.py --matrix      # intent 联动机制是否成立
python3 m0.py --guardtest   # 输出质量闸门（拒答/乱码/空正文）
```

## LLM 配置（可选）

不配置也能跑，叙事层会自动降级为模板，所有测试照常通过。

要接真实模型：

```bash
cp ../.env.example .env      # 或直接把 .env 放在项目根目录，也能自动找到
# 编辑 .env，填入你自己的 key 和端点
```

```bash
LLM_BASE_URL=https://api.minimax.chat/v1
LLM_MODEL=MiniMax-M2
LLM_API_KEY=<你的 key>
```

`m0.py` 启动时按「脚本目录 → 上级目录 → 当前工作目录」的顺序查找 `.env`，
所以从项目根或从 `m0-prototype/` 里跑都能读到。真实环境变量优先于 `.env`。

> `.env` 已被 `.gitignore` 排除。**不要把 key 提交上去，也不要贴到 issue 里。**

## ★ MiniMax-M2 实测记录（这些结论直接改变了架构）

### 1. `sensitive` 字段不可信，不能当审核用

实测三个梯度场景，`input_sensitive` / `output_sensitive` **全部返回 `False`**：

| 场景 | sensitive | 实际行为 |
|---|---|---|
| 推下山崖 + 劫财 | False | 正常生成 |
| 扎腿逼供 | False | **返回「我无法创作这个内容。」** |
| 割喉 + 死亡 | False | 通过，但被大幅软化 |

**拒答时 sensitive 也是 False。** 所以绝不能用这个字段做自动化判断。

### 2. 拒答会伪装成正常响应

`'我无法创作这个内容。'` 是 **HTTP 200 + finish_reason=stop + content 非空**。
不检测就会原样展示给玩家 —— NPC 会对玩家说"我无法创作这个内容"。

→ 已实现 `output_issue()` 闸门，命中则重试一次，仍失败则**显式降级并打印原因**。

### 3. 拦截没有规律

同样是暴力，扎腿逼供被拒，割喉反而通过。**无法预测什么会被拦**。
→ 结论：内容策略不能依赖模型内置对齐，必须自建规则。

### 4. 模型会悄悄软化

要求写"直白血腥"，返回的是"荒原的风很快会吹干血迹"这种留白体。
→ 无限流要的残酷感会被系统性地稀释，这是需要主动对抗的默认倾向。

### 5. `reasoning_split: true` 必须开

M2.x **忽略** `enable_thinking=false` 和 `thinking=disabled`（见
[openclaw issue #122598](https://github.com/openclaw/openclaw/issues/122598)）。
不开 `reasoning_split` 时，`<think>` 块会混在 `content` 里污染叙事。

| 参数 | content 内容 |
|---|---|
| 默认 | 推理 + 正文混杂（98 字符含推理） |
| `reasoning_split: true` | **只有正文**（45 字符），推理进 `reasoning_content` |

### 6. 幻觉乱码

实测出现过 `hbarsole`、`_body` 这类从中文里冒出的英文 token。
→ 已由 `latin_garbage()` 拦截（全大写缩写如 NPC/DC 放行）。

## 已验证

| 假设 | 怎么验的 | 结果 |
|---|---|---|
| 状态机不卡死 | `--selftest` 连跑 10 回合 | 推进至 RESOLVED，无非法状态 |
| intent 原文零泄露 | `--redteam` Case1 | 未授权时 0 次出现 |
| 观察失败不泄露 | `--redteam` Case2 | 0 次出现，且无线索 |
| 观察成功只给摘要 | `--redteam` Case3 | 有线索、无原文、不反向泄露 |
| 隔离靠签名不靠 prompt | `--redteam` Case4 反射检查 | `build_opponent_view` 不接受 intent |
| 骰子可复现不可预测 | `--redteam` Case5 | 同输入同结果，跨行动方不同 |
| intent 联动机制 | `--matrix` 2×2 | 线索 = 填了 intent 且观察成功 |
| 拒答/乱码/空正文能拦 | `--guardtest` 10 例 | 全通过，正常叙事不误伤 |
| 闸门接入 render | 注入假 `_call` | 持续拒答→重试1次→显式降级 |

**真实 LLM 端到端**（8 回合）：耗时 **8.4s/次**，**287 completion_tokens/次**（含推理），敏感命中 0。
按异步玩法完全可接受；成本量级极低。

## 没验的（诚实标注）

- **好不好玩** —— 仍是 M0 唯一真目标，得你自己玩 5 局判断
- **AI 兜底扮演的真实感** —— 当前 B 是写死的 3 句 mock
- **持久化** —— 内存态，M1 接 MySQL
- **意图解析** —— 当前用动作类型选项代替自由文本
- **内容审核** —— 只有输出闸门，输入侧完全没做；`performance.text` 已全量保留备用
- **叙事与状态一致性** —— 实测发现过：观察判定失败，叙事却写成"商人驱马走了"（但状态里商人还在）。这是待修的 P0 问题，见下

## 已知 P0 问题

**叙事可以用状态暗示不存在的结果。**
实测第 1 回合：A 观察失败 → 叙事写成"他拉动缰绳，驱马向前去了"，但状态里 A 仍在原地。
玩家会被误导。修法：给 narrator 明确约束「判定失败只表示你没获得信息，不得改变对方的位置或行为」，
并在 M1 引入「叙事事件白名单」——narrator 只能描述规则引擎已产生的事件。

## 关键代码位置

| 概念 | 位置 | 说明 |
|---|---|---|
| 三通道隔离 | `Channels.build_opponent_view` | **签名里没有 Intent**，这是安全边界 |
| 确定性骰子 | `Encounter.roll` | `HMAC(seed, encId:turn:actor:action)` |
| 判定透明化 | `render_verdict` | 摊开 roll/DC/加值/来源 |
| 授权泄露 | `apply_effects` | 观察成功才给摘要，且幂等 |
| 输出闸门 | `output_issue` / `latin_garbage` | 因 sensitive 不可信而自建 |
| 叙事层可替换 | `Narrator` / `MockNarrator` / `LLMNarrator` | LLM 挂了必须降级 |

## M1 要做的（按优先级）

1. **修 P0**：叙事事件白名单，narrator 不得描述规则引擎未产生的结果
2. Spring Boot + MySQL，Encounter 落库（`run_event` append-only，可回放）
3. **结算幂等**：`(encId, turn, actor)` 幂等键（异步系统天然会重复触发结算）
4. 自由文本 → 结构化动作的意图解析（JSON schema 约束 + 校验失败追问）
5. 异步 FSM：行动点、TTL、AI 无感接管（验收：AI 接管前后结算数值完全等价）
