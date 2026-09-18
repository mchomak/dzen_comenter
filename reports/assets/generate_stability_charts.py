"""Generate SVG charts from the read-only production aggregates in the report."""

from pathlib import Path


PERIODS = [
    ("Articles\npre-batch", 0.0, 0.0, 0.0, (0, 0, 0), 1635, 140.51),
    ("Early\nbatch", 79.60, 90.61, 12.17, (518, 80, 73), 749, 137.91),
    ("Queue\nrecovery", 50.22, 84.42, 6.93, (423, 72, 39), 574, 121.45),
    ("Durable\nqueue", 21.95, 90.00, 33.33, (298, 33, 9), 508, 58.61),
    ("Sanitizer", 0.64, 10.43, 2.61, (237, 26, 8), 424, 61.39),
    ("Current\ndeploy", 1.75, 0.0, 0.0, (158, 19, 11), 317, 36.85),
]

WIDTH, HEIGHT = 1120, 640
LEFT, RIGHT, TOP, BOTTOM = 115, 45, 72, 145
PLOT_W, PLOT_H = WIDTH - LEFT - RIGHT, HEIGHT - TOP - BOTTOM
FONT = "font-family='Arial, sans-serif'"


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, value, size=15, anchor="start", weight="normal", color="#1f2937"):
    return (
        f"<text x='{x:.1f}' y='{y:.1f}' {FONT} font-size='{size}' "
        f"text-anchor='{anchor}' font-weight='{weight}' fill='{color}'>{esc(value)}</text>"
    )


def line(x1, y1, x2, y2, color="#d1d5db", width=1):
    return f"<line x1='{x1:.1f}' y1='{y1:.1f}' x2='{x2:.1f}' y2='{y2:.1f}' stroke='{color}' stroke-width='{width}'/>"


def svg(parts):
    return "\n".join([
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{WIDTH}' height='{HEIGHT}' viewBox='0 0 {WIDTH} {HEIGHT}'>",
        "<rect width='100%' height='100%' fill='#ffffff'/>",
        *parts,
        "</svg>",
    ])


def axes(parts, title, subtitle, y_label, maximum=100):
    parts.extend([text(LEFT, 34, title, 25, weight="bold"), text(LEFT, 57, subtitle, 14, color="#4b5563")])
    for tick in range(0, maximum + 1, 20):
        y = TOP + PLOT_H - PLOT_H * tick / maximum
        parts.append(line(LEFT, y, LEFT + PLOT_W, y))
        parts.append(text(LEFT - 12, y + 5, str(tick), 13, anchor="end", color="#4b5563"))
    parts.append(line(LEFT, TOP, LEFT, TOP + PLOT_H, color="#6b7280", width=1.2))
    parts.append(line(LEFT, TOP + PLOT_H, LEFT + PLOT_W, TOP + PLOT_H, color="#6b7280", width=1.2))
    parts.append(text(25, TOP + PLOT_H / 2, y_label, 14, anchor="middle", color="#4b5563"))


def x_labels(parts):
    step = PLOT_W / len(PERIODS)
    for idx, (label, *_rest) in enumerate(PERIODS):
        x = LEFT + step * (idx + 0.5)
        for line_no, segment in enumerate(label.split("\n")):
            parts.append(text(x, TOP + PLOT_H + 28 + line_no * 17, segment, 13, anchor="middle", color="#374151"))
    return step


def interval_error_chart():
    parts = []
    axes(parts, "Ошибки ответов по периодам", "Reply status = error на момент среза; интервалы имеют разную длительность", "доля от replies, %")
    step = x_labels(parts)
    for idx, (_label, error_rate, *_rest) in enumerate(PERIODS):
        x = LEFT + step * idx + step * 0.28
        width = step * 0.44
        height = PLOT_H * error_rate / 100
        y = TOP + PLOT_H - height
        parts.append(f"<rect x='{x:.1f}' y='{y:.1f}' width='{width:.1f}' height='{height:.1f}' rx='3' fill='#dc2626'/>")
        parts.append(text(x + width / 2, y - 8, f"{error_rate:.2f}", 13, anchor="middle", weight="bold", color="#991b1b"))
    parts.append(text(LEFT, HEIGHT - 26, "Источник: production PostgreSQL, replies.created_at. Даты границ и ограничения — в Markdown-отчёте.", 12, color="#6b7280"))
    return svg(parts)


def format_chart():
    parts = []
    axes(parts, "Служебные метки в сохранённых непустых ответах", "«тип: … ответ:» и «тип: … SKIP» — отдельные подмножества, не stack", "доля непустых текстов, %")
    step = x_labels(parts)
    for idx, (_label, _error, type_rate, skip_rate, *_rest) in enumerate(PERIODS):
        center = LEFT + step * (idx + 0.5)
        for offset, value, color, label in ((-step * 0.17, type_rate, "#7c3aed", "тип/ответ"), (step * 0.02, skip_rate, "#f59e0b", "тип/SKIP")):
            width = step * 0.15
            height = PLOT_H * value / 100
            y = TOP + PLOT_H - height
            x = center + offset
            parts.append(f"<rect x='{x:.1f}' y='{y:.1f}' width='{width:.1f}' height='{height:.1f}' rx='2' fill='{color}'/>")
            if value:
                parts.append(text(x + width / 2, y - 7, f"{value:.1f}", 12, anchor="middle", weight="bold", color=color))
    parts.extend([
        "<rect x='700' y='32' width='12' height='12' fill='#7c3aed'/>", text(719, 43, "тип/ответ", 13, color="#4b5563"),
        "<rect x='823' y='32' width='12' height='12' fill='#f59e0b'/>", text(842, 43, "тип/SKIP", 13, color="#4b5563"),
        text(LEFT, HEIGHT - 26, "Источник: production PostgreSQL, replies.created_at. Санация вступления проверяется поведением, не только датой commit.", 12, color="#6b7280"),
    ])
    return svg(parts)


def batch_size_chart():
    parts = []
    axes(parts, "Фактический размер batch", "Доля batch-вызовов; тройные batch — верхний зелёный сегмент", "доля batch-вызовов, %")
    step = x_labels(parts)
    colors = ("#2563eb", "#60a5fa", "#16a34a")
    for idx, (_label, _error, _type, _skip, sizes, *_rest) in enumerate(PERIODS):
        total = sum(sizes)
        if not total:
            continue
        x = LEFT + step * idx + step * 0.28
        width = step * 0.44
        bottom = TOP + PLOT_H
        for value, color in zip(sizes, colors):
            height = PLOT_H * value / total
            bottom -= height
            parts.append(f"<rect x='{x:.1f}' y='{bottom:.1f}' width='{width:.1f}' height='{height:.1f}' fill='{color}'/>")
        triple = sizes[2] / total * 100
        parts.append(text(x + width / 2, bottom - 8, f"3: {triple:.1f}%", 12, anchor="middle", weight="bold", color="#166534"))
    parts.extend([
        "<rect x='710' y='32' width='12' height='12' fill='#2563eb'/>", text(729, 43, "1 комментарий", 13, color="#4b5563"),
        "<rect x='842' y='32' width='12' height='12' fill='#60a5fa'/>", text(861, 43, "2 комментария", 13, color="#4b5563"),
        "<rect x='978' y='32' width='12' height='12' fill='#16a34a'/>", text(997, 43, "3 комментария", 13, color="#4b5563"),
        text(LEFT, HEIGHT - 26, "Источник: production PostgreSQL, reply_batches.created_at. Период до batch не показан: batch-вызовов не было.", 12, color="#6b7280"),
    ])
    return svg(parts)


def main():
    target = Path(__file__).parent
    (target / "stability-error-rates.svg").write_text(interval_error_chart(), encoding="utf-8")
    (target / "stability-format-markers.svg").write_text(format_chart(), encoding="utf-8")
    (target / "stability-batch-sizes.svg").write_text(batch_size_chart(), encoding="utf-8")


if __name__ == "__main__":
    main()
