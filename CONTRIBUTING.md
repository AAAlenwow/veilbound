# 参与方式

项目在 **M0** 阶段——最小、最松的时候。没有贡献流程的官僚主义，也没有稳定的 API。

> 仓库已开源。玩法机制本身不受版权保护（[参考](https://dev.americanbar.org/groups/intellectual_property_law/resources/landslide/archive/why-videogame-rules-are-not-expression-protected-copyright-law/)），
> 所以设计归属不靠保密，靠 [AUTHORS.md](AUTHORS.md) 里的公开记录——谁在什么时候提出了什么。
> 引用这些机制时请注明出处。

## 先跑起来

不需要 LLM、不需要联网、不需要装依赖：

```bash
cd m0-prototype
python3 m0.py --play --solo    # 玩一局
python3 m0.py --redteam        # 三通道隔离测试
```

读代码从 `m0.py` 开始，单文件约 894 行，没有框架包袱。关键位置：

| 概念 | 位置 |
|---|---|
| 三通道隔离（安全边界） | `Channels.build_opponent_view` |
| 确定性骰子 | `Encounter.roll` |
| 输出质量闸门 | `output_issue` |
| 叙事层（可替换） | `Narrator` / `LLMNarrator` |

## 想做什么，先开 issue

不用先写代码。开 issue 说一句"我想做 X"就行。特别是下面这些，非常欢迎：

### 高优先级

- **VRM 角色原型**：一个 VRM 模型 + `emotion` 字段驱动表情 + TTS 口型。见 [3d-avatar.md](docs/3d-avatar.md)。哪怕只是个网页 demo 也极有价值。
- **叙事事件白名单**：修 P0 缺陷（narrator 会描述规则引擎没产生的结果）
- **自由文本 → 结构化动作**：意图解析 + 校验失败追问

### 中等优先级

- **写一个副本**：一个 Encounter 模板 + 判定 DC 表。不需要写代码，写 YAML/JSON 就够
- **异步 FSM**：行动点、TTL、AI 无感接管
- **后端落地**：Spring Boot + MySQL，`run_event` append-only

### 特别想要的

- **你把 M0 玩 5 局，然后告诉我哪里不好玩。** 这比任何代码贡献都有用。这是项目唯一真正的风险点。

## 提交代码

- 小改动直接提 PR，不需要先开 issue
- 大改动（架构层面）先开 issue 讨论，避免白做
- 测试：如果你改了 `Channels`、`Encounter.roll`、`output_issue` 中的任何一个，**必须**跑通四项目测试：

```bash
python3 m0.py --selftest && python3 m0.py --redteam && \
python3 m0.py --matrix && python3 m0.py --guardtest
```

## 几条硬约束（改动前请先读）

这些不是风格偏好，是踩过坑之后定下的：

1. **判定不能进 LLM。** 骰子必须确定性可复现。理由见 [DEVLOG](docs/DEVLOG.md) 第一节。PvP 场景下 AI 裁定无法自证清白。
2. **`build_opponent_view` 的签名里不能出现 Intent。** 隔离靠签名，不靠提示词。`--redteam` 会反射检查。
3. **物品只能来自白名单**，AI 只能选不能造。否则生成式内容会击穿经济系统。
4. **降级必须显式。** LLM 挂了要能继续跑，但必须打印原因，不能静默吞掉。
5. **结算必须幂等。** 异步系统天然会重复触发（超时重试、消息重投）。

## 不要做的事

- 不要给 `m0.py` 加框架/依赖。它现在是零依赖单文件，这是刻意的——M1 会重写。
- 不要提交 `.env`。里面有 API key，已被 `.gitignore` 排除。
- 不要提交生成式 AI 直接产出的美术资源（版权和 Steam 披露都有坑）。

## 语言

代码注释中文或英文都行，文档用中文。issue 用什么语言都行。
