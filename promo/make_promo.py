#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《雨夜·十九点四十》推广短视频 · 代码逐帧渲染
=============================================

风格参照 opus 5.5 那批 code-rendered 视频：不用视频大模型，
每一帧都由代码画出来，排版和节奏精确到帧。

规格：1080x1920（9:16 竖屏）· 30fps · 约 42 秒 · 无声（留给抖音配乐）

用法：
    python3 make_promo.py                # 渲染
    python3 make_promo.py --preview 5    # 只导出第 5 秒的单帧 PNG 检查

依赖：Pillow、numpy、ffmpeg（本机均已有）

设计说明：
  · 配色克制到三色：近黑底 / 暖白字 / 旧铜色强调
  · 文字只做淡入 + 极轻微上浮，不做花哨特效
  · 雨丝贯穿全片，是唯一的持续动势
  · 关键数字（37 / 19:40）放大并换强调色，这是记忆点
"""

from __future__ import annotations

import argparse
import functools
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------- 规格

W, H, FPS = 1080, 1920, 30
DURATION = 42.0
TOTAL_FRAMES = int(DURATION * FPS)

BG      = (10, 12, 16)
FG      = (232, 224, 213)
ACCENT  = (200, 164, 92)
DIM     = (118, 128, 142)
RAIN    = (46, 66, 78)

F_SONG  = "/System/Library/Fonts/Supplemental/Songti.ttc"
F_HEI   = "/System/Library/Fonts/STHeiti Medium.ttc"
F_HEI_L = "/System/Library/Fonts/STHeiti Light.ttc"


@functools.lru_cache(maxsize=64)
def get_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size, index=0)


# ---------------------------------------------------------------- 文案与时间轴
#
# 每个 slide: t0/t1 显示区间，blocks 是若干文本块
# block = (文字, 字号, 字体, 颜色, 纵向位置比例, 是否强调)

SLIDES = [
    {"t0": 0.4, "t1": 4.4, "blocks": [
        ("1998 · 雨夜", 66, F_HEI_L, ACCENT, 0.36, False),
        ("他把一块表，拨快了 37 分钟。", 72, F_SONG, FG, 0.46, False),
    ]},
    {"t0": 4.7, "t1": 9.0, "blocks": [
        ("槐荫路 17 号", 104, F_SONG, FG, 0.38, False),
        ("守夜人每晚 19:40 上楼点灯", 56, F_HEI_L, DIM, 0.48, False),
        ("坐十七分钟，再拧灭", 56, F_HEI_L, DIM, 0.53, False),
    ]},
    {"t0": 9.3, "t1": 13.2, "blocks": [
        ("他说 ——", 58, F_HEI_L, DIM, 0.42, False),
        ("是照看线路", 92, F_SONG, FG, 0.50, False),
    ]},
    {"t0": 13.5, "t1": 17.2, "blocks": [
        ("五年后", 96, F_SONG, FG, 0.42, False),
        ("来了一个女人", 72, F_SONG, FG, 0.51, False),
    ]},
    {"t0": 17.5, "t1": 22.6, "blocks": [
        ("她没问：我姐姐去哪了", 60, F_HEI_L, DIM, 0.38, False),
        ("她问：", 60, F_HEI_L, DIM, 0.47, False),
        ("那天 19:40", 88, F_SONG, ACCENT, 0.55, True),
        ("楼上到底有没有人", 76, F_SONG, FG, 0.63, False),
    ]},
    {"t0": 22.9, "t1": 26.6, "blocks": [
        ("19:39　灯亮", 70, F_HEI_L, FG, 0.42, False),
        ("19:56　灯灭", 70, F_HEI_L, FG, 0.50, False),
        ("一分不差", 64, F_SONG, ACCENT, 0.60, False),
    ]},
    {"t0": 26.9, "t1": 31.2, "blocks": [
        ("可 19:40 那一刻", 62, F_HEI_L, DIM, 0.36, False),
        ("她不在这栋楼里", 82, F_SONG, FG, 0.45, False),
        ("她在站前广场", 62, F_HEI_L, FG, 0.55, False),
        ("等一个没来的人", 78, F_SONG, ACCENT, 0.63, False),
    ]},
    {"t0": 31.5, "t1": 35.4, "blocks": [
        ("37", 300, F_SONG, ACCENT, 0.34, True),
        ("分钟", 68, F_HEI_L, FG, 0.50, False),
        ("刚好抹掉了「她在等一个人」", 54, F_SONG, DIM, 0.60, False),
    ]},
    {"t0": 35.7, "t1": 40.0, "blocks": [
        ("这是一个游戏", 84, F_SONG, FG, 0.34, False),
        ("你们被投进同一个场景", 56, F_HEI_L, DIM, 0.44, False),
        ("你看到的", 62, F_HEI_L, FG, 0.54, False),
        ("和他看到的，不是同一件事", 62, F_HEI_L, ACCENT, 0.61, False),
    ]},
    {"t0": 39.8, "t1": 42.0, "blocks": [
        ("你不知道对面是谁", 60, F_HEI_L, FG, 0.36, False),
        ("也不知道，他是不是在骗你", 52, F_HEI_L, DIM, 0.44, False),
    ]},
]

TITLE_SLIDE = {"t0": 40.6, "t1": 42.0, "blocks": [
    ("《雨夜 · 十九点四十》", 78, F_SONG, FG, 0.575, False),
    ("匿名对位 · 无限流", 50, F_HEI_L, ACCENT, 0.635, False),
    ("M0 原型已可玩", 40, F_HEI_L, DIM, 0.685, False),
]}


# ---------------------------------------------------------------- 动画

def alpha_of(t: float, t0: float, t1: float,
             fade_in: float = 0.5, fade_out: float = 0.4) -> float:
    """出现的时间权重。淡入、保持、淡出。"""
    if t < t0 or t > t1:
        return 0.0
    if t < t0 + fade_in:
        return (t - t0) / fade_in
    if t > t1 - fade_out:
        return max(0.0, (t1 - t) / fade_out)
    return 1.0


# ---------------------------------------------------------------- 雨

_rng = np.random.default_rng(20261019)
RAIN_N = 170
rain_x = _rng.uniform(0, W, RAIN_N)
rain_y0 = _rng.uniform(0, H, RAIN_N)
rain_speed = _rng.uniform(1500, 3200, RAIN_N)
rain_len = _rng.uniform(26, 78, RAIN_N)
rain_dim = _rng.uniform(0.45, 1.0, RAIN_N)


def draw_rain(draw: ImageDraw.ImageDraw, t: float) -> None:
    for i in range(RAIN_N):
        x = float(rain_x[i])
        y = float((rain_y0[i] + t * rain_speed[i]) % (H + 140) - 70)
        d = rain_dim[i]
        col = (int(RAIN[0] * d), int(RAIN[1] * d), int(RAIN[2] * d))
        draw.line([(x, y), (x - 6, y + float(rain_len[i]))], fill=col, width=2)


# ---------------------------------------------------------------- 文字

Y_SHIFT = 0.045          # 整体下移，避开顶部头像区与底部文案区


def draw_blocks(layer: Image.Image, blocks, t: float,
                t0: float, t1: float) -> None:
    d = ImageDraw.Draw(layer)
    for (txt, size, fpath, color, yratio, emph) in blocks:
        a = alpha_of(t, t0, t1, 0.55 if not emph else 0.9, 0.35)
        if a <= 0.01:
            continue
        f = get_font(fpath, size)
        bbox = d.textbbox((0, 0), txt, font=f)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = (W - tw) / 2
        # 极轻微上浮
        rise = (1.0 - a) * 10
        y = H * (yratio + Y_SHIFT) - th / 2 + rise
        rgba = (*color, int(255 * a))
        d.text((x - bbox[0], y - bbox[1]), txt, font=f, fill=rgba)


# ---------------------------------------------------------------- 帧

def make_base() -> Image.Image:
    """预渲染背景：上深下更深的渐变，压住画面重心。"""
    ys = np.linspace(0, 1, H)[:, None]
    top = np.array([12, 15, 20], dtype=float)
    bot = np.array([5, 6, 9], dtype=float)
    grad = top * (1 - ys) + bot * ys
    arr = np.repeat(grad[:, None, :], W, axis=1).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


BASE = make_base()


def render_frame(idx: int) -> bytes:
    t = idx / FPS
    img = BASE.copy()
    draw = ImageDraw.Draw(img)
    draw_rain(draw, t)

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    for s in SLIDES:
        if s["t0"] <= t <= s["t1"]:
            draw_blocks(layer, s["blocks"], t, s["t0"], s["t1"])
    if TITLE_SLIDE["t0"] <= t <= TITLE_SLIDE["t1"]:
        draw_blocks(layer, TITLE_SLIDE["blocks"], t,
                    TITLE_SLIDE["t0"], TITLE_SLIDE["t1"])

    # 常驻角落标记
    d2 = ImageDraw.Draw(layer)
    d2.text((64, 96), "VEILBOUND", font=get_font(F_HEI_L, 30),
            fill=(*DIM, 140))

    out = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")

    # 底部进度线
    dfin = ImageDraw.Draw(out)
    p = t / DURATION
    bar_y = 156
    dfin.line([(64, bar_y), (64 + (W - 128) * p, bar_y)],
              fill=ACCENT, width=3)
    dfin.line([(64 + (W - 128) * p, bar_y), (W - 64, bar_y)],
              fill=(38, 42, 50), width=3)

    return out.tobytes()


# ---------------------------------------------------------------- 主流程

def render(out_path: str) -> int:
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{W}x{H}", "-r", str(FPS),
        "-i", "-",
        "-an",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        out_path,
    ]
    print(f"渲染 {TOTAL_FRAMES} 帧 · {W}x{H} · {FPS}fps · {DURATION}s")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert proc.stdin is not None
    try:
        for i in range(TOTAL_FRAMES):
            proc.stdin.write(render_frame(i))
            if i % (FPS * 4) == 0:
                print(f"  {i / FPS:5.1f}s / {DURATION:.0f}s")
        proc.stdin.close()
    except BrokenPipeError:
        print("ffmpeg 提前退出", file=sys.stderr)
        return 1
    rc = proc.wait()
    if rc != 0:
        print(f"ffmpeg 失败 rc={rc}", file=sys.stderr)
        return rc
    size = os.path.getsize(out_path) / 1024 / 1024
    print(f"完成：{out_path}  ({size:.1f} MB)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="veilbound-promo.mp4")
    ap.add_argument("--preview", type=float, default=None,
                    help="只导出该秒数的单帧 PNG")
    a = ap.parse_args()

    if a.preview is not None:
        idx = int(a.preview * FPS)
        raw = render_frame(idx)
        img = Image.frombytes("RGB", (W, H), raw)
        p = f"preview-{a.preview:g}s.png"
        img.save(p)
        print(f"已导出 {p}")
        return 0

    return render(a.out)


if __name__ == "__main__":
    sys.exit(main())
