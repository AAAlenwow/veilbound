# 3D 角色与演出方案

> 目标：把"文字叙事"升级成"角色在对你说话"，且不炸成本、不炸延迟。
> 结论先行：**可行，但不要把 3D 做成独立系统，要把它做成叙事层的第二个输出。**

---

## 1. 核心判断

### 判断一：3D 是叙事层的「第二个输出」，不是另建一套引擎

现在服务端返回的是纯文本。加 3D 之后只多一个结构：

```json
{
  "text": "雨点砸在脸上，你勉强睁开眼。",
  "emotion": "fear",
  "intensity": 0.7,
  "pause_ms": 600,
  "gesture": "step_back"
}
```

客户端拿这个 + TTS 音频，用 `three.js` + `three-vrm` 实时驱动角色表情和口型。

**不需要预渲染视频。** 这是关键——一旦走预渲染，成本和时间会失控（见 §5）。

### 判断二：不要做 3D 开放世界，做「舞台」

| 方案 | 成本 | 判断 |
|---|---|---|
| 3D 开放世界，玩家自由走位 | 极高（每张地图都是美术活） | ❌ |
| **固定舞台 + 有限镜头** | 低 | ✅ |

一个 Encounter 本质上是**两个人站在一个地方说话**。这是一个舞台剧，不是开放世界。

固定 2~4 个镜头角度、一套环境光、一层体积雾，就足够撑起氛围。成本差一个数量级。

### 判断三：异步架构给了额外退路

如果某天真的要做重度演出（预渲染一段过场），异步形态允许离线渲染——玩家下次上线时看。实时性要求从"100ms"变成"几小时"。

**但这是退路，不是必走的路。** 先用客户端实时渲染，成本几乎为零。

---

## 2. 为什么这条路走得通：VRM 生态

### VRM 1.0 有标准表情系统

VRM 规范定义了预设表达式（preset expressions），包括：

- 情绪：`happy` / `angry` / `sad` / `relaxed` / `surprised`
- 口型：五元音 `aa` / `ih` / `ou` / `ee` / `oh`
- 眨眼：`blink` / `blinkLeft` / `blinkRight`
- 视线：`lookAt`

（[VRM 1.0 expression 文档](https://vrm.dev/en/vrm1/expression/)、
[BlendShape 设置](https://vrm.dev/en/univrm/blendshape/univrm_blendshape/)）

**这意味着两件事：**
1. 情感标签 → 表情参数，是**直接映射**，不需要训练模型
2. TTS 音频 → 口型，是标准的律动问题，已有成熟做法

### VRoid Studio 免费且可商用

[pixiv 的 VRoid Studio](https://vroid.com/en/studio/guidelines) 免费，能捏脸、能做服装，导出标准 VRM。

**这直接对应我们的收费点**——玩家捏脸 + 穿搭，本来就是这套生态的原生能力，不需要自己造工具。

### 已经有现成轮子

| 项目 | 做什么 |
|---|---|
| [ARPAHLS/avatar](https://github.com/ARPAHLS/avatar) | VRM + LLM + lipsync，桌面 overlay & 浏览器 |
| [kajidog/mcp-vrm-player](https://github.com/kajidog/mcp-vrm-player) | VRM 播放器（MCP 形态） |
| [calyi/GMTalker](https://huggingface.co/calyi/GMTalker) | AI 角色对话方向的项目 |

**不用从零造。** 缺的是有人把这些接到我们的 Encounter 流程上。

### 自动 rigging 在进步

传统 3D 角色最贵的一环是**骨骼绑定**。这块正在被自动化：
- [ACT: A Unified Framework for Rigging and Animating Characters with Arbitrary Topologies](https://dl.acm.org/doi/abs/10.1145/3811392)（ACM TOG）
- [Human–AI Collaborative Workflow for Three-Dimensional Animation Production](https://ieeexplore.ieee.org/document/11682836)（IEEE）

但对本项目而言，**这条暂时用不上**——VRM 模型从 VRoid 出来时自带绑定和表情，直接就能用。

---

## 3. 技术管线

```
┌─────────────────────────────────────────────────────────┐
│ 服务端                                                   │
│  LLM 叙事 → {text, emotion, intensity, pause_ms, gesture}│
│  TTS      → audio (mp3/opus)                            │
└─────────────────────────────────────────────────────────┘
                    ↓ WebSocket / 静态资源
┌─────────────────────────────────────────────────────────┐
│ 客户端 (three.js + three-vrm)                            │
│  1. 加载 VRM 模型（对位双方各自的形象）                    │
│  2. 场景：固定舞台 + 镜头机位                              │
│  3. 播放 TTS 音频                                         │
│  4. 同步驱动：                                            │
│     · expression: emotion → VRM preset weight            │
│     · 口型: 音频包络/音素 → aa/ih/ou/ee/oh                │
│     · 视线: lookAt → 看向对方                             │
│     · 小动作: gesture → 动画片段（可选）                   │
│  5. 字幕叠加 text                                         │
└─────────────────────────────────────────────────────────┘
```

### 关键：emotion 从哪来

不需要额外的模型。LLM 在生成叙事时**顺便输出情绪标签**即可——它本来就知道这句话是什么情绪。

给 LLM 的输出加结构化约束（JSON schema）：

```json
{
  "line": "雨点砸在脸上，你勉强睁开眼。",
  "emotion": "fear",
  "intensity": 0.7
}
```

**这就是整个 3D 方案在服务端的全部增量**：多 3 个字段。

### 「思考方式」怎么体现

用户想的是"建模有真实的表情和思考方式"。拆成三层：

| 想表达的 | 对应的技术手段 |
|---|---|
| 角色在**思考** | 视线偏移 + 头部微侧 + 停顿（`pause_ms`） |
| 角色有**情绪** | `emotion` → expression weight + 呼吸/微动幅度 |
| 角色在**反应**对方 | 对方说话时看向对方，说完后视线回到玩家 |

第一层是纯动画参数，不需要 AI。第二层是标签映射。第三层是状态机。

**都能做，但都是"演出细节"，不是"AI 能力"。** 别指望模型自己会演。

---

## 4. 分阶段落地

不要一次做到位。按"演出感"的性价比排序：

| 阶段 | 做什么 | 成本 | 能验证什么 |
|---|---|---|---|
| **P0** | 2D 立绘 + 表情差分（甚至只换图） | 极低 | **"角色在对你说"的演出感到底有没有价值** |
| **P1** | 单个 VRM 模型 + 表情 + 口型 | 低 | VRM 管线通不通 |
| **P2** | 双方 VRM 同框 + 镜头 | 中 | 对位的"在场感" |
| **P3** | 3D 舞台环境 + 光影氛围 | 中高 | 沉浸度是否值得这笔钱 |

**P0 是关键验证点。** 如果 2D 立绘 + 表情切换都没有明显提升体验，那 3D 也不会有——只是更贵。**先花一周做 P0，别直接上 3D。**

---

## 5. 成本评估（诚实版）

| 项 | 量级 | 说明 |
|---|---|---|
| **VRM 角色** | 低 | VRoid 免费；买现成模型几百~几千 |
| **客户端渲染** | 零 | WebGL 本地跑，服务端不承担 |
| **TTS** | 按字符计费 | MiniMax 等都有语音 API，量级可控 |
| **表情驱动** | 零 | 标签映射，不需要模型 |
| **★ 3D 场景美术** | **这才是真烧钱的地方** | 每个舞台都要美术 |
| **★ 动作动画** | **第二烧钱** | 手势/走动需要动画片段 |

**结论：角色便宜，场景贵，动画最贵。**

所以 §1 的判断二（做舞台不做开放世界）是成本控制的命门。一个 Encounter 只需要一个舞台，5 个副本轮换 = 5 个舞台，这个量级是可以承受的。

**绝对不要做的事**：给每个副本做开放式 3D 场景。那会让美术成本变成无底洞。

---

## 6. 风险

| 风险 | 严重度 | 缓解 |
|---|---|---|
| 3D 分散精力，M0 的"好不好玩"还没验完就去做 3D | **高** | **P0 之前不碰 3D**；先证明玩法 |
| VRM 模型的风格不统一，5 个副本 5 种画风 | 中 | 统一风格包，或只允许官方精选模型进主流程 |
| 玩家自定义 VRM 出现违规内容（造型） | 中高 | 只能从官方库选，不允许上传自定义模型 |
| 移动端性能 | 中 | three-vrm 在手机上跑得动，但要限面数 |
| TTS 成本随语音条数线性增长 | 中 | 缓存 + 只对关键台词配音 |

---

## 7. 一句话给想做这块的人

> 不要做 3D 引擎。**做一件事就够**：让 VRM 角色按 `emotion` 字段做表情，按 TTS 音频对口型，在一个固定的舞台光照下看向对方。
>
> 这一件事做通，整个方向就活了。其余都是美术量的堆叠。

---

## 参考

- [VRM 1.0 expression](https://vrm.dev/en/vrm1/expression/)
- [VRM BlendShape 设置](https://vrm.dev/en/univrm/blendshape/univrm_blendshape/)
- [VRoid Studio 指南与许可](https://vroid.com/en/studio/guidelines)
- [VRoid Studio 商用利用说明](https://vroid.pixiv.help/hc/ja/articles/4405813333657)
- [ARPAHLS/avatar](https://github.com/ARPAHLS/avatar)
- [kajidog/mcp-vrm-player](https://github.com/kajidog/mcp-vrm-player)
- [ACT: Rigging and Animating Characters with Arbitrary Topologies](https://dl.acm.org/doi/abs/10.1145/3811392)
