#
# SPDX-FileCopyrightText: Copyright (c) 2021-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
"""Blocks for encoding of linear codes."""

from typing import Optional

import numpy as np
import torch

from sionna.phy import Block
from sionna.phy.fec.coding import pcm2gm
from sionna.phy.fec.utils import int_mod_2

__all__ = ["LinearEncoder"]


class LinearEncoder(Block):
    r"""Linear binary encoder for a given generator or parity-check matrix.

    If ``is_pcm`` is `True`, ``enc_mat`` is interpreted as parity-check
    matrix and internally converted to a corresponding generator matrix.

    :param enc_mat: Binary generator matrix of shape `[k, n]`. If ``is_pcm`` is
        `True`, ``enc_mat`` is interpreted as parity-check matrix of shape
        `[n-k, n]`.
    :param is_pcm: If `True`, the ``enc_mat`` is interpreted as parity-check
        matrix instead of a generator matrix.
    :param precision: Precision used for internal calculations and outputs.
        If `None`, :attr:`~sionna.phy.config.Config.precision` is used.
    :param device: Device for computation (e.g., 'cpu', 'cuda:0').
        If `None`, :attr:`~sionna.phy.config.Config.device` is used.

    :input info_bits: [..., k], `torch.float` or `torch.int`.
        Binary tensor containing the information bits.

    :output c: [..., n], same dtype as ``info_bits``.
        Binary tensor containing codewords with same shape as inputs,
        except the last dimension changes to `[..., n]`.

    .. rubric:: Notes

    If ``is_pcm`` is `True`, this block uses
    :func:`~sionna.phy.fec.utils.pcm2gm` to find the generator matrix for
    encoding. Please note that this imposes a few constraints on the
    provided parity-check matrix such as full rank and it must be binary.

    Note that this encoder is generic for all binary linear block codes
    and, thus, cannot implement any code specific optimizations. As a
    result, the encoding complexity is :math:`O(k^2)`. Please consider code
    specific encoders such as the
    :class:`~sionna.phy.fec.polar.encoding.Polar5GEncoder` or
    :class:`~sionna.phy.fec.ldpc.encoding.LDPC5GEncoder` for an improved
    encoding performance.

    .. rubric:: Examples


    .. code-block:: python

        import torch
        from sionna.phy.fec.utils import load_parity_check_examples
        from sionna.phy.fec.linear import LinearEncoder

        # Load (7,4) Hamming code
        pcm, k, n, _ = load_parity_check_examples(0)
        encoder = LinearEncoder(pcm, is_pcm=True)

        # Generate random information bits
        u = torch.randint(0, 2, (10, k), dtype=torch.float32)
        c = encoder(u)
        print(c.shape)
        # torch.Size([10, 7])
    """

    def __init__(
        self,
        enc_mat: np.ndarray,
        *,
        is_pcm: bool = False,
        precision: Optional[str] = None,
        device: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(precision=precision, device=device, **kwargs)

        # Check input values for consistency
        if not isinstance(is_pcm, bool):
            raise TypeError("is_pcm must be bool.")

        # Verify that enc_mat is binary
        if not ((enc_mat == 0) | (enc_mat == 1)).all():
            raise ValueError("enc_mat is not binary.")
        if len(enc_mat.shape) != 2:
            raise ValueError("enc_mat must be 2-D array.")

        # In case parity-check matrix is provided, convert to generator matrix
        if is_pcm:
            gm = pcm2gm(enc_mat, verify_results=True)
        else:
            gm = enc_mat

        # Register as buffer for CUDAGraph compatibility
        self.register_buffer("_gm", torch.tensor(gm, dtype=self.dtype, device=self.device))

        self._k = self._gm.shape[0]
        self._n = self._gm.shape[1]
        self._coderate = self._k / self._n

        if self._k > self._n:
            raise ValueError("Invalid matrix dimensions.")

    @property
    def k(self) -> int:
        """Number of information bits per codeword."""
        return self._k

    @property
    def n(self) -> int:
        """Codeword length."""
        return self._n

    @property
    def gm(self) -> torch.Tensor:
        """Generator matrix used for encoding."""
        return self._gm

    @property
    def coderate(self) -> float:
        """Coderate of the code."""
        return self._coderate

    def build(self, input_shape: tuple) -> None:
        """Check for valid input shapes."""
        if input_shape[-1] != self._k:
            raise ValueError(f"Last dimension must be of size k={self._k}.")

    def call(self, bits: torch.Tensor, /) -> torch.Tensor:
        """Generic encoding function based on generator matrix multiplication."""
        # Validate input shape
        if bits.shape[-1] != self._k:
            raise ValueError(f"Last dimension must be of size k={self._k}.")

        # Add batch_dim if not provided (will be removed afterwards)
        no_batch_dim = bits.dim() == 1
        if no_batch_dim:
            bits = bits.unsqueeze(0)

        # Encode via matrix multiplication: c = u * G
        c = torch.matmul(bits.to(self._gm.dtype), self._gm)
        c = int_mod_2(c)

        # Cast back to input dtype
        c = c.to(bits.dtype)

        # Remove batch_dim if not desired
        if no_batch_dim:
            c = c.squeeze(0)

        return c

