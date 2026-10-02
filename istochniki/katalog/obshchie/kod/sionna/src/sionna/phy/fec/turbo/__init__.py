#
# SPDX-FileCopyrightText: Copyright (c) 2021-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
"""Turbo code module of Sionna PHY."""

from .encoding import TurboEncoder
from .decoding import TurboDecoder
from .utils import TurboTermination, puncture_pattern, polynomial_selector
from . import coeffs

__all__ = [
    "TurboEncoder",
    "TurboDecoder",
    "TurboTermination",
    "puncture_pattern",
    "polynomial_selector",
    "coeffs",
]



