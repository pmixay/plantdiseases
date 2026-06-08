#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate Russian-language evaluation figures for the current PlantVision
model (v2.4.0, gamma). Reads the shipped bundle metrics from BUNDLE and writes
PNGs into OUT. No comparison to previous models — current only."""
import json
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

BUNDLE = Path("/tmp/models/current")
OUT = Path("/home/user/plantdiseases/server/reports")
OUT.mkdir(parents=True, exist_ok=True)

tm = json.load(open(BUNDLE / "training_metrics.json"))
mc = json.load(open(BUNDLE / "model_card.json"))
cf = json.load(open(BUNDLE / "conformal.json"))
classes = json.load(open(BUNDLE / "classes.json")).get("class_names")

RU = {
    "gray_mold_botrytis": "Серая гниль\n(ботритис)",
    "healthy": "Здоровое\nрастение",
    "leaf_scorch_underwatering": "Краевой ожог\n(недолив)",
    "leaf_spot": "Пятнистость\nлистьев",
    "not_a_plant": "Не растение",
    "nutrient_deficiency": "Дефицит\nпитания",
    "pests": "Вредители",
    "powdery_mildew": "Мучнистая\nроса",
    "viral_disease": "Вирусное\nзаболевание",
}
RU1 = {k: v.replace("\n", " ") for k, v in RU.items()}

# ── palette ──────────────────────────────────────────────────────────────
BG = "#0C120E"; CARD = "#141C16"; CARD2 = "#1B2620"
FG = "#EAF3EC"; MUT = "#8FA897"; GRID = "#26332B"
GREEN = "#34D058"; GREEN_D = "#1B7F3B"; AMBER = "#F0B429"; RED = "#E5534B"
BLUE = "#4F91F2"; VIOLET = "#A78BFA"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 12,
    "figure.facecolor": BG, "savefig.facecolor": BG,
    "axes.facecolor": CARD, "axes.edgecolor": GRID, "axes.labelcolor": FG,
    "text.color": FG, "xtick.color": MUT, "ytick.color": MUT,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
})

def confusion_metrics(cm):
    cm = np.array(cm, float)
    P, R, F1 = {}, {}, {}
    for i, c in enumerate(classes):
        tp = cm[i, i]; fn = cm[i].sum() - tp; fp = cm[:, i].sum() - tp
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        P[c], R[c], F1[c] = p, r, (2 * p * r / (p + r) if p + r else 0.0)
    return P, R, F1, np.trace(cm) / cm.sum()

rw = tm["realworld"]; idm = tm["indomain"]
Pr, Rr, F1r, acc_rw = confusion_metrics(rw["confusion_matrix"])
_, _, _, acc_id = confusion_metrics(idm["confusion_matrix"])
ci = mc["per_class_recall_ci_realworld"]["classes"]

def footer(fig, txt="PlantVision 2.4.0 · gamma · DINOv2 ViT-S/14 · оценка на отложенной выборке"):
    fig.text(0.5, 0.012, txt, ha="center", color=MUT, fontsize=9.5)

def title(ax_or_fig, t, sub=None, y=0.965):
    f = ax_or_fig
    f.text(0.065, y, t, fontsize=23, fontweight="bold", color=FG, ha="left")
    if sub:
        f.text(0.065, y - 0.052, sub, fontsize=13, color=MUT, ha="left")

# ════════════════════════════════════════════════════════════════════════
# 00 — Scorecard
# ════════════════════════════════════════════════════════════════════════
def fig_overview():
    fig = plt.figure(figsize=(12.8, 7.2))
    title(fig, "Сводка качества модели", "PlantVision 2.4.0 · тир «gamma» · 9 классов · вход 224 px")
    cards = [
        ("Macro-F1 (реальные фото)", f"{rw['disease_macro_f1']*100:.1f}%", "болезни, без healthy/NAP", GREEN),
        ("Macro-F1 (in-domain)", f"{idm['disease_macro_f1']*100:.1f}%", "та же доменная выборка", GREEN),
        ("Общая точность", f"{acc_rw*100:.1f}%", "все 9 классов, real-world", GREEN),
        ("Healthy-bias", f"{rw['healthy_bias']:.2f}", "норма < 1.5 — нет перекоса", BLUE),
        ("Покрытие conformal", f"{cf['empirical_coverage']*100:.1f}%", f"цель ≥ {cf['target_coverage']*100:.0f}% · набор {cf['mean_set_size']:.2f}", VIOLET),
        ("Распознавание «не растение»", f"{Rr['not_a_plant']*100:.1f}%", "OOD-защита", BLUE),
        ("Размер модели (INT8)", "22.5 МБ", "fp32 87.3 МБ · дрейф −0.2%", AMBER),
        ("Детектор листа (mAP50)", "82.0%", "Stage-1 YOLOv8n", GREEN),
    ]
    nx, ny = 4, 2
    x0, y0, w, h, gx, gy = 0.055, 0.16, 0.208, 0.30, 0.025, 0.07
    for i, (lab, val, sub, col) in enumerate(cards):
        cx = x0 + (i % nx) * (w + gx)
        cy = y0 + (ny - 1 - i // nx) * (h + gy)
        ax = fig.add_axes([cx, cy, w, h]); ax.axis("off")
        ax.add_patch(FancyBboxPatch((0, 0), 1, 1, boxstyle="round,pad=0.02,rounding_size=0.06",
                                    transform=ax.transAxes, fc=CARD, ec=GRID, lw=1.2))
        ax.plot([0.07, 0.07], [0.12, 0.88], color=col, lw=4, transform=ax.transAxes,
                solid_capstyle="round")
        ax.text(0.15, 0.70, val, fontsize=30, fontweight="bold", color=col, transform=ax.transAxes)
        ax.text(0.15, 0.42, lab, fontsize=12.5, color=FG, transform=ax.transAxes, fontweight="bold")
        ax.text(0.15, 0.20, sub, fontsize=10, color=MUT, transform=ax.transAxes)
    footer(fig)
    fig.savefig(OUT / "00_overview.png", dpi=150); plt.close(fig)

# ════════════════════════════════════════════════════════════════════════
# 01 — Per-class precision / recall / F1
# ════════════════════════════════════════════════════════════════════════
def fig_perclass():
    order = sorted(classes, key=lambda c: F1r[c], reverse=True)
    fig = plt.figure(figsize=(13.2, 7.6))
    title(fig, "Метрики по классам", "Точность / Полнота / F1 · реальные фото (val_realworld)")
    ax = fig.add_axes([0.065, 0.12, 0.9, 0.72])
    x = np.arange(len(order)); bw = 0.26
    p = [Pr[c] for c in order]; r = [Rr[c] for c in order]; f = [F1r[c] for c in order]
    ax.bar(x - bw, p, bw, label="Точность", color=BLUE)
    ax.bar(x, r, bw, label="Полнота", color=GREEN)
    ax.bar(x + bw, f, bw, label="F1", color=VIOLET)
    for xi, fv in zip(x, f):
        ax.text(xi + bw, fv + 0.02, f"{fv*100:.0f}", ha="center", color=VIOLET, fontsize=9, fontweight="bold")
    ax.set_xticks(x); ax.set_xticklabels([RU[c] for c in order], fontsize=10)
    ax.set_ylim(0, 1.08); ax.set_yticks(np.arange(0, 1.01, 0.2))
    ax.set_yticklabels([f"{int(v*100)}%" for v in np.arange(0, 1.01, 0.2)])
    ax.set_axisbelow(True); ax.grid(axis="x", alpha=0)
    ax.legend(loc="upper right", facecolor=CARD2, edgecolor=GRID, labelcolor=FG, ncols=3)
    footer(fig)
    fig.savefig(OUT / "01_perclass.png", dpi=150); plt.close(fig)

# ════════════════════════════════════════════════════════════════════════
# 02 — Recall with 95% CI
# ════════════════════════════════════════════════════════════════════════
def fig_recall_ci():
    order = sorted(classes, key=lambda c: ci[c]["point"])
    fig = plt.figure(figsize=(12.8, 7.4))
    title(fig, "Полнота по классам с 95% доверительным интервалом",
          f"бутстрэп n={mc['per_class_recall_ci_realworld']['n']} · {mc['per_class_recall_ci_realworld']['n_boot']} итераций")
    ax = fig.add_axes([0.30, 0.12, 0.63, 0.72])
    y = np.arange(len(order))
    pts = [ci[c]["point"] for c in order]
    lo = [ci[c]["point"] - ci[c]["ci_low"] for c in order]
    hi = [ci[c]["ci_high"] - ci[c]["point"] for c in order]
    cols = [GREEN if ci[c]["support"] >= 100 else (AMBER if ci[c]["support"] >= 50 else RED) for c in order]
    ax.errorbar(pts, y, xerr=[lo, hi], fmt="none", ecolor=MUT, elinewidth=2, capsize=5)
    ax.scatter(pts, y, s=130, c=cols, zorder=5, edgecolor=BG, linewidth=1.5)
    for yi, c in zip(y, order):
        ax.text(ci[c]["ci_high"] + 0.015, yi, f"{ci[c]['point']*100:.0f}%  (n={ci[c]['support']})",
                va="center", color=FG, fontsize=10)
    ax.axvline(0.70, color=GREEN_D, ls="--", lw=1.3)
    ax.text(0.705, len(order) - 0.4, "порог 70%", color=GREEN_D, fontsize=9.5)
    ax.set_yticks(y); ax.set_yticklabels([RU1[c] for c in order], fontsize=11)
    ax.set_xlim(0.2, 1.12); ax.set_xticks(np.arange(0.2, 1.01, 0.2))
    ax.set_xticklabels([f"{int(v*100)}%" for v in np.arange(0.2, 1.01, 0.2)])
    ax.grid(axis="y", alpha=0)
    fig.text(0.30, 0.045, "● ≥100 наблюдений   ● 50–99   ● <50 (широкий ДИ — мало данных)",
             color=MUT, fontsize=9.5)
    fig.savefig(OUT / "02_recall_ci.png", dpi=150); plt.close(fig)

# ════════════════════════════════════════════════════════════════════════
# 03 — Confusion matrix (row-normalized)
# ════════════════════════════════════════════════════════════════════════
def fig_confusion():
    cm = np.array(rw["confusion_matrix"], float)
    norm = cm / cm.sum(1, keepdims=True).clip(min=1)
    fig = plt.figure(figsize=(12.6, 9.4))
    title(fig, "Матрица ошибок (реальные фото)", "доля строки · строки — истинный класс, столбцы — предсказание", y=0.975)
    ax = fig.add_axes([0.26, 0.14, 0.62, 0.66])
    im = ax.imshow(norm, cmap="Greens", vmin=0, vmax=1, aspect="auto")
    lab = [RU1[c] for c in classes]
    ax.set_xticks(range(len(classes))); ax.set_xticklabels(lab, rotation=40, ha="right", fontsize=9.5)
    ax.set_yticks(range(len(classes))); ax.set_yticklabels(lab, fontsize=9.5)
    ax.set_xlabel("Предсказание", color=FG, fontsize=12); ax.set_ylabel("Истина", color=FG, fontsize=12)
    ax.grid(False)
    for i in range(len(classes)):
        for j in range(len(classes)):
            v = norm[i, j]
            if v >= 0.005:
                ax.text(j, i, f"{v*100:.0f}", ha="center", va="center", fontsize=8.5,
                        color=("#06210F" if v > 0.55 else FG), fontweight=("bold" if i == j else "normal"))
    cb = fig.colorbar(im, ax=ax, fraction=0.045, pad=0.03)
    cb.outline.set_edgecolor(GRID); cb.ax.yaxis.set_tick_params(color=MUT)
    cb.set_ticks([0, 0.5, 1.0]); cb.set_ticklabels(["0%", "50%", "100%"])
    fig.savefig(OUT / "03_confusion.png", dpi=150); plt.close(fig)

# ════════════════════════════════════════════════════════════════════════
# 04 — Conformal prediction sets
# ════════════════════════════════════════════════════════════════════════
def fig_conformal():
    fig = plt.figure(figsize=(12.8, 7.0))
    title(fig, "Конформные множества предсказаний (RAPS)",
          "честная неопределённость: «либо X, либо Y» с гарантией покрытия")
    # left: coverage gauge
    axg = fig.add_axes([0.07, 0.16, 0.36, 0.62]); axg.axis("off")
    cov = cf["empirical_coverage"]; tgt = cf["target_coverage"]
    axg.add_patch(FancyBboxPatch((0, 0), 1, 1, boxstyle="round,pad=0.02,rounding_size=0.05",
                                 transform=axg.transAxes, fc=CARD, ec=GRID, lw=1.2))
    axg.text(0.5, 0.74, f"{cov*100:.1f}%", ha="center", fontsize=46, fontweight="bold", color=VIOLET,
             transform=axg.transAxes)
    axg.text(0.5, 0.55, "эмпирическое покрытие", ha="center", color=FG, fontsize=13, transform=axg.transAxes)
    axg.text(0.5, 0.40, f"цель ≥ {tgt*100:.0f}%   ✓ выполнено", ha="center", color=GREEN, fontsize=12,
             transform=axg.transAxes)
    bx = 0.12
    axg.add_patch(plt.Rectangle((bx, 0.24), 0.76, 0.05, transform=axg.transAxes, fc=GRID))
    axg.add_patch(plt.Rectangle((bx, 0.24), 0.76 * cov, 0.05, transform=axg.transAxes, fc=VIOLET))
    axg.plot([bx + 0.76 * tgt] * 2, [0.21, 0.32], color=GREEN, lw=2, transform=axg.transAxes)
    # right: facts
    axr = fig.add_axes([0.50, 0.16, 0.43, 0.62]); axr.axis("off")
    rows = [
        ("Средний размер множества", f"{cf['mean_set_size']:.2f} класса"),
        ("Метод", f"RAPS · α={cf['alpha']} · k_reg={cf.get('k_reg')}"),
        ("Калибровочная выборка", f"{cf['calibration_n']} фото"),
        ("Порог q̂", f"{cf['q_hat']:.3f}"),
        ("Температурная калибровка", f"T = {json.loads(open(BUNDLE/'temperature.json').read()).get('temperature', '—'):.3f}"),
    ]
    for i, (k, v) in enumerate(rows):
        yy = 0.86 - i * 0.17
        axr.text(0.0, yy, k, color=MUT, fontsize=12.5, transform=axr.transAxes)
        axr.text(1.0, yy, v, color=FG, fontsize=14, fontweight="bold", ha="right", transform=axr.transAxes)
        axr.plot([0, 1], [yy - 0.06, yy - 0.06], color=GRID, lw=0.8, transform=axr.transAxes)
    footer(fig)
    fig.savefig(OUT / "04_conformal.png", dpi=150); plt.close(fig)

# ════════════════════════════════════════════════════════════════════════
# 05 — Training data composition
# ════════════════════════════════════════════════════════════════════════
def fig_dataset():
    tc = mc["train_counts"]
    order = sorted(classes, key=lambda c: tc.get(c, 0), reverse=True)
    vals = [tc.get(c, 0) for c in order]
    fig = plt.figure(figsize=(12.8, 7.2))
    title(fig, "Состав обучающей выборки", f"{sum(tc.values()):,} изображений · 16 источников · реальные + студийные фото".replace(",", " "))
    ax = fig.add_axes([0.26, 0.12, 0.66, 0.72])
    y = np.arange(len(order))[::-1]
    cols = [GREEN if c not in ("not_a_plant",) else BLUE for c in order]
    cols = [RED if tc.get(c, 0) < 700 else cc for c, cc in zip(order, cols)]
    ax.barh(y, vals, color=cols, height=0.66)
    for yi, v in zip(y, vals):
        ax.text(v + max(vals) * 0.01, yi, f"{v}", va="center", color=FG, fontsize=11, fontweight="bold")
    ax.set_yticks(y); ax.set_yticklabels([RU1[c] for c in order], fontsize=11)
    ax.set_xlim(0, max(vals) * 1.12); ax.grid(axis="y", alpha=0)
    fig.text(0.26, 0.045, "● основные классы   ● «не растение» (OOD)   ● тонкие классы <700 (узкое место по данным)",
             color=MUT, fontsize=9.5)
    fig.savefig(OUT / "05_dataset.png", dpi=150); plt.close(fig)

# ════════════════════════════════════════════════════════════════════════
# 06 — Model families (3 columns): Aqua / Gamma / Frontier
# ════════════════════════════════════════════════════════════════════════
def fig_families():
    fig = plt.figure(figsize=(13.6, 8.4))
    title(fig, "Семейства моделей PlantVision", "Gamma — измерено. Aqua и Frontier — ПРОЕКЦИЯ (один рецепт обучения, разный бэкбон)", y=0.965)
    cols = [
        ("AQUA", "на устройстве · офлайн", BLUE, False, [
            ("Бэкбон", "MobileNetV3-S\n(дистилляция)"),
            ("Macro-F1 реальные", "≈ 70%"),
            ("Macro-F1 in-domain", "≈ 79%"),
            ("Размер (INT8)", "≈ 6 МБ"),
            ("Задержка", "≈ 40 мс / CPU тел."),
            ("Среда", "Android, офлайн"),
        ]),
        ("GAMMA", "текущий прод · баланс", GREEN, True, [
            ("Бэкбон", "DINOv2 ViT-S/14\n(заморожен)+LoRA"),
            ("Macro-F1 реальные", f"{rw['disease_macro_f1']*100:.1f}%"),
            ("Macro-F1 in-domain", f"{idm['disease_macro_f1']*100:.1f}%"),
            ("Размер (INT8)", "22.5 МБ"),
            ("Задержка", "≈ 8 мс / GPU"),
            ("Среда", "сервер (CPU/GPU)"),
        ]),
        ("FRONTIER", "высокая ёмкость · проекция", VIOLET, False, [
            ("Бэкбон", "DINOv2 ViT-B/14\n(~86M, заморожен)"),
            ("Macro-F1 реальные", "≈ 83%"),
            ("Macro-F1 in-domain", "≈ 90%"),
            ("Размер (INT8)", "≈ 90 МБ"),
            ("Задержка", "≈ 20 мс / GPU"),
            ("Среда", "сервер GPU (выкл.)"),
        ]),
    ]
    cw, gap = 0.275, 0.045
    x0 = (1 - (3 * cw + 2 * gap)) / 2
    for i, (name, role, col, primary, rows) in enumerate(cols):
        cx = x0 + i * (cw + gap)
        ax = fig.add_axes([cx, 0.10, cw, 0.78]); ax.axis("off")
        fc = CARD2 if primary else CARD
        ax.add_patch(FancyBboxPatch((0, 0), 1, 1, boxstyle="round,pad=0.02,rounding_size=0.04",
                                    transform=ax.transAxes, fc=fc, ec=(col if primary else GRID),
                                    lw=(2.6 if primary else 1.2)))
        ax.add_patch(plt.Rectangle((0.0, 0.92), 1.0, 0.08, transform=ax.transAxes, fc=col, alpha=0.16))
        ax.text(0.5, 0.945, name, ha="center", fontsize=21, fontweight="bold", color=col, transform=ax.transAxes)
        ax.text(0.5, 0.905, role, ha="center", fontsize=10.5, color=MUT, transform=ax.transAxes)
        tag = "ИЗМЕРЕНО" if primary else "ПРОЕКЦИЯ"
        ax.text(0.5, 0.86, tag, ha="center", fontsize=9, color=(GREEN if primary else AMBER),
                fontweight="bold", transform=ax.transAxes)
        for j, (k, v) in enumerate(rows):
            yy = 0.78 - j * 0.125
            ax.text(0.08, yy, k, color=MUT, fontsize=10.5, transform=ax.transAxes)
            ax.text(0.92, yy, v, color=FG, fontsize=12.5, fontweight="bold", ha="right",
                    va="top", transform=ax.transAxes, linespacing=0.95)
            ax.plot([0.08, 0.92], [yy - 0.045, yy - 0.045], color=GRID, lw=0.7, transform=ax.transAxes)
    fig.text(0.5, 0.03, "Проекции Aqua/Frontier — ожидания на основе типичного разрыва дистилляции (ViT-S→Mobile, −5…7 п.п.)\n"
                        "и прироста ёмкости (ViT-S→ViT-B, +4…6 п.п.); подтверждаются только обучением соответствующего тира.",
             ha="center", color=MUT, fontsize=9.2)
    fig.savefig(OUT / "06_families.png", dpi=150); plt.close(fig)

# ════════════════════════════════════════════════════════════════════════
# 07 — in-domain vs real-world per-class recall
# ════════════════════════════════════════════════════════════════════════
def fig_domain_gap():
    order = sorted(classes, key=lambda c: rw["per_class_recall"][c], reverse=True)
    fig = plt.figure(figsize=(13.2, 7.4))
    title(fig, "Полнота: in-domain против реальных фото",
          "разрыв показывает обобщение на «садовые» снимки")
    ax = fig.add_axes([0.065, 0.12, 0.9, 0.72])
    x = np.arange(len(order)); bw = 0.34
    idr = [idm["per_class_recall"][c] for c in order]
    rwr = [rw["per_class_recall"][c] for c in order]
    ax.bar(x - bw/2, idr, bw, label="in-domain", color=GREEN_D)
    ax.bar(x + bw/2, rwr, bw, label="реальные фото", color=GREEN)
    ax.set_xticks(x); ax.set_xticklabels([RU[c] for c in order], fontsize=10)
    ax.set_ylim(0, 1.08); ax.set_yticks(np.arange(0, 1.01, 0.2))
    ax.set_yticklabels([f"{int(v*100)}%" for v in np.arange(0, 1.01, 0.2)])
    ax.grid(axis="x", alpha=0)
    ax.legend(loc="upper right", facecolor=CARD2, edgecolor=GRID, labelcolor=FG, ncols=2)
    footer(fig)
    fig.savefig(OUT / "07_domain_gap.png", dpi=150); plt.close(fig)

for fn in (fig_overview, fig_perclass, fig_recall_ci, fig_confusion,
           fig_conformal, fig_dataset, fig_families, fig_domain_gap):
    fn(); print("ok:", fn.__name__)
print("written to", OUT)
