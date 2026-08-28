"""Отрисовка фона окна входа: Земля из космоса, спутник, луч на станцию.

Зачем отдельный сценарий, а не картинка в репозитории: изолированная машина
ничего не скачивает, а тащить в репозиторий несколько мегабайт бинарного
файла ради заставки не хочется. Сценарий детерминированный — один и тот же
seed даёт один и тот же кадр, — и его можно перезапустить, если понадобится
другое разрешение или другой ракурс.

    python3 tools/make_login_bg.py src/reportgen/web/static/login-bg.jpg

Своя фотография всегда важнее нарисованной: положите файл login-bg.jpg рядом
с settings.json, и окно входа возьмёт его (см. Settings.brand_login_image).
Снимки Земли в общественном достоянии есть на сайтах NASA.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

WIDTH, HEIGHT = 2560, 1440
SEED = 20260828

# Центр планеты вынесен далеко вниз: в кадре остаётся только кромка шара —
# так Земля и выглядит с низкой орбиты.
CENTER = np.array([0.42 * WIDTH, 2.62 * HEIGHT])
RADIUS = 2.05 * HEIGHT
# Солнце почти сбоку слева: в кадре виден узкий серп планеты, и свет обязан
# меняться поперёк него. При солнце «из-за камеры» освещённость по всей дуге
# одинаковая, терминатора нет, и кадр выглядит дневным небом, а не космосом.
SUN = np.array([-0.88, -0.30, 0.37])
SUN /= np.linalg.norm(SUN)


def _smoothstep(edge0: float, edge1: float, value: np.ndarray) -> np.ndarray:
    t = np.clip((value - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _value_noise(shape: tuple[int, int], cells: tuple[int, int],
                 rng: np.random.Generator) -> np.ndarray:
    """Гладкий шум: решётка случайных значений с косинусной интерполяцией.

    Пригодного шума Перлина в зависимостях нет, а тащить его ради одной
    картинки незачем: на облака хватает и этого — важна связность пятен,
    а не математическая чистота.
    """
    grid = rng.random((cells[0] + 1, cells[1] + 1))
    ys = np.linspace(0, cells[0], shape[0], endpoint=False)
    xs = np.linspace(0, cells[1], shape[1], endpoint=False)
    y0, x0 = np.floor(ys).astype(int), np.floor(xs).astype(int)
    fy = (1 - np.cos((ys - y0) * np.pi)) / 2
    fx = (1 - np.cos((xs - x0) * np.pi)) / 2
    top = grid[y0][:, x0] * (1 - fx) + grid[y0][:, x0 + 1] * fx
    bottom = grid[y0 + 1][:, x0] * (1 - fx) + grid[y0 + 1][:, x0 + 1] * fx
    return top * (1 - fy[:, None]) + bottom * fy[:, None]


def _fbm(shape: tuple[int, int], rng: np.random.Generator,
         octaves: int = 6, base: int = 4) -> np.ndarray:
    """Сумма шумов растущей частоты — то, из чего получается облачность."""
    total = np.zeros(shape)
    amplitude, weight = 1.0, 0.0
    for octave in range(octaves):
        cells = (base * 2 ** octave, base * 2 ** octave * shape[1] // shape[0])
        total += amplitude * _value_noise(shape, cells, rng)
        weight += amplitude
        amplitude *= 0.5
    return total / weight


def render() -> Image.Image:
    rng = np.random.default_rng(SEED)
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH].astype(np.float64)

    # -- космос: почти чёрный, с еле заметной засветкой в стороне солнца ----
    glow = np.exp(-(((xx - 0.18 * WIDTH) ** 2 + (yy - 0.05 * HEIGHT) ** 2)
                    / (2 * (0.50 * WIDTH) ** 2)))
    image = np.empty((HEIGHT, WIDTH, 3))
    image[..., 0] = 0.0030 + 0.0060 * glow
    image[..., 1] = 0.0048 + 0.0100 * glow
    image[..., 2] = 0.0095 + 0.0195 * glow

    # -- звёзды -------------------------------------------------------------
    # Ярких мало, тусклых много: так выглядит настоящее звёздное поле.
    for count, low, high, size in ((1400, 0.06, 0.22, 0), (260, 0.25, 0.55, 0),
                                   (70, 0.55, 0.95, 1)):
        sy = rng.integers(0, HEIGHT, count)
        sx = rng.integers(0, WIDTH, count)
        brightness = rng.uniform(low, high, count)
        # Цвет звезды чуть плавает от голубого к тёплому.
        tint = rng.uniform(-0.10, 0.06, count)
        for index in range(count):
            y, x = int(sy[index]), int(sx[index])
            value = brightness[index]
            for dy in range(-size, size + 1):
                for dx in range(-size, size + 1):
                    py, px = y + dy, x + dx
                    if not (0 <= py < HEIGHT and 0 <= px < WIDTH):
                        continue
                    fall = value if (dy or dx) == 0 else value * 0.35
                    image[py, px, 0] += fall * (1 + tint[index])
                    image[py, px, 1] += fall
                    image[py, px, 2] += fall * (1 - tint[index] * 0.5)

    # -- геометрия шара -----------------------------------------------------
    dx = xx - CENTER[0]
    dy = yy - CENTER[1]
    distance = np.sqrt(dx * dx + dy * dy)
    inside = distance <= RADIUS

    # Нормаль к поверхности в точке экрана: z из уравнения сферы.
    nx = np.where(inside, dx / RADIUS, 0.0)
    ny = np.where(inside, dy / RADIUS, 0.0)
    nz = np.sqrt(np.clip(1.0 - nx * nx - ny * ny, 0.0, 1.0))
    lambert = np.clip(nx * SUN[0] + ny * SUN[1] + nz * SUN[2], 0.0, 1.0)
    # Терминатор мягкий: у планеты с атмосферой резкой границы света нет.
    light = _smoothstep(0.0, 0.42, lambert) ** 1.25

    # -- поверхность --------------------------------------------------------
    clouds = _fbm((HEIGHT, WIDTH), rng, octaves=7, base=3)
    # Облачные полосы вытянуты вдоль широт: сжимаем шум по вертикали.
    bands = _fbm((HEIGHT, WIDTH), np.random.default_rng(SEED + 1), octaves=4, base=2)
    clouds = np.clip(clouds * 0.72 + bands * 0.28, 0, 1)
    cover = _smoothstep(0.50, 0.78, clouds)

    ocean = np.stack([np.full((HEIGHT, WIDTH), 0.020),
                      np.full((HEIGHT, WIDTH), 0.062),
                      np.full((HEIGHT, WIDTH), 0.150)], axis=-1)
    cloud_colour = np.stack([np.full((HEIGHT, WIDTH), 0.66),
                             np.full((HEIGHT, WIDTH), 0.72),
                             np.full((HEIGHT, WIDTH), 0.80)], axis=-1)
    surface = ocean * (1 - cover[..., None]) + cloud_colour * cover[..., None]
    surface *= light[..., None]

    # Рассеяние у самой кромки диска: узкая голубая каёмка на освещённой
    # стороне. Широкая полоса делала планету светлой целиком.
    edge = _smoothstep(0.975, 1.0, distance / RADIUS)
    scatter = np.stack([0.10 * edge, 0.20 * edge, 0.38 * edge], axis=-1)
    surface = np.clip(surface + scatter * light[..., None], 0, 1)

    image = np.where(inside[..., None], surface, image)

    # -- атмосфера снаружи диска -------------------------------------------
    # Узкая яркая полоса у самого горизонта плюс короткий мягкий ореол.
    # Широкий ореол заливал полкадра, и небо переставало быть чёрным.
    outside = np.clip(distance - RADIUS, 0, None)
    rim = np.exp(-outside / (0.0018 * RADIUS))
    halo = np.exp(-outside / (0.014 * RADIUS)) * 0.22
    # Свечение гаснет вместе со светом: над ночной стороной атмосферы не видно.
    air = (rim + halo) * light
    air = np.where(inside, 0.0, air)
    image[..., 0] += air * 0.30
    image[..., 1] += air * 0.55
    image[..., 2] += air * 0.92

    _add_satellite(image, xx, yy)

    return Image.fromarray((np.clip(image, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8))


def _limb_y(x: float) -> float:
    """Высота горизонта планеты в этой точке кадра."""
    dx = x - CENTER[0]
    return float(CENTER[1] - np.sqrt(max(RADIUS ** 2 - dx * dx, 0.0)))


def _add_satellite(image: np.ndarray, xx: np.ndarray, yy: np.ndarray) -> None:
    """Спутник и луч на наземную станцию.

    Рисуется прямо в растр, а не поверх разметкой: тогда не надо совмещать
    две системы координат при другом соотношении сторон экрана, и спутник
    остаётся на своём месте относительно планеты при любой обрезке кадра.

    Аппарат намеренно мелкий и почти силуэтом: крупный подробный спутник
    посреди снимка сразу выдаёт рисунок.
    """
    sat_x, sat_y = 0.735 * WIDTH, 0.255 * HEIGHT
    ground_x = 0.655 * WIDTH
    ground_y = _limb_y(ground_x)

    # -- луч: узкий конус от аппарата к точке на горизонте ------------------
    along = np.array([ground_x - sat_x, ground_y - sat_y])
    length = float(np.hypot(*along))
    along /= length
    across = np.array([-along[1], along[0]])
    rel_x, rel_y = xx - sat_x, yy - sat_y
    depth = rel_x * along[0] + rel_y * along[1]
    side = np.abs(rel_x * across[0] + rel_y * across[1])
    t = np.clip(depth / length, 0.0, 1.0)
    width = 4.0 + 46.0 * t                       # раскрыв диаграммы к земле
    inside_beam = (depth > 0) & (depth < length) & (side < width)
    # Ярче у аппарата, мягче к поверхности, размыто по краям конуса.
    strength = (1.0 - t) ** 1.6 * (1.0 - side / np.maximum(width, 1e-6)) ** 2
    beam = np.where(inside_beam, strength, 0.0) * 0.075
    image[..., 0] += beam * 0.55
    image[..., 1] += beam * 0.78
    image[..., 2] += beam * 1.00

    # -- корпус и панели ----------------------------------------------------
    def box(cx, cy, half_w, half_h, value, tint=(1.0, 1.0, 1.0)):
        mask = (np.abs(xx - cx) < half_w) & (np.abs(yy - cy) < half_h)
        for channel, factor in enumerate(tint):
            image[..., channel] = np.where(mask, value * factor, image[..., channel])

    scale = HEIGHT / 1440.0
    body_w, body_h = 11 * scale, 15 * scale
    panel_w, panel_h = 27 * scale, 10 * scale
    gap = body_w + 6 * scale

    box(sat_x - gap - panel_w, sat_y, panel_w, panel_h, 0.055, (0.85, 0.95, 1.15))
    box(sat_x + gap + panel_w, sat_y, panel_w, panel_h, 0.055, (0.85, 0.95, 1.15))
    box(sat_x - gap - panel_w, sat_y, panel_w, 1.0 * scale, 0.020)   # шов панели
    box(sat_x + gap + panel_w, sat_y, panel_w, 1.0 * scale, 0.020)
    box(sat_x - gap / 2 - panel_w / 2, sat_y, gap / 2 + panel_w / 2, 1.2 * scale, 0.10)
    box(sat_x + gap / 2 + panel_w / 2, sat_y, gap / 2 + panel_w / 2, 1.2 * scale, 0.10)
    box(sat_x, sat_y, body_w, body_h, 0.075)
    # Блик солнца на обращённой к светилу грани — аппарат перестаёт быть
    # плоским пятном.
    box(sat_x - body_w + 1.6 * scale, sat_y, 1.8 * scale, body_h, 0.42, (1.0, 1.0, 1.02))

    # -- отметка наземной станции ------------------------------------------
    spot = np.exp(-(((xx - ground_x) ** 2 + (yy - ground_y) ** 2)
                    / (2 * (7.0 * scale) ** 2)))
    image[..., 0] += spot * 0.30
    image[..., 1] += spot * 0.45
    image[..., 2] += spot * 0.60


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1
                  else "src/reportgen/web/static/login-bg.jpg")
    target.parent.mkdir(parents=True, exist_ok=True)
    picture = render()
    picture.save(target, quality=88, optimize=True, progressive=True)
    print(f"{target} — {picture.size[0]}×{picture.size[1]}, "
          f"{target.stat().st_size / 1024:.0f} КБ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
