#
# SPDX-FileCopyrightText: Copyright (c) 2021-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
"""Sionna PHY Forward Error Correction (FEC) Package."""

from . import ldpc
from . import linear
from . import polar
from . import conv
from . import turbo
from .linear import LinearEncoder, OSDecoder
from .interleaving import (
    RowColumnInterleaver,
    RandomInterleaver,
    Turbo3GPPInterleaver,
    Deinterleaver,
)
from .turbo import TurboEncoder, TurboDecoder
from .ldpc import (
    LDPC5GEncoder,
    LDPCBPDecoder,
    LDPC5GDecoder,
)
from .polar import (
    PolarEncoder,
    Polar5GEncoder,
    PolarSCDecoder,
    PolarSCLDecoder,
    PolarBPDecoder,
    Polar5GDecoder,
    generate_5g_ranking,
    generate_polar_transform_mat,
    generate_rm_code,
    generate_dense_polar,
)
from .conv import (
    ConvEncoder,
    ViterbiDecoder,
    BCJRDecoder,
    polynomial_selector,
    Trellis,
)
from .crc import CRCEncoder, CRCDecoder
from .scrambling import Scrambler, TB5GScrambler, Descrambler
from .utils import (
    GaussianPriorSource,
    llr2mi,
    j_fun,
    j_fun_inv,
    bin2int,
    int2bin,
    int_mod_2,
)
from .plotting import (
    plot_trajectory,
    plot_exit_chart,
    get_exit_analytic,
)
from .coding import (
    load_parity_check_examples,
    alist2mat,
    load_alist,
    make_systematic,
    gm2pcm,
    pcm2gm,
    verify_gm_pcm,
    generate_reg_ldpc,
)

__all__ = [
    "conv",
    "ldpc",
    "linear",
    "polar",
    "turbo",
    "ConvEncoder",
    "ViterbiDecoder",
    "BCJRDecoder",
    "polynomial_selector",
    "Trellis",
    "LinearEncoder",
    "OSDecoder",
    "LDPC5GEncoder",
    "LDPCBPDecoder",
    "LDPC5GDecoder",
    "PolarEncoder",
    "Polar5GEncoder",
    "PolarSCDecoder",
    "PolarSCLDecoder",
    "PolarBPDecoder",
    "Polar5GDecoder",
    "generate_5g_ranking",
    "generate_polar_transform_mat",
    "generate_rm_code",
    "generate_dense_polar",
    "CRCEncoder",
    "CRCDecoder",
    "Scrambler",
    "TB5GScrambler",
    "Descrambler",
    "GaussianPriorSource",
    "llr2mi",
    "j_fun",
    "j_fun_inv",
    "plot_trajectory",
    "plot_exit_chart",
    "get_exit_analytic",
    "load_parity_check_examples",
    "bin2int",
    "int2bin",
    "alist2mat",
    "load_alist",
    "make_systematic",
    "gm2pcm",
    "pcm2gm",
    "verify_gm_pcm",
    "generate_reg_ldpc",
    "int_mod_2",
    "TurboEncoder",
    "TurboDecoder",
    "RowColumnInterleaver",
    "RandomInterleaver",
    "Turbo3GPPInterleaver",
    "Deinterleaver",
]

