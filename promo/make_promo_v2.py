#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
《雨夜·十九点四十》推广短片 v2
================================

v1 的错误：纯文字排版、没有人物、把真相全剧透、没有音乐。
v2 的取向：**展示游戏长什么样，而不是讲剧情讲什么。**

核心视觉是三样东西，都是这个游戏独有的：
  1. 分屏对比 —— 你们看到的是同一个人，但不是同一件事
  2. 判定明账 —— 掷骰摊开给你看，判定不由 AI 决定
  3. 表演 / 内心 —— 你说什么，和你想什么，是两回事

技术：Pillow 逐帧绘制 + numpy 合成音轨 + ffmpeg 编码。不用视频大模型。

用法：
    python3 make_promo.py                   # 出片
    python3 make_promo.py --preview 12.5    # 单帧检查排版
    python3 make_promo.py --audio-only      # 只生成音轨试听
"""

from __future__ import annotations

import argparse
import functools
import math
import os
import subprocess
import sys
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ------------------------------------------------------------------ 规格

W, H, FPS = 1080, 1920, 30
DURATION = 35.0
TOTAL = int(DURATION * FPS)
SR = 44100

BG_TOP = (9, 11, 15)
BG_BOT = (4, 5, 8)
FG = (234, 228, 218)
ACCENT = (204, 168, 96)
DIM = (122, 132, 146)
INK = (3, 4, 6)                      # 剪影用，比背景更黑
LAMP = (214, 176, 108)               # 路灯暖光
COLD = (86, 118, 140)                # 雨夜冷光

F_SONG = "/System/Library/Fonts/Supplemental/Songti.ttc"
F_HEI = "/System/Library/Fonts/STHeiti Medium.ttc"
F_HEI_L = "/System/Library/Fonts/STHeiti Light.ttc"


@functools.lru_cache(maxsize=96)
def font(path: str, size: int):
    return ImageFont.truetype(path, size, index=0)


def lerp(a, b, k):
    return tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))


def clamp01(x):
    return max(0.0, min(1.0, x))


def ease(x):
    return x * x * (3 - 2 * x)


def appear(t, t0, t1, fin=0.55, fout=0.45):
    if t < t0 or t > t1:
        return 0.0
    if t < t0 + fin:
        return clamp01((t - t0) / fin)
    if t > t1 - fout:
        return clamp01((t1 - t) / fout)
    return 1.0


# ------------------------------------------------------------------ 背景

def build_background() -> Image.Image:
    ys = np.linspace(0, 1, H)[:, None]
    g = (np.array(BG_TOP, float) * (1 - ys)
         + np.array(BG_BOT, float) * ys)
    arr = np.repeat(g[:, None, :], W, axis=1).astype(np.uint8)
    img = Image.fromarray(arr, "RGB")

    # 远景：几栋楼的暗轮廓，压住地平线
    d = ImageDraw.Draw(img)
    rng = np.random.default_rng(11)
    x = -40
    while x < W + 40:
        bw = int(rng.uniform(120, 260))
        bh = int(rng.uniform(180, 420))
        top = int(H * 0.62) - bh
        d.rectangle([x, top, x + bw, int(H * 0.72)], fill=(13, 16, 21))
        # 零星窗格
        for _ in range(int(rng.integers(1, 4))):
            wx = x + int(rng.uniform(20, max(21, bw - 30)))
            wy = top + int(rng.uniform(30, max(31, bh - 40)))
            if rng.random() < 0.5:
                d.rectangle([wx, wy, wx + 10, wy + 14], fill=(40, 34, 24))
        x += bw + int(rng.uniform(10, 60))

    # 地面湿润反光带
    ground_y = int(H * 0.72)
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.rectangle([0, ground_y, W, H], fill=(18, 22, 28))
    glow = glow.filter(ImageFilter.GaussianBlur(24))
    img = Image.blend(img, Image.blend(img, glow, 0.5), 0.55)
    return img


BASE_BG = build_background()


def radial_glow(radius: int, color, power: float = 2.2) -> Image.Image:
    """预渲染一张径向光晕（RGBA）。"""
    d = radius * 2
    yy, xx = np.mgrid[0:d, 0:d]
    dist = np.sqrt((xx - radius) ** 2 + (yy - radius) ** 2) / radius
    a = np.clip(1 - dist, 0, 1) ** power
    arr = np.zeros((d, d, 4), dtype=np.uint8)
    for i in range(3):
        arr[..., i] = color[i]
    arr[..., 3] = (a * 255).astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


GLOW_WARM = radial_glow(360, LAMP, 2.0)
GLOW_COLD = radial_glow(300, COLD, 2.4)


# ------------------------------------------------------------------ 雨

_rng = np.random.default_rng(2026)
RAIN_N = 200
RAIN_X = _rng.uniform(0, W, RAIN_N)
RAIN_Y = _rng.uniform(0, H, RAIN_N)
RAIN_V = _rng.uniform(1400, 3100, RAIN_N)
RAIN_L = _rng.uniform(22, 84, RAIN_N)
RAIN_A = _rng.uniform(0.25, 1.0, RAIN_N)


def draw_rain(d: ImageDraw.ImageDraw, t: float, density: float = 1.0) -> None:
    n = int(RAIN_N * clamp01(density))
    for i in range(n):
        x = float(RAIN_X[i])
        y = float((RAIN_Y[i] + t * RAIN_V[i]) % (H + 160) - 80)
        a = RAIN_A[i]
        col = (int(52 * a), int(74 * a), int(88 * a))
        d.line([(x, y), (x - 7, y + float(RAIN_L[i]))], fill=col, width=2)


# ------------------------------------------------------------------ 人物剪影

def person_standing(d: ImageDraw.ImageDraw, cx: float, base_y: float,
                    h: float, color=INK, lean: float = 0.0) -> None:
    """站立剪影。按真实人体比例：头 ≈ 身高/7.5，肩宽 ≈ 身高/4。"""
    hr = h * 0.066                      # 头半径
    top = base_y - h
    hb = top + 2 * hr                   # 下巴
    sh_y = hb + h * 0.028               # 肩线
    sw = h * 0.128                      # 肩半宽
    waist_y = sh_y + h * 0.175
    ww = h * 0.082
    hip_y = waist_y + h * 0.055
    hw = h * 0.086
    knee_y = hip_y + h * 0.26
    lx = lean * h

    d.ellipse([cx - hr + lx * 0.5, top, cx + hr + lx * 0.5, hb], fill=color)
    d.polygon([(cx - hr * 0.40 + lx * 0.5, hb - hr * 0.25),
               (cx + hr * 0.40 + lx * 0.5, hb - hr * 0.25),
               (cx + hr * 0.58 + lx * 0.7, sh_y),
               (cx - hr * 0.58 + lx * 0.7, sh_y)], fill=color)
    d.polygon([(cx - sw + lx, sh_y), (cx + sw + lx, sh_y),
               (cx + hw, hip_y), (cx - hw, hip_y)], fill=color)
    lw = h * 0.047
    for sgn in (-1, 1):
        d.polygon([(cx + sgn * hw * 0.85, hip_y), (cx + sgn * h * 0.012, hip_y),
                   (cx + sgn * lw * 0.55, knee_y),
                   (cx + sgn * lw * 1.25, base_y),
                   (cx + sgn * lw * 0.30, base_y),
                   (cx + sgn * lw * 0.08, knee_y)], fill=color)
    for sgn in (-1, 1):
        d.polygon([(cx + sgn * sw + lx, sh_y + h * 0.004),
                   (cx + sgn * (sw + h * 0.020) + lx, sh_y + h * 0.014),
                   (cx + sgn * (sw + h * 0.024), hip_y - h * 0.035),
                   (cx + sgn * (sw + h * 0.004), hip_y - h * 0.020)], fill=color)


def person_fallen(d: ImageDraw.ImageDraw, cx: float, base_y: float,
                  L: float, color=INK) -> None:
    """
    倒地剪影（侧卧，头在左）。
    用侧视比例画，不做旋转——旋转在贴图时容易错位。
    """
    hr = L * 0.062
    hx = cx - L * 0.44
    # 头
    d.ellipse([hx - hr, base_y - 2.0 * hr, hx + hr, base_y + 0.05 * hr], fill=color)
    # 颈
    sh_x = hx + hr * 1.35
    d.polygon([(hx + hr * 0.5, base_y - 1.85 * hr),
               (sh_x, base_y - 2.05 * hr),
               (sh_x, base_y - 0.45 * hr),
               (hx + hr * 0.5, base_y - 0.30 * hr)], fill=color)
    # 躯干：肩到髋，略微上抬（侧卧时有厚度）
    hip_x = cx + L * 0.06
    d.polygon([(sh_x, base_y - 2.15 * hr), (hip_x, base_y - 1.70 * hr),
               (hip_x, base_y - 0.35 * hr), (sh_x, base_y - 0.75 * hr)], fill=color)
    # 大腿（往右上）
    knee_x = cx + L * 0.285
    d.polygon([(hip_x, base_y - 1.70 * hr), (knee_x, base_y - 1.95 * hr),
               (knee_x, base_y - 0.80 * hr), (hip_x, base_y - 0.40 * hr)], fill=color)
    # 小腿（折下去）
    foot_x = cx + L * 0.455
    d.polygon([(knee_x, base_y - 1.95 * hr), (foot_x, base_y - 0.55 * hr),
               (foot_x - L * 0.03, base_y - 0.25 * hr),
               (knee_x, base_y - 1.15 * hr)], fill=color)
    # 一条手臂垂在地面
    d.polygon([(sh_x + hr * 0.2, base_y - 1.75 * hr),
               (sh_x + L * 0.16, base_y - 1.15 * hr),
               (sh_x + L * 0.17, base_y - 0.55 * hr),
               (sh_x + L * 0.10, base_y - 0.60 * hr),
               (sh_x - hr * 0.1, base_y - 1.35 * hr)], fill=color)


# ------------------------------------------------------------------ 文字工具

def text(d: ImageDraw.ImageDraw, s: str, size: int, path: str, color,
         y: float, alpha: float = 1.0, align: str = "center",
         x: float | None = None, spacing: int = 0) -> tuple[float, float]:
    f = font(path, size)
    bbox = d.textbbox((0, 0), s, font=f)
    tw = bbox[2] - bbox[0]
    if x is None:
        x = (W - tw) / 2 if align == "center" else 64
    d.text((x - bbox[0], y - bbox[1]), s, font=f, fill=(*color, int(255 * alpha)))
    return x, y


def text_blocks(layer, blocks, t, t0, t1):
    d = ImageDraw.Draw(layer)
    for (s, size, path, color, yratio, fin) in blocks:
        a = appear(t, t0, t1, fin, 0.4)
        if a <= 0.02:
            continue
        rise = (1 - ease(a)) * 14
        text(d, s, size, path, color, H * yratio + rise, a)


# ------------------------------------------------------------------ 场景

def scene_open(img: Image.Image, t: float, local: float) -> None:
    """雨夜，一个人倒在山道边。"""
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    ground = int(H * 0.70)

    # 远处路灯
    lamp_x, lamp_y = W * 0.78, H * 0.36
    lay.alpha_composite(GLOW_WARM, (int(lamp_x - 360), int(lamp_y - 360)))
    d.line([(lamp_x, lamp_y), (lamp_x, ground)], fill=(20, 22, 27), width=7)
    d.ellipse([lamp_x - 16, lamp_y - 16, lamp_x + 16, lamp_y + 16],
              fill=(236, 206, 150, 235))

    # 冷光从左上压下来
    lay.alpha_composite(GLOW_COLD, (int(W * 0.10 - 300), int(H * 0.18 - 300)))

    # 地面
    d.rectangle([0, ground, W, H], fill=(11, 13, 17, 235))
    # 地上的积水反光
    d.ellipse([W * 0.20, ground + 26, W * 0.86, ground + 190],
              fill=(24, 30, 38, 120))

    # 倒下的人（缓慢浮现）
    fade = ease(clamp01(local / 1.6))
    person_fallen(d, W * 0.46, ground + 78, W * 0.60,
                  (4, 5, 8, int(240 * fade)))

    # 血迹（暗红，克制）
    if fade > 0.6:
        d.ellipse([W * 0.24, ground + 92, W * 0.44, ground + 132],
                  fill=(58, 20, 22, int(90 * fade)))

    img.paste(Image.alpha_composite(img.convert("RGBA"), lay).convert("RGB"), (0, 0))

    tl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    text_blocks(tl, [
        ("雨夜。他倒在山道边。", 68, F_SONG, FG, 0.17, 0.9),
    ], t, 0.9, 3.6)
    img.paste(Image.alpha_composite(img.convert("RGBA"), tl).convert("RGB"), (0, 0))


def scene_split(img: Image.Image, t: float, local: float) -> None:
    """
    分屏：左边「你看到的」，右边「他看到的」。
    这是整个游戏最独特的一格画面 —— 同一个人，两个世界。
    做法：先铺光，再把剪影压在光前面，剪影自然出轮廓。
    """
    ground = int(H * 0.62)
    half = W // 2

    # ---------------- 左：冷光里的人 ----------------
    left = Image.new("RGB", (half, H), (6, 8, 11))
    lm = Image.new("RGBA", (half, H), (0, 0, 0, 0))
    lm.alpha_composite(GLOW_COLD, (-200, int(H * 0.30)))
    lm.alpha_composite(GLOW_COLD, (int(half * 0.10), int(H * 0.44)))
    left = Image.alpha_composite(left.convert("RGBA"), lm).convert("RGB")
    ld = ImageDraw.Draw(left)
    ld.rectangle([0, ground, half, H], fill=(10, 13, 17))
    ld.ellipse([half * 0.10, ground + 14, half * 0.95, ground + 120],
               fill=(19, 24, 31))
    person_fallen(ld, half * 0.50, ground + 52, half * 0.80, (2, 3, 5))
    # 地面血迹
    ld.ellipse([half * 0.30, ground + 60, half * 0.50, ground + 84],
               fill=(52, 18, 20))

    # ---------------- 右：暖光里逼近的人 ----------------
    right = Image.new("RGB", (half, H), (6, 8, 11))
    rm = Image.new("RGBA", (half, H), (0, 0, 0, 0))
    rm.alpha_composite(GLOW_WARM, (int(half * 0.02), int(H * 0.26)))
    right = Image.alpha_composite(right.convert("RGBA"), rm).convert("RGB")
    rd = ImageDraw.Draw(right)
    rd.rectangle([0, ground, half, H], fill=(10, 13, 17))
    rd.ellipse([half * 0.14, ground + 14, half * 0.90, ground + 120],
               fill=(19, 24, 31))
    person_standing(rd, half * 0.50, ground + 66, half * 0.90,
                    (2, 3, 5), lean=0.025)

    canvas = img.copy()
    canvas.paste(left, (0, 0))
    canvas.paste(right, (half, 0))

    # 中缝
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    grow = ease(clamp01(local / 0.8))
    d.line([(half, int(H * 0.155)), (half, int(H * (0.155 + 0.585 * grow)))],
           fill=(*ACCENT, 190), width=2)

    img.paste(canvas, (0, 0))
    img.paste(Image.alpha_composite(img.convert("RGBA"), lay).convert("RGB"), (0, 0))

    # 文字
    tl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d2 = ImageDraw.Draw(tl)
    a1 = appear(t, 3.9, 8.4, 0.6, 0.5)
    if a1 > 0:
        text(d2, "你看到的", 38, F_HEI_L, DIM, H * 0.128, a1, align="left", x=64)
        text(d2, "他倒在地上。", 52, F_SONG, FG, H * 0.168, a1, align="left", x=64)
        text(d2, "他看到的", 38, F_HEI_L, DIM, H * 0.128, a1,
             align="left", x=half + 44)
        text(d2, "一个提刀的人逼近。", 52, F_SONG, ACCENT, H * 0.168, a1,
             align="left", x=half + 44)

    a2 = appear(t, 5.3, 8.4, 0.7, 0.5)
    if a2 > 0:
        text(d2, "同一个人。", 58, F_SONG, FG, H * 0.845, a2)
        text(d2, "不是同一件事。", 42, F_HEI_L, DIM, H * 0.895, a2)
    img.paste(Image.alpha_composite(img.convert("RGBA"), tl).convert("RGB"), (0, 0))


def scene_choice(img: Image.Image, t: float, local: float) -> None:
    """游戏 UI：你要怎么做。"""
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)

    text(d, "第 3 回合", 36, F_HEI_L, DIM, H * 0.115, appear(t, 8.3, 13.8, .5, .4))
    text(d, "他抬头看你。", 64, F_SONG, FG, H * 0.155, appear(t, 8.5, 13.8, .6, .4))

    opts = [
        ("1", "把他扶上马", "help"),
        ("2", "先搜他身上", "attack"),
        ("3", "不管他，走", "leave"),
    ]
    base = H * 0.30
    for i, (k, label, tag) in enumerate(opts):
        a = appear(t, 9.0 + i * 0.55, 13.8, 0.45, 0.35)
        if a <= 0.02:
            continue
        y = base + i * 150
        box = [70, y, W - 70, y + 116]
        d.rounded_rectangle(box, radius=10,
                            fill=(17, 20, 26, int(210 * a)),
                            outline=(*ACCENT, int(120 * a)), width=2)
        d.text((104, y + 34), k, font=font(F_HEI, 40), fill=(*ACCENT, int(240 * a)))
        d.text((168, y + 30), label, font=font(F_SONG, 52), fill=(*FG, int(240 * a)))

    a = appear(t, 11.0, 13.8, 0.5, 0.4)
    if a > 0:
        text(d, "做什么，由你决定。", 44, F_HEI_L, DIM, H * 0.755, a)
        text(d, "但判定，不由 AI 决定。", 44, F_HEI_L, ACCENT, H * 0.806, a)

    img.paste(Image.alpha_composite(img.convert("RGBA"), lay).convert("RGB"), (0, 0))


def scene_judge(img: Image.Image, t: float, local: float) -> None:
    """判定明账：掷骰、DC、加值，全摊开。"""
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)

    a = appear(t, 13.8, 19.2, 0.5, 0.45)
    if a > 0:
        text(d, "判定明账", 40, F_HEI_L, DIM, H * 0.16, a)

    # 逐行弹出：掷骰 → DC → 加值 → 结果
    rows = [("掷骰", "17", FG, 0.26, 14.2),
            ("DC", "15", FG, 0.335, 14.6),
            ("加值", "+1", FG, 0.410, 15.0)]
    for label, val, col, yr, t0 in rows:
        aa = appear(t, t0, 19.2, 0.4, 0.4)
        if aa <= 0.02:
            continue
        text(d, label, 44, F_HEI_L, DIM, H * yr, aa, align="left", x=140)
        text(d, val, 96, F_SONG, col, H * yr - 26, aa, align="left", x=380)

    # 结果
    ar = appear(t, 15.6, 19.2, 0.7, 0.45)
    if ar > 0:
        d.line([(140, H * 0.49), (W - 140, H * 0.49)],
               fill=(*DIM, int(110 * ar)), width=2)
        text(d, "成功", 128, F_SONG, ACCENT, H * 0.515, ar)

    a2 = appear(t, 16.8, 19.2, 0.6, 0.45)
    if a2 > 0:
        text(d, "骰子是算出来的，可复现、可回放。", 40, F_HEI_L, DIM, H * 0.71, a2)
        text(d, "你可以不接受运气，但能接受明账。", 40, F_HEI_L, FG, H * 0.757, a2)

    img.paste(Image.alpha_composite(img.convert("RGBA"), lay).convert("RGB"), (0, 0))


def scene_intent(img: Image.Image, t: float, local: float) -> None:
    """表演 / 内心：说出来的和想的是两回事。"""
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)

    a0 = appear(t, 19.2, 24.6, 0.5, 0.45)
    if a0 > 0:
        text(d, "你说的", 40, F_HEI_L, DIM, H * 0.150, a0)
        text(d, "「我可以带你出山。」", 60, F_SONG, FG, H * 0.192, a0)

    a1 = appear(t, 20.6, 24.6, 0.55, 0.45)
    if a1 > 0:
        d.line([(120, H * 0.285), (W - 120, H * 0.285)],
               fill=(*DIM, int(80 * a1)), width=1)
        text(d, "你想的", 40, F_HEI_L, DIM, H * 0.315, a1)
        text(d, "「等他伤好，就抢他。」", 60, F_SONG, ACCENT, H * 0.357, a1)

    a2 = appear(t, 22.2, 24.6, 0.6, 0.45)
    if a2 > 0:
        text(d, "「心里那栏」只有系统能看到。", 42, F_HEI_L, DIM, H * 0.655, a2)
        text(d, "除非他看穿你。", 58, F_SONG, FG, H * 0.715, a2)
        text(d, "——所以填真心话，是一次下注。", 38, F_HEI_L, DIM, H * 0.772, a2)

    img.paste(Image.alpha_composite(img.convert("RGBA"), lay).convert("RGB"), (0, 0))


def scene_reveal(img: Image.Image, t: float, local: float) -> None:
    """揭面：两个人正面相对。"""
    ground = int(H * 0.74)
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    d.rectangle([0, ground, W, H], fill=(10, 12, 16, 230))
    d.ellipse([W * 0.16, ground + 16, W * 0.84, ground + 140],
              fill=(22, 27, 34, 150))

    walk = ease(clamp01(local / 2.2))
    left_x = W * (0.28 - 0.03 * walk)
    right_x = W * (0.72 + 0.03 * walk)
    person_standing(d, left_x, ground + 80, H * 0.30, (4, 5, 8, 240))
    person_standing(d, right_x, ground + 80, H * 0.29, (4, 5, 8, 240), lean=-0.04)

    img.paste(Image.alpha_composite(img.convert("RGBA"), lay).convert("RGB"), (0, 0))

    tl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    text_blocks(tl, [
        ("一局结束。", 60, F_SONG, FG, 0.135, 0.6),
        ("你们可以互相看一眼。", 46, F_HEI_L, DIM, 0.196, 0.7),
        ("这是这游戏里，唯一能看见对方的时候。", 36, F_HEI_L, ACCENT, 0.875, 0.8),
    ], t, 24.6, 29.8)
    img.paste(Image.alpha_composite(img.convert("RGBA"), tl).convert("RGB"), (0, 0))


def scene_title(img: Image.Image, t: float, local: float) -> None:
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)

    a = appear(t, 29.9, 35.0, 0.9, 0.01)
    if a > 0:
        text(d, "你分不清", 52, F_SONG, DIM, H * 0.335, a * 0.9)
        text(d, "谁在骗你", 104, F_SONG, FG, H * 0.395, a)
    b = appear(t, 31.2, 35.0, 0.9, 0.01)
    if b > 0:
        text(d, "《雨夜 · 十九点四十》", 62, F_SONG, ACCENT, H * 0.585, b)
        text(d, "匿名对位 · 无限流", 40, F_HEI_L, DIM, H * 0.640, b)
        text(d, "M0 原型已可玩", 34, F_HEI_L, (88, 96, 108), H * 0.685, b)

    img.paste(Image.alpha_composite(img.convert("RGBA"), lay).convert("RGB"), (0, 0))


SCENES = [
    (0.0, 3.6, scene_open),
    (3.6, 8.4, scene_split),
    (8.4, 14.0, scene_choice),
    (14.0, 19.2, scene_judge),
    (19.2, 24.6, scene_intent),
    (24.6, 29.9, scene_reveal),
    (29.9, DURATION, scene_title),
]


# ------------------------------------------------------------------ 音轨

def synth_audio(path: str) -> None:
    """
    雨声 + 低频 drone + 心跳。全部用 numpy 合成，不用任何音源文件。
    目标不是"好听的音乐"，是"让人心里发紧的氛围"。
    """
    n = int(SR * DURATION)
    t = np.arange(n) / SR
    rng = np.random.default_rng(7)

    # --- 雨：带通白噪声 ---
    noise = rng.standard_normal(n).astype(np.float32)
    X = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(n, 1 / SR)
    band = np.exp(-((freqs - 1800) ** 2) / (2 * 1600 ** 2))
    band[freqs < 180] *= 0.15
    rain = np.fft.irfft(X * band, n).astype(np.float32)
    rain /= (np.abs(rain).max() + 1e-9)

    # --- drone：低频持续音，制造压迫 ---
    drone = np.zeros(n, dtype=np.float32)
    for f, a in ((55.0, 0.55), (82.5, 0.30), (110.0, 0.16), (164.8, 0.07)):
        drone += a * np.sin(2 * np.pi * f * t + rng.uniform(0, 6))
    drone /= 1.4
    # 极缓慢的呼吸式起伏
    drone *= (0.72 + 0.28 * np.sin(2 * np.pi * 0.055 * t)) ** 2

    # --- 心跳：低频短脉冲 ---
    beat = np.zeros(n, dtype=np.float32)
    for start in np.arange(0.8, DURATION, 1.06):
        i = int(start * SR)
        L = int(0.20 * SR)
        if i + L >= n:
            break
        env = np.exp(-np.linspace(0, 9, L)).astype(np.float32)
        amp = 0.55 + 0.45 * min(1.0, start / DURATION)     # 越到后面越急
        beat[i:i + L] += (np.sin(2 * np.pi * 47 * np.linspace(0, 0.20, L))
                          * env * amp).astype(np.float32)

    # --- 关键转折点加一记低频撞击（分屏出现 / 判定结果）---
    hit = np.zeros(n, dtype=np.float32)
    for at in (3.6, 5.4, 15.6, 31.2):
        i = int(at * SR)
        L = int(0.9 * SR)
        if i + L >= n:
            continue
        tt = np.linspace(0, 0.9, L)
        hit[i:i + L] += (np.sin(2 * np.pi * 41 * tt)
                         * np.exp(-tt * 4.5) * 0.85).astype(np.float32)

    mix = rain * 0.30 + drone * 0.30 + beat * 0.42 + hit * 0.50

    # 整体淡入淡出
    fade = int(1.2 * SR)
    mix[:fade] *= np.linspace(0, 1, fade)
    mix[-int(2.0 * SR):] *= np.linspace(1, 0, int(2.0 * SR))

    peak = np.abs(mix).max()
    if peak > 0:
        mix = mix / peak * 0.88
    pcm = (mix * 32767).astype(np.int16)

    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


# ------------------------------------------------------------------ 渲染

def render_frame(idx: int) -> bytes:
    t = idx / FPS
    img = BASE_BG.copy()
    d = ImageDraw.Draw(img)
    draw_rain(d, t)

    for t0, t1, fn in SCENES:
        if t0 <= t < t1:
            fn(img, t, t - t0)
            break

    # 角落标记 + 顶部进度
    dfin = ImageDraw.Draw(img)
    dfin.text((56, 84), "VEILBOUND", font=font(F_HEI_L, 26), fill=(72, 80, 92))
    p = clamp01(t / DURATION)
    dfin.line([(56, 132), (56 + (W - 112) * p, 132)], fill=ACCENT, width=2)
    dfin.line([(56 + (W - 112) * p, 132), (W - 56, 132)], fill=(30, 34, 40), width=2)

    return img.tobytes()


def render(out_path: str, audio_path: str) -> int:
    synth_audio(audio_path)

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
        "-r", str(FPS), "-i", "-",
        "-i", audio_path,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k",
        "-shortest", "-movflags", "+faststart",
        out_path,
    ]
    print(f"渲染 {TOTAL} 帧 · {W}x{H} · {FPS}fps · {DURATION}s（含合成音轨）")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert proc.stdin is not None
    try:
        for i in range(TOTAL):
            proc.stdin.write(render_frame(i))
            if i % (FPS * 5) == 0:
                print(f"  {i / FPS:5.1f}s / {DURATION:.0f}s")
        proc.stdin.close()
    except BrokenPipeError:
        print("ffmpeg 提前退出", file=sys.stderr)
        return 1
    rc = proc.wait()
    if rc != 0:
        return rc
    print(f"完成：{out_path}  ({os.path.getsize(out_path)/1048576:.1f} MB)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="veilbound-promo.mp4")
    ap.add_argument("--preview", type=float)
    ap.add_argument("--audio-only", action="store_true")
    a = ap.parse_args()

    if a.audio_only:
        synth_audio("promo-audio.wav")
        print("已生成 promo-audio.wav")
        return 0

    if a.preview is not None:
        raw = render_frame(int(a.preview * FPS))
        Image.frombytes("RGB", (W, H), raw).save(f"pv-{a.preview:g}.png")
        print(f"已导出 pv-{a.preview:g}.png")
        return 0

    return render(a.out, "promo-audio.wav")


if __name__ == "__main__":
    sys.exit(main())
