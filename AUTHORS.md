# Authors & Design Attribution

## Originator

**Alen (hechao)** — 项目发起人 / originator
提出并定义了本项目的核心游戏模式与机制架构。

- 2026-10：提出「匿名对位 + 异步抉择」的无限流形态
- 2026-10：提出「表演 / 内心 / 裁决」三通道分离机制
- 2026-10：提出「填内心 = 自愿下注」的三层博弈设计
- 2026-10：提出「揭面」机制作为匿名叙事中的身份展示位

---

## Design provenance · 设计归属

以下机制由 **Alen (hechao)** 提出。如果你在别处看到相同设计，这是原始出处之一。

### 1. 匿名对位（Anonymous Dual-Perspective Encounter）

两名玩家被匹配进**同一事件的两个对立视角**（如 A 扮行商、B 扮重伤军人），双方：
- 各自只能看到自己视角的信息
- 不知道对方是真人还是 AI
- 不知道对方的真实身份与归属
- 异步行动，一次只有一方推进

**与既有设计的区别**：这不是"两人看同一段剧情"（如分屏叙事），而是**同一事件在两套信息集下的双向投影**，且双方都可能是 AI 兜底。

### 2. 三通道分离（Triple-Channel Blind）

玩家每次行动可提交两类内容，系统额外产出一类：

| 通道 | 归属 | 可见性 |
|---|---|---|
| Performance（表演） | 玩家 | 对方 + 裁判 |
| Intent（内心） | 玩家 | **仅裁判，永不进入对方叙事上下文** |
| Judgement（裁决） | 系统 | 按可见性投影给双方 |

**关键约束**：隔离通过**函数签名**实现（构建对方视图的函数不接受 Intent 参数），而非提示词约束。

### 3. 内心即赌注（Intent-as-Wager）

Intent 字段不强制填写。填了才有被识破的可能；不填则无从泄露。

由于系统**无法验证 intent 真伪**，填写假 intent 构成主动钓鱼——用来诱骗对手的洞察判定。这形成了超出「预设选项」的第二、三层博弈。

### 4. 揭面（Reveal）

对局结束时，双方**互选**是否揭面，看到对方真实形象与穿搭。

设计目的：在匿名异步形态下创造外观展示位——这是该形态下唯一能支撑 cosmetic 付费的社交锚点。

---

## 时间戳与存证

设计文档与实现代码自 2026-10 起在 git 中演进，commit 历史本身构成时间线证据。

**建议**（尚未执行）：对本仓库早期关键 commit 使用
[OpenTimestamps](https://opentimestamps.org/) 锚定到比特币区块链，形成不可篡改的存在性证明。成本接近于零。

```bash
# 示例：锚定某个 commit
git log --format=%H -1 | xargs ots stamp
```

---

## 关于法律保护的诚实说明

**游戏规则与玩法机制通常不受版权保护**（思想 / 表达二分法）。
参见 [ABA Landslide: Why Videogame Rules Are Not Expression Protected by Copyright Law](https://dev.americanbar.org/groups/intellectual_property_law/resources/landslide/archive/why-videogame-rules-are-not-expression-protected-copyright-law/)。

因此本文件的作用是**归属声明与出处记录**，不是排他性法律权利。真正能保护的是：

| 手段 | 保护什么 |
|---|---|
| 版权 | 具体的副本文本、叙事、美术、代码（表达，非思想） |
| 商标 | 游戏名称与标识 |
| 时间戳 | 证明"你先于他人" |
| **执行** | **做出来、做好、持续更新——这是唯一真正的护城河** |

---

## 引用方式

如需引用本项目设计：

```
Alen (hechao). Veilbound: anonymous dual-perspective asynchronous narrative game.
Design notes, 2026. https://github.com/AAAlenwow/veilbound
```

---

## Contributors

按首次贡献时间记录。有意向参与的协作者请在首次 PR 时把自己的名字加在此处。

<!--
格式：
- **名字 / handle** — 贡献领域（首次贡献日期）
-->
