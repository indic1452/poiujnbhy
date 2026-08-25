"""Добавляет src/ в путь импорта: пакет не устанавливается, тесты гоняются из репозитория."""

import os
import sys
from pathlib import Path

# Распознавание вложенных картинок втрое удлиняет прогон: тесты его включают
# точечно, там где проверяют именно его.
os.environ.setdefault("REPORTGEN_OCR_EMBEDDED", "0")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
