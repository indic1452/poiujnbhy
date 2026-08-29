"""Отрисовка фона окна входа: ночная Земля с орбиты.

Кадр повторяет то, что видит человек с борта станции ночью: чёрное небо,
тонкая зелёная полоса свечения воздуха над кромкой, тёмная планета и цепочки
городских огней вдоль побережий. Строится трассировкой лучей по шару, на
который натянуты две мозаики NASA:

* «Black Marble» — ночная съёмка огней (Suomi NPP/VIIRS);
* «Blue Marble» — дневная мозаика, из неё берётся только слабая подсветка
  рельефа, чтобы материки не тонули в чёрном.

Обе мозаики — общественное достояние, как и всё, что снимает NASA.

Почему сценарий, а не готовая картинка в репозитории: мозаики весят больше
двух мегабайт каждая, и держать их в истории ради заставки незачем. В
репозиторий кладётся только готовый кадр, а исходники берутся один раз:

    # мозаики лежат внутри пакета three-globe (npm), качать с сайтов не надо
    npm pack three-globe
    tar xzf three-globe-*.tgz package/example/img/earth-night.jpg \\
        package/example/img/earth-blue-marble.jpg
    python3 tools/make_login_bg.py src/reportgen/web/static/login-bg.jpg \\
        --night package/example/img/earth-night.jpg \\
        --texture package/example/img/earth-blue-marble.jpg

Без мозаик сценарий рисует шар по своему рельефу: кадр выходит беднее, но
собирается на машине, где мозаик нет.

В поставке лежит настоящая фотография ночной Земли, переданная отделом, —
этот сценарий нужен тому, у кого своего снимка нет.

Своя фотография всегда важнее: положите файл login-bg.jpg рядом с
settings.json, и окно входа возьмёт его (см. Settings.brand_login_image).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

WIDTH, HEIGHT = 2560, 1440
#: Кратность передискретизации. Кромка планеты, огни городов и звёзды — это
#: детали в один пиксель; без запаса они рвутся на «лесенку», которую видно
#: на большом мониторе сразу.
SUPERSAMPLE = 2
SEED = 20260829

#: Радиус планеты и высота съёмки в тех же единицах. Станция ходит на 400 км,
#: но оттуда горизонт почти прямой; берём выше, чтобы дуга планеты читалась
#: и на широком мониторе.
R_EARTH = 6371.0
ALTITUDE = 2600.0

#: Наклон камеры вниз от местного горизонта и поле зрения по вертикали.
#: Горизонт опущен на arccos(R/(R+h)) ≈ 32.7°; наклон больше — значит
#: горизонт уходит выше середины кадра, и планета занимает низ, а под
#: карточку входа остаётся тёмное небо.
PITCH_DEG = 52.0
FOV_DEG = 56.0

#: Точка под камерой и разворот кадра. Берём Адриатику: побережья Италии,
#: Балкан и юга Европы дают ту самую узнаваемую вязь огней. По открытой воде
#: ночной кадр читается как чёрный прямоугольник.
SUB_LAT, SUB_LON = 43.0, 15.0
ROLL_DEG = -6.0

#: Точка, над которой стоит Солнце. Уводим его на другую сторону планеты:
#: в кадре должна быть ночь целиком, без светлой дуги терминатора.
SUN_LAT, SUN_LON = -18.0, -155.0

#: Высота слоя свечения воздуха. Ночное свечение атмосферы (возбуждённый
#: кислород) идёт с 85–100 км — на снимках это узкая зеленоватая полоса над
#: самой кромкой, ради неё кадр и выглядит съёмкой, а не рисунком.
AIRGLOW_H = 92.0
AIRGLOW_RGB = np.array([0.34, 0.78, 0.60])
#: Рассеянный воздух над планетой: сине-серая дымка, гаснущая в черноту.
HAZE_RGB = np.array([0.26, 0.42, 0.72])


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


def _rays(scale_down: int = 1) -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
    """Начало и направления лучей для каждого пикселя кадра."""
    width = WIDTH * SUPERSAMPLE // scale_down
    height = HEIGHT * SUPERSAMPLE // scale_down
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


def _fallback_surface(lat: np.ndarray, lon: np.ndarray,
                      rng: np.random.Generator) -> np.ndarray:
    """Поверхность без мозаики: море и материки по шуму.

    Нужна там, где снимков NASA под рукой нет. Кадр выходит беднее — это
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


def _fallback_lights(surface: np.ndarray, lat: np.ndarray, lon: np.ndarray,
                     rng: np.random.Generator) -> np.ndarray:
    """Огни без ночной мозаики: пятна на суше вдоль побережий.

    Настоящая съёмка огней всегда лучше; это запасной вариант для машины,
    где мозаик нет.
    """
    red, green, blue = surface[..., 0], surface[..., 1], surface[..., 2]
    land = _smoothstep(-0.01, 0.05, green - blue)
    speckle = np.zeros_like(lat)
    amp, freq = 1.0, 9.0
    for _ in range(4):
        phase = rng.uniform(0.0, 2.0 * np.pi, size=2)
        speckle += amp * np.sin(freq * lat + phase[0]) * np.sin(freq * lon + phase[1])
        amp *= 0.6
        freq *= 2.1
    return land * _smoothstep(0.25, 0.95, speckle)


def _stars(shape: tuple[int, int], rng: np.random.Generator) -> np.ndarray:
    """Звёздное поле. Ярких звёзд мало, слабых много — как на небе."""
    height, width = shape
    field = np.zeros((height, width), dtype=np.float32)
    count = int(width * height / 6400)
    ys = rng.integers(0, height, count)
    xs = rng.integers(0, width, count)
    # Показатель степени подобран так, чтобы ярких точек были единицы:
    # равномерная яркость даёт «сыпь», которой на небе не бывает.
    mag = rng.random(count) ** 7.0
    np.maximum.at(field, (ys, xs), mag.astype(np.float32))
    return field


def _load(path: Path | None) -> np.ndarray | None:
    if path is None:
        return None
    with Image.open(path) as raw:
        return np.asarray(raw.convert("RGB"), dtype=np.float32) / 255.0


def render(texture_path: Path | None, night_path: Path | None,
           scale_down: int = 1) -> Image.Image:
    rng = np.random.default_rng(SEED)
    origin, direction, (width, height) = _rays(scale_down)

    texture = _load(texture_path)
    night = _load(night_path)

    distance = _hit_sphere(origin, direction, R_EARTH)
    ground = np.isfinite(distance)

    image = np.zeros((height, width, 3), dtype=np.float32)

    # -- звёзды ------------------------------------------------------------
    stars = _stars((height, width), rng)
    blur = max(0.4, 0.6 * SUPERSAMPLE / scale_down)
    stars = np.asarray(
        Image.fromarray((stars * 255).astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(blur)), dtype=np.float32) / 255.0
    image += (stars * 1.7)[..., None] * np.array([0.86, 0.90, 1.0])

    # -- поверхность -------------------------------------------------------
    if ground.any():
        hit = origin + direction[ground] * distance[ground][..., None]
        normal = hit / np.linalg.norm(hit, axis=-1, keepdims=True)
        lat = np.arcsin(np.clip(normal[..., 2], -1.0, 1.0))
        lon = np.arctan2(normal[..., 1], normal[..., 0])

        surface = (_sample_texture(texture, lat, lon) if texture is not None
                   else _fallback_surface(lat, lon, rng))

        if night is not None:
            lights = _sample_texture(night, lat, lon)
            # Мозаика огней сжата под экран; возвращаем ей размах, иначе
            # города сливаются в ровное оранжевое поле.
            lights = np.clip(lights, 0.0, 1.0) ** 1.55 * 3.1
        else:
            grey = _fallback_lights(surface, lat, lon, rng)
            lights = grey[..., None] * np.array([1.00, 0.72, 0.36]) * 1.6

        # Ночная сторона освещена только небом: слабый холодный свет, чтобы
        # материки и облака не пропадали в чёрном. Яркость дневной мозаики
        # поджимаем: иначе снег и лёд светятся белыми кляксами, каких на
        # ночном снимке не бывает.
        soft = surface / (surface + 0.55)
        ambient = soft * np.array([0.11, 0.14, 0.20]) * 0.20

        # Длина пути луча в воздухе. У точки прямо под камерой луч идёт
        # поперёк слоёв, у кромки — вдоль, и воздуха набегает в десятки раз
        # больше: далёкие огни тускнеют и уходят в дымку.
        view = -direction[ground]
        mu = np.clip(np.sum(normal * view, axis=-1), 0.02, 1.0)
        airmass = np.clip(1.0 / mu, 1.0, 42.0)
        clear = np.exp(-airmass * 0.055)[..., None]
        veil = 1.0 - np.exp(-airmass * 0.030)

        lit = (ambient + lights) * clear
        lit += veil[..., None] * HAZE_RGB * 0.070
        image[ground] = lit

    # -- воздух над кромкой ------------------------------------------------
    # Ближайшее расстояние от центра планеты до луча. Точка наибольшего
    # сближения лежит при t = -(d·o); если она позади камеры, луч уходит от
    # планеты и воздуха не встречает вовсе.
    along = direction @ origin
    miss = np.linalg.norm(origin - direction * along[..., None], axis=-1)
    miss = np.where(along >= 0.0, np.linalg.norm(origin), miss)

    sky = ~ground
    height_km = miss - R_EARTH

    # Свечение воздуха: узкий светящийся слой, поэтому по касательной он
    # виден как чёткая полоса, а выше и ниже гаснет.
    band = np.exp(-0.5 * ((height_km - AIRGLOW_H) / 30.0) ** 2)
    image += (band * sky)[..., None] * AIRGLOW_RGB * 0.30

    # Рассеянный воздух: сине-серая дымка, плотная у самой кромки и уходящая
    # в черноту за две-три сотни километров.
    haze = np.exp(-np.clip(height_km, 0.0, None) / 130.0) * sky
    image += haze[..., None] * HAZE_RGB * 0.24
    far = np.exp(-np.clip(height_km, 0.0, None) / 460.0) * sky
    image += far[..., None] * HAZE_RGB * 0.075

    # -- сведение ----------------------------------------------------------
    image = np.clip(image, 0.0, None)
    # Мягкое сжатие ярких мест: без него огни выгорают в белые кляксы.
    image = image / (1.0 + image * 0.42)
    image = np.clip(image, 0.0, 1.0) ** (1.0 / 1.06)
    # Небольшая добавка насыщенности: сжатие тянет цвета к серому, и тёплые
    # огни на фоне холодной дымки становятся одинаково блёклыми.
    grey = image @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    image = np.clip(grey[..., None] + (image - grey[..., None]) * 1.26, 0.0, 1.0)

    out = Image.fromarray((image * 255.0 + 0.5).astype(np.uint8), mode="RGB")
    target = (WIDTH // scale_down, HEIGHT // scale_down)
    if out.size != target:
        out = out.resize(target, Image.LANCZOS)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", nargs="?",
                        default="src/reportgen/web/static/login-bg.jpg")
    parser.add_argument("--texture", default=None,
                        help="дневная мозаика Земли (NASA Blue Marble)")
    parser.add_argument("--night", default=None,
                        help="ночная мозаика огней (NASA Black Marble)")
    parser.add_argument("--draft", type=int, default=1,
                        help="во сколько раз уменьшить кадр для примерки")
    args = parser.parse_args()

    paths = []
    for name in ("texture", "night"):
        raw = getattr(args, name)
        path = Path(raw) if raw else None
        if path is not None and not path.is_file():
            print(f"мозаика не найдена: {path}")
            return 2
        paths.append(path)

    target = Path(args.target)
    target.parent.mkdir(parents=True, exist_ok=True)
    frame = render(paths[0], paths[1], scale_down=max(1, args.draft))
    frame.save(target, quality=88, optimize=True, progressive=True)
    print(f"{target} — {target.stat().st_size // 1024} КБ, {frame.size[0]}×{frame.size[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
