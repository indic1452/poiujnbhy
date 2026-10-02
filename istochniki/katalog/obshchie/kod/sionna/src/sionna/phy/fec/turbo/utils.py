#
# SPDX-FileCopyrightText: Copyright (c) 2021-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
"""Utility functions and classes for Turbo codes."""

import math
from typing import Optional

import torch

from sionna.phy.block import Object

__all__ = ["polynomial_selector", "puncture_pattern", "TurboTermination"]


def polynomial_selector(constraint_length: int) -> tuple[str, str]:
    r"""Returns the generator polynomials for rate-1/2 convolutional codes
    for a given ``constraint_length``.

    :param constraint_length: An integer defining the desired constraint length
        of the encoder. The memory of the encoder is ``constraint_length`` - 1.

    :output gen_poly: Tuple of strings with each string being a 0,1 sequence
        where each polynomial is represented in binary form.

    .. rubric:: Notes

    Please note that the polynomials are optimized for RSC codes and are
    not necessarily the same as used in the polynomial selector
    :func:`~sionna.phy.fec.conv.utils.polynomial_selector` of the
    convolutional codes.

    .. rubric:: Examples


    .. code-block:: python

        from sionna.phy.fec.turbo import polynomial_selector

        gen_poly = polynomial_selector(4)
        print(gen_poly)
        # ('1011', '1101')
    """
    if not isinstance(constraint_length, int):
        raise TypeError("constraint_length must be int.")
    if not 2 < constraint_length < 7:
        raise ValueError("Unsupported constraint_length.")

    gen_poly_dict = {
        3: ("111", "101"),  # (7, 5)
        4: ("1011", "1101"),  # (13, 15)
        5: ("10011", "11011"),  # (23, 33)
        6: ("111101", "101011"),  # (75, 53)
    }
    gen_poly = gen_poly_dict[constraint_length]
    return gen_poly


def puncture_pattern(
    turbo_coderate: float,
    conv_coderate: float,
    device: Optional[str] = None,
) -> torch.Tensor:
    r"""Returns puncturing pattern such that the Turbo code has rate
    ``turbo_coderate`` given the underlying convolutional encoder is of rate
    ``conv_coderate``.

    :param turbo_coderate: Desired coderate of the Turbo code.
    :param conv_coderate: Coderate of the underlying convolutional encoder.
        Currently, only rate=0.5 is supported.
    :param device: Device for the output tensor.

    :output turbo_punct_pattern: 2D boolean tensor indicating the positions
        to be punctured.

    .. rubric:: Examples


    .. code-block:: python

        from sionna.phy.fec.turbo import puncture_pattern

        pattern = puncture_pattern(0.5, 0.5)
        print(pattern)
        # tensor([[ True,  True, False],
        #         [ True, False,  True]])
    """
    if conv_coderate != 0.5:
        raise ValueError("Only conv_coderate=0.5 is supported.")

    if turbo_coderate == 0.5:
        pattern = torch.tensor([[1, 1, 0], [1, 0, 1]], dtype=torch.int32, device=device)
    elif turbo_coderate == 1 / 3:
        pattern = torch.tensor([[1, 1, 1]], dtype=torch.int32, device=device)
    else:
        raise NotImplementedError("turbo_coderate not supported.")

    turbo_punct_pattern = pattern.bool()
    return turbo_punct_pattern


class TurboTermination(Object):
    r"""Termination object, handles the transformation of termination bits from
    the convolutional encoders to a Turbo codeword.

    Similarly, it handles the transformation of channel symbols corresponding
    to the termination of a Turbo codeword to the underlying convolutional
    codewords.

    :param constraint_length: Constraint length of the convolutional encoder
        used in the Turbo code. Note that the memory of the encoder is
        ``constraint_length`` - 1.
    :param conv_n: Number of output bits for one state transition in the
        underlying convolutional encoder.
    :param num_conv_encs: Number of parallel convolutional encoders used in
        the Turbo code.
    :param num_bitstreams: Number of output bit streams from Turbo code.
    :param precision: Precision used for internal calculations and outputs.
        If `None`, :attr:`~sionna.phy.config.Config.precision` is used.
    :param device: Device for computation (e.g., 'cpu', 'cuda:0').
        If `None`, :attr:`~sionna.phy.config.Config.device` is used.

    .. rubric:: Examples


    .. code-block:: python

        from sionna.phy.fec.turbo import TurboTermination

        term = TurboTermination(constraint_length=4, conv_n=2)
        num_term_syms = term.get_num_term_syms()
        print(num_term_syms)
        # 4
    """

    def __init__(
        self,
        constraint_length: int,
        conv_n: int = 2,
        num_conv_encs: int = 2,
        num_bitstreams: int = 3,
        precision: Optional[str] = None,
        device: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(precision=precision, device=device, **kwargs)

        # Ensure all parameters are int
        constraint_length = int(constraint_length)
        conv_n = int(conv_n)
        num_conv_encs = int(num_conv_encs)
        num_bitstreams = int(num_bitstreams)

        self._mu = constraint_length - 1
        self._conv_n = conv_n
        if num_conv_encs != 2:
            raise NotImplementedError("Only num_conv_encs=2 supported.")
        self._num_conv_encs = num_conv_encs
        self._num_bitstreams = num_bitstreams

    @property
    def mu(self) -> int:
        """Memory of the underlying convolutional encoder."""
        return self._mu

    @property
    def conv_n(self) -> int:
        """Number of output bits per state transition."""
        return self._conv_n

    @property
    def num_conv_encs(self) -> int:
        """Number of parallel convolutional encoders."""
        return self._num_conv_encs

    @property
    def num_bitstreams(self) -> int:
        """Number of output bit streams."""
        return self._num_bitstreams

    def get_num_term_syms(self) -> int:
        r"""Computes the number of termination symbols for the Turbo code
        based on the underlying convolutional code parameters, primarily the
        memory :math:`\mu`.

        Note that it is assumed that one Turbo symbol implies
        ``num_bitstreams`` bits.

        :output turbo_term_syms: Total number of termination symbols for the
            Turbo Code. One symbol equals ``num_bitstreams`` bits.

        .. rubric:: Examples


        .. code-block:: python

            from sionna.phy.fec.turbo import TurboTermination

            term = TurboTermination(constraint_length=4, conv_n=2)
            num_term_syms = term.get_num_term_syms()
            print(num_term_syms)
            # 4
        """
        total_term_bits = self._conv_n * self._num_conv_encs * self._mu
        turbo_term_syms = math.ceil(total_term_bits / self._num_bitstreams)
        return turbo_term_syms

    def termbits_conv2turbo(
        self, term_bits1: torch.Tensor, term_bits2: torch.Tensor
    ) -> torch.Tensor:
        r"""Merges termination bit streams from the two convolutional encoders
        to a bit stream corresponding to the Turbo codeword.

        Let ``term_bits1`` and ``term_bits2`` be:

        :math:`[x_1(K), z_1(K), x_1(K+1), z_1(K+1),..., x_1(K+\mu-1),z_1(K+\mu-1)]`

        :math:`[x_2(K), z_2(K), x_2(K+1), z_2(K+1),..., x_2(K+\mu-1), z_2(K+\mu-1)]`

        where :math:`x_i, z_i` are the systematic and parity bit streams
        respectively for a rate-1/2 convolutional encoder i, for i = 1, 2.

        In the example output below, we assume :math:`\mu=4` to demonstrate zero
        padding at the end. Zero padding is done such that the total length is
        divisible by ``num_bitstreams`` (defaults to 3) which is the number of
        Turbo bit streams.

        Assume ``num_bitstreams`` = 3. Then number of termination symbols for
        the TurboEncoder is :math:`\lceil \frac{2 \cdot conv\_n \cdot \mu}{3} \rceil`:

        :math:`[x_1(K), z_1(K), x_1(K+1)]`

        :math:`[z_1(K+1), x_1(K+2), z_1(K+2)]`

        :math:`[x_1(K+3), z_1(K+3), x_2(K)]`

        :math:`[z_2(K), x_2(K+1), z_2(K+1)]`

        :math:`[x_2(K+2), z_2(K+2), x_2(K+3)]`

        :math:`[z_2(K+3), 0, 0]`

        Therefore, the output from this method is a single dimension vector
        where all Turbo symbols are concatenated together.

        :math:`[x_1(K), z_1(K), x_1(K+1), z_1(K+1), x_1(K+2), z_1(K+2), x_1(K+3),`

        :math:`z_1(K+3), x_2(K), z_2(K), x_2(K+1), z_2(K+1), x_2(K+2), z_2(K+2),`

        :math:`x_2(K+3), z_2(K+3), 0, 0]`

        :param term_bits1: 2+D tensor containing termination bits from
            convolutional encoder 1.
        :param term_bits2: 2+D tensor containing termination bits from
            convolutional encoder 2.

        :output term_bits: Tensor of termination bits. The output is obtained
            by concatenating the inputs and then adding right zero-padding if
            needed.

        .. rubric:: Examples


        .. code-block:: python

            import torch
            from sionna.phy.fec.turbo import TurboTermination

            term = TurboTermination(constraint_length=4, conv_n=2)
            term_bits1 = torch.randint(0, 2, (10, 6), dtype=torch.int32)
            term_bits2 = torch.randint(0, 2, (10, 6), dtype=torch.int32)
            result = term.termbits_conv2turbo(term_bits1, term_bits2)
            print(result.shape)
            # torch.Size([10, 12])
        """
        term_bits = torch.cat([term_bits1, term_bits2], dim=-1)

        num_term_bits = term_bits.shape[-1]
        num_term_syms = math.ceil(num_term_bits / self._num_bitstreams)

        extra_bits = self._num_bitstreams * num_term_syms - num_term_bits
        if extra_bits > 0:
            batch_shape = term_bits.shape[:-1]
            zeros = torch.zeros(
                *batch_shape,
                extra_bits,
                dtype=term_bits.dtype,
                device=term_bits.device,
            )
            term_bits = torch.cat([term_bits, zeros], dim=-1)

        return term_bits

    def term_bits_turbo2conv(
        self, term_bits: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor]:
        r"""Splits the termination symbols from a Turbo codeword to the
        termination symbols corresponding to the two convolutional encoders,
        respectively.

        Let's assume :math:`\mu=4` and the underlying convolutional encoders
        are systematic and rate-1/2, for demonstration purposes.

        Let ``term_bits`` tensor, corresponding to the termination symbols of
        the Turbo codeword be as following:

        :math:`y = [x_1(K), z_1(K), x_1(K+1), z_1(K+1), x_1(K+2), z_1(K+2),`
        :math:`x_1(K+3), z_1(K+3), x_2(K), z_2(K), x_2(K+1), z_2(K+1),`
        :math:`x_2(K+2), z_2(K+2), x_2(K+3), z_2(K+3), 0, 0]`

        The two termination tensors corresponding to the convolutional encoders
        are:
        :math:`y[0,..., 2\mu]`, :math:`y[2\mu,..., 4\mu]`. The output from this
        method is a tuple of two tensors, each of
        size :math:`2\mu` and shape :math:`[\mu,2]`.

        :math:`[[x_1(K), z_1(K)],`

        :math:`[x_1(K+1), z_1(K+1)],`

        :math:`[x_1(K+2), z_1(K+2)],`

        :math:`[x_1(K+3), z_1(K+3)]]`

        and

        :math:`[[x_2(K), z_2(K)],`

        :math:`[x_2(K+1), z_2(K+1)],`

        :math:`[x_2(K+2), z_2(K+2)],`

        :math:`[x_2(K+3), z_2(K+3)]]`

        :param term_bits: Channel output of the Turbo codeword, corresponding
            to the termination part.

        :output term_bits1: Channel output corresponding to encoder 1.

        :output term_bits2: Channel output corresponding to encoder 2.

        .. rubric:: Examples


        .. code-block:: python

            import torch
            from sionna.phy.fec.turbo import TurboTermination

            term = TurboTermination(constraint_length=4, conv_n=2)
            term_bits = torch.randn(10, 12)
            term_bits1, term_bits2 = term.term_bits_turbo2conv(term_bits)
            print(term_bits1.shape, term_bits2.shape)
            # torch.Size([10, 6]) torch.Size([10, 6])
        """
        input_len = term_bits.shape[-1]
        divisible = input_len % self._num_bitstreams
        if divisible != 0:
            raise ValueError("Programming Error: input_len not divisible.")

        enc1_term_len = self._conv_n * self._mu
        enc2_term_len = self._conv_n * self._mu

        term_bits1 = term_bits[..., :enc1_term_len]
        term_bits2 = term_bits[..., enc1_term_len : enc1_term_len + enc2_term_len]

        return term_bits1, term_bits2



