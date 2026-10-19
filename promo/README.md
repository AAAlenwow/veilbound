# 推广视频

42 秒 · 1080×1920（9:16）· 30fps · H.264 · 无声

## 产物

| 文件 | 说明 |
|---|---|
| `veilbound-promo.mp4` | 成片，可直接传抖音 |
| `make_promo.py` | 生成脚本，改文案后重跑即可 |

## 为什么是「代码逐帧渲染」

不是调用视频大模型，而是**每一帧都由代码画出来**，排版和节奏精确到帧。
这是 opus 5.5 那批 viral 视频的共同做法（[参考实现](https://github.com/Kianzzz/xilo-opus-video)、
[示例合集](https://github.com/billyhetech/awesome-opus5-5-videos)）。

好处：改一个字不用重拍，改完重跑 2 分钟出新片。

## 怎么改

文案和时间轴都在 `make_promo.py` 顶部的 `SLIDES` 里：

```python
{"t0": 0.4, "t1": 4.4, "blocks": [
    ("1998 · 雨夜", 66, F_HEI_L, ACCENT, 0.36, False),
    #  文字        字号  字体    颜色    纵向位置  强调
]},
```

```bash
# 先出一张关键帧看排版，别直接渲染整片
python3 make_promo.py --preview 20

# 满意后渲染
python3 make_promo.py --out veilbound-promo.mp4
```

需要 Pillow + numpy + ffmpeg。本机用 bundled Python：

```bash
/Users/hechao/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3 make_promo.py
```

## 设计约束（踩过的坑）

- **抖音安全区**：底部约 25% 会被文案/按钮遮挡，顶部约 10% 有头像。
  文字全部压缩在 20%~72% 区间，进度条放顶部。
- **文字不重叠**：相邻 slide 的时间区间不能交叠，否则两组字会叠在一起（第 41 秒踩过）。
- **克制配色**：近黑底 / 暖白字 / 旧铜色强调，三色到底。多一个颜色就掉档次。
- **无声是刻意的**：抖音创作者通常自己配 BGM 和旁白。烧进去反而限制二创。

## 发布建议

- **封面**：用「37」那帧（第 33 秒），数字大字最好认
- **开头 3 秒**必须是钩子，别放 logo
- **文案**：`他把一块表，拨快了 37 分钟。` —— 具体、反常、有疑问
- **标签**：`#悬疑` `#无限流` `#AI游戏` `#独立游戏` `#剧本杀`
- 抖音号简介里挂仓库地址（仓库现在是私有的，要改成"私信获取"或先转公开）
