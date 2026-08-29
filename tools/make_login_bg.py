"""Отрисовка фона окна входа: Земля с орбиты по настоящему снимку.

Кадр строится трассировкой лучей по шару, на который натянут снимок NASA
Blue Marble Next Generation — мозаика съёмки MODIS с аппарата Terra. Снимок
в общественном достоянии, как и всё, что снимает NASA.

Почему сценарий, а не готовая картинка в репозитории: сама мозаика весит
больше двух мегабайт, и держать её в истории ради заставки незачем. В
репозиторий кладётся только готовый кадр, а исходник берётся один раз.

    # мозаика лежит внутри пакета basemap-data (PyPI), качать с сайтов не надо
    pip download basemap-data --no-deps -d /tmp/bm
    unzip -o /tmp/bm/*.whl -d /tmp/bmx
    python3 tools/make_login_bg.py src/reportgen/web/static/login-bg.jpg \\
        --texture /tmp/bmx/mpl_toolkits/basemap_data/bmng.jpg

Без --texture сценарий рисует шар по своему рельефу: кадр выходит беднее, но
собирается на машине, где мозаики нет.

Своя фотография всегда важнее: положите файл login-bg.jpg рядом с
settings.json, и окно входа возьмёт его (см. Settings.brand_login_image).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

WIDTH, HEIGHT = 2560, 1440
#: Кратность передискретизации. Кромка планеты и звёзды — это тонкие детали
#: в один пиксель; без запаса они рвутся на «лесенку», которую видно на
#: большом мониторе сразу.
SUPERSAMPLE = 2
SEED = 20260829

#: Радиус планеты и высота съёмки в тех же единицах. С низкой орбиты
#: (400 км) горизонт почти прямой и в кадре одна пустыня; с двух тысяч
#: километров видна дуга планеты целиком — так снимают метеоспутники.
R_EARTH = 6371.0
ALTITUDE = 1950.0

#: Наклон камеры вниз от местного горизонта, в градусах. Горизонт опущен на
#: arccos(R/(R+h)); наклон берём чуть меньше, чтобы дуга легла ниже середины
#: кадра: планета занимает низ, вверху остаётся место под карточку входа.
PITCH_DEG = 32.0
FOV_DEG = 58.0

#: Точка под камерой и разворот кадра. Выбраны так, чтобы в кадр попадала
#: узнаваемая суша, а не открытая вода: без берега снимок читается как
#: синее пятно.
SUB_LAT, SUB_LON = 26.0, 44.0
ROLL_DEG = -7.0

#: Точка, над которой стоит Солнце. Задаём широтой и долготой, как и точку
#: под камерой: так видно, где на кадре окажется день, а где ночь. Солнце
#: западнее камеры и ниже по широте — в кадр попадает и освещённая дуга, и
#: терминатор. При солнце «из-за камеры» освещённость по всей дуге
#: одинаковая, терминатора нет, и кадр выглядит дневным небом, а не космосом.
SUN_LAT, SUN_LON = 14.0, -34.0

#: Толщина атмосферы и её цвет. Рэлеевское рассеяние сильнее к синему концу
#: спектра — отсюда и голубая кромка над планетой.
ATMO_H = 105.0
ATMO_RGB = np.array([0.30, 0.55, 1.00])


def _smoothstep(edge0: float, edge1: float, value: np.ndarray) -> np.ndarray:
    t = np.clip((value - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _unit(lat_deg: float, lon_deg: float) -> np.ndarray:
    """Единичный вектор по широте и долготе."""
    lat, lon = np.radians(lat_deg), np.radians(lon_deg)
    return np.array([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])


SUN_DIR = _unit(SUN_LAT, SUN_LON)


def _rotation(sub_lat: float, sub_lon: float, pitch: float, roll: float) -> np.ndarray:
    """Поворот из системы камеры в систему планеты."""
    up = _unit(sub_lat, sub_lon)
    north = np.array([0.0, 0.0, 1.0])
    east = np.cross(north, up)
    east /= np.linalg.norm(east)
    north = np.cross(up, east)

    p, r = np.radians(pitch), np.radians(roll)
    # Взгляд от местного горизонта наклоняем ВНИЗ на угол тангажа: только
    # так луч уходит под горизонт и встречает и планету, и воздух над ней.
    forward = np.cos(p) * north - np.sin(p) * up
    right = east * np.cos(r) + np.cross(forward, east) * np.sin(r)
    right -= forward * float(right @ forward)
    right /= np.linalg.norm(right)
    upv = np.cross(right, forward)
    return np.stack([right, upv, forward], axis=1)


def _rays() -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
    """Начало и направления лучей для каждого пикселя кадра."""
    width, height = WIDTH * SUPERSAMPLE, HEIGHT * SUPERSAMPLE
    aspect = width / height
    scale = np.tan(np.radians(FOV_DEG) * 0.5)

    xs = (np.arange(width) + 0.5) / width * 2.0 - 1.0
    ys = 1.0 - (np.arange(height) + 0.5) / height * 2.0
    gx, gy = np.meshgrid(xs * scale * aspect, ys * scale)

    local = np.stack([gx, gy, np.ones_like(gx)], axis=-1)
    local /= np.linalg.norm(local, axis=-1, keepdims=True)

    basis = _rotation(SUB_LAT, SUB_LON, PITCH_DEG, ROLL_DEG)
    directions = local @ basis.T

    origin = _unit(SUB_LAT, SUB_LON) * (R_EARTH + ALTITUDE)
    return origin, directions, (width, height)


def _hit_sphere(origin: np.ndarray, direction: np.ndarray, radius: float) -> np.ndarray:
    """Ближнее пересечение луча со сферой. Промах — NaN."""
    b = 2.0 * (direction @ origin)
    c = float(origin @ origin) - radius * radius
    disc = b * b - 4.0 * c
    hit = disc > 0.0
    root = np.full(disc.shape, np.nan)
    sq = np.sqrt(np.where(hit, disc, 0.0))
    near = (-b - sq) * 0.5
    far = (-b + sq) * 0.5
    take = np.where(near > 0.0, near, far)
    root[hit] = take[hit]
    root[root <= 0.0] = np.nan
    return root


def _sample_texture(texture: np.ndarray, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Билинейная выборка равнопромежуточной мозаики по широте и долготе."""
    th, tw = texture.shape[:2]
    u = (lon / (2.0 * np.pi) + 0.5) * tw - 0.5
    v = (0.5 - lat / np.pi) * th - 0.5

    x0 = np.floor(u).astype(np.int64)
    y0 = np.clip(np.floor(v).astype(np.int64), 0, th - 1)
    fx = (u - x0)[..., None]
    fy = (v - y0)[..., None]
    x0 %= tw
    x1 = (x0 + 1) % tw
    y1 = np.clip(y0 + 1, 0, th - 1)

    top = texture[y0, x0] * (1.0 - fx) + texture[y0, x1] * fx
    bottom = texture[y1, x0] * (1.0 - fx) + texture[y1, x1] * fx
    return top * (1.0 - fy) + bottom * fy


def _fallback_surface(lat: np.ndarray, lon: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Поверхность без мозаики: море и материки по шуму.

    Нужна там, где снимка NASA под рукой нет. Кадр выходит беднее — это
    честно написано в справке сценария.
    """
    value = np.zeros_like(lat)
    amp, freq = 1.0, 1.6
    for _ in range(6):
        phase = rng.uniform(0.0, 2.0 * np.pi, size=3)
        value += amp * (
            np.sin(freq * lat * 2.3 + phase[0])
            * np.sin(freq * lon * 1.7 + phase[1])
            + 0.5 * np.sin(freq * (lat + lon) * 1.1 + phase[2])
        )
        amp *= 0.55
        freq *= 1.9
    land = _smoothstep(0.15, 0.55, value)
    sea = np.array([0.05, 0.12, 0.26])
    soil = np.array([0.24, 0.28, 0.18])
    return sea * (1.0 - land[..., None]) + soil * land[..., None]


def _night_lights(surface: np.ndarray) -> np.ndarray:
    """Свечение городов на ночной стороне.

    Отдельного снимка ночной Земли в мозаике нет, поэтому берём сушу из
    дневного кадра: у воды в Blue Marble синий канал заметно выше зелёного,
    у суши — наоборот. Огни ставим только на суше и вполсилы: ночная
    сторона должна оставаться тёмной, иначе кадр выглядит нарисованным.
    """
    red, green, blue = surface[..., 0], surface[..., 1], surface[..., 2]
    land = _smoothstep(-0.01, 0.05, green - blue) * _smoothstep(0.03, 0.12, red + green)
    return land


def _stars(shape: tuple[int, int], rng: np.random.Generator) -> np.ndarray:
    """Звёздное поле. Ярких звёзд мало, слабых много — как на небе."""
    height, width = shape
    field = np.zeros((height, width), dtype=np.float32)
    count = int(width * height / 5200)
    ys = rng.integers(0, height, count)
    xs = rng.integers(0, width, count)
    # Показатель степени подобран так, чтобы ярких точек были единицы:
    # равномерная яркость даёт «сыпь», которой на небе не бывает.
    mag = rng.random(count) ** 7.0
    np.maximum.at(field, (ys, xs), mag.astype(np.float32))
    return field


def render(texture_path: Path | None) -> Image.Image:
    rng = np.random.default_rng(SEED)
    origin, direction, (width, height) = _rays()

    texture = None
    if texture_path is not None:
        with Image.open(texture_path) as raw:
            texture = np.asarray(raw.convert("RGB"), dtype=np.float32) / 255.0

    distance = _hit_sphere(origin, direction, R_EARTH)
    ground = np.isfinite(distance)

    image = np.zeros((height, width, 3), dtype=np.float32)

    # -- звёзды ------------------------------------------------------------
    stars = _stars((height, width), rng)
    stars = np.asarray(
        Image.fromarray((stars * 255).astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(0.6 * SUPERSAMPLE)), dtype=np.float32) / 255.0
    image += (stars * 1.9)[..., None] * np.array([0.86, 0.90, 1.0])

    # -- поверхность -------------------------------------------------------
    if ground.any():
        hit = origin + direction[ground] * distance[ground][..., None]
        normal = hit / np.linalg.norm(hit, axis=-1, keepdims=True)
        lat = np.arcsin(np.clip(normal[..., 2], -1.0, 1.0))
        lon = np.arctan2(normal[..., 1], normal[..., 0])

        surface = (_sample_texture(texture, lat, lon) if texture is not None
                   else _fallback_surface(lat, lon, rng))

        sun = np.clip(normal @ SUN_DIR, -1.0, 1.0)
        # Терминатор мягкий: у планеты с атмосферой резкой границы нет.
        day = _smoothstep(-0.14, 0.22, sun)
        lit = surface * (0.05 + 0.92 * day[..., None] * np.clip(sun, 0.0, 1.0)[..., None] ** 0.62)

        night = _night_lights(surface) * (1.0 - day)
        lit += night[..., None] * np.array([0.42, 0.31, 0.16]) * 0.30

        # Блик на воде под солнцем: у зеркальной глади он есть всегда, и без
        # него океан выглядит матовой краской.
        view = -direction[ground]
        half = view + SUN_DIR
        half /= np.linalg.norm(half, axis=-1, keepdims=True)
        water = _smoothstep(0.02, 0.10, surface[..., 2] - surface[..., 1])
        gloss = np.clip(np.sum(normal * half, axis=-1), 0.0, 1.0) ** 220.0
        lit += (gloss * water * day)[..., None] * np.array([0.55, 0.62, 0.70])

        image[ground] = lit

    # -- атмосфера ---------------------------------------------------------
    # Толщина воздуха вдоль луча считается по расстоянию до центра планеты:
    # у кромки луч идёт по касательной и проходит через воздух долго, отсюда
    # яркая голубая полоса, которая и делает кадр космическим.
    # Ближайшее расстояние от центра планеты до луча: проекция начала луча
    # на направление даёт точку наибольшего сближения.
    # Точка наибольшего сближения лежит при t = -(d·o). Если она позади
    # камеры, луч уходит от планеты и воздуха не встречает вовсе: без этой
    # оговорки свечение заливало всё небо над горизонтом.
    along = direction @ origin
    miss = np.linalg.norm(origin - direction * along[..., None], axis=-1)
    away = along >= 0.0
    miss = np.where(away, np.linalg.norm(origin), miss)

    shell = _smoothstep(R_EARTH + ATMO_H * 2.6, R_EARTH - ATMO_H * 0.35, miss)
    # За планетой воздуха не видно — там уже поверхность.
    limb = np.where(ground, _smoothstep(0.0, ATMO_H * 0.8, R_EARTH - miss) * 0.55, 1.0)
    # Освещённость самой атмосферы: с ночной стороны она тоже гаснет.
    lit_air = _smoothstep(-0.55, 0.25, direction @ SUN_DIR * -1.0 + 0.45)
    glow = shell * limb * lit_air
    image += glow[..., None] * ATMO_RGB * 0.78

    # Второй, широкий и слабый слой: у настоящего снимка свечение уходит в
    # черноту постепенно, а не обрывается ступенькой.
    # Над самой планетой второго слоя быть не должно: он ложился белёсой
    # вуалью на океан, и вода выходила серой, как на выцветшей печати.
    halo = _smoothstep(R_EARTH + ATMO_H * 9.0, R_EARTH, miss) * (~ground)
    image += (halo * lit_air)[..., None] * ATMO_RGB * 0.16

    # -- сведение ----------------------------------------------------------
    image = np.clip(image, 0.0, None)
    # Мягкое сжатие ярких мест: без него кромка выгорает в белую полосу.
    image = image / (1.0 + image * 0.34)
    image = np.clip(image, 0.0, 1.0) ** (1.0 / 1.10)
    # Небольшая добавка насыщенности: сжатие ярких мест тянет цвета к серому,
    # и океан из синего становится оловянным.
    grey = image @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    image = np.clip(grey[..., None] + (image - grey[..., None]) * 1.22, 0.0, 1.0)

    out = Image.fromarray((image * 255.0 + 0.5).astype(np.uint8), mode="RGB")
    if SUPERSAMPLE > 1:
        out = out.resize((WIDTH, HEIGHT), Image.LANCZOS)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", nargs="?",
                        default="src/reportgen/web/static/login-bg.jpg")
    parser.add_argument("--texture", default=None,
                        help="равнопромежуточная мозаика Земли (NASA Blue Marble)")
    args = parser.parse_args()

    texture = Path(args.texture) if args.texture else None
    if texture is not None and not texture.is_file():
        print(f"мозаика не найдена: {texture}")
        return 2

    target = Path(args.target)
    target.parent.mkdir(parents=True, exist_ok=True)
    render(texture).save(target, quality=88, optimize=True, progressive=True)
    print(f"{target} — {target.stat().st_size // 1024} КБ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
