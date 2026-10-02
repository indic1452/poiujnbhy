#
#  PocketSDR Python Library - Forward Error Correction (FEC) Functions
#
#  References:
#  [1] CCSDS 131.0-B-3, TM Synchronization and Channel Coding, September 2017
#
#  Author:
#  T.TAKASU
#
#  History:
#  2021-12-24  1.0  new
#  2022-01-04  1.1  fix bug to call set_viterbi27_polynomial() by python 3.8
#  2026-08-07  1.2  use the decoders of the Pocket SDR library (sdr_fec.c),
#                   which replaced the LIBFEC dependency
#
import os, platform
from ctypes import *
import numpy as np
import sdr_func

# constants --------------------------------------------------------------------
POLY_CONV = (0x4F, 0x6D)  # convolution code polynomials (G1, G2)
NONE = np.array([], dtype='uint8')

RS_N, RS_K, RS_NROOTS = 255, 223, 32 # CCSDS RS(255,223) code parameters
RS_A0, RS_GF_POLY, RS_FCR, RS_PRIM = 255, 0x187, 112, 11
RS_TAL_BASIS = (0x8D, 0xEF, 0xEC, 0x86, 0xFA, 0x99, 0xAF, 0x7B)
RS_TBL = None             # CCSDS RS tables cache

# load Pocket SDR library ------------------------------------------------------
env = platform.platform()
dir = os.path.dirname(__file__)
if 'Windows' in env:
    lib = dir + '/../lib/win32/libsdr.so'
elif 'macOS' in env:
    lib = dir + '/../lib/macos/libsdr.so'
else: # linux or Raspberry Pi OS
    lib = dir + '/../lib/linux/libsdr.so'
try:
    libsdr = cdll.LoadLibrary(lib)
except:
    print('libsdr load error: ' + lib)
    exit(-1)

libsdr.sdr_decode_conv.argtypes = (POINTER(c_uint8), c_int32, POINTER(c_uint8))
libsdr.sdr_decode_rs.argtypes = (POINTER(c_uint8),)

#-------------------------------------------------------------------------------
#  Encode convolution code (K=7, R=1/2, Poly=G1:0x4F,G2:0x6D).
#
#  args:
#      data     (I) Data as uint8 ndarray (0 or 1).
#
#  returns:
#      enc_data Encoded data as uint8 ndarray (0 or 1).
#               (len(enc_data) = (len(data) + 6) * 2)
#
def encode_conv(data):
    N = len(data)
    
    if N <= 0 or data.dtype != 'uint8':
        print('encode_conv: data length or type error')
        return NONE
    
    enc_data = np.zeros((N + 6) * 2, dtype='uint8')
    R = 0
    for i in range(N + 6):
        R = (R << 1) + ((data[i] & 1) if i < N else 0)
        enc_data[i*2+0] = sdr_func.xor_bits(R & POLY_CONV[0])
        enc_data[i*2+1] = sdr_func.xor_bits(R & POLY_CONV[1])
    
    return enc_data

#-------------------------------------------------------------------------------
#  Decode convolution code (K=7, R=1/2, Poly=G1:0x4F,G2:0x6D).
#
#  args:
#      data     (I) Data as uint8 ndarray (0 to 255 for soft-decision).
#
#  returns:
#      dec_data Decoded data as uint8 ndarray (0 or 1).
#               (len(dec_data) = len(data) / 2 - 6)
#
def decode_conv(data):
    N = len(data) // 2 - 6
    
    if N <= 0 or data.dtype != 'uint8':
        print('decode_conv: data length or type error')
        return NONE

    data = np.ascontiguousarray(data, dtype='uint8')
    dec_data = np.zeros(N, dtype='uint8')
    p = data.ctypes.data_as(POINTER(c_uint8))
    q = dec_data.ctypes.data_as(POINTER(c_uint8))

    # decode convolution code by Viterbi decoder
    libsdr.sdr_decode_conv(p, len(data), q)

    return dec_data

#-------------------------------------------------------------------------------
#  Generate the CCSDS RS(255,223) tables ([1]). The Pocket SDR library exports
#  the decoder only, so the encoder is kept here for encode_rs().
#
#  returns:
#      alpha_to, index_of, genpoly, tal, tal1
#
def rs_tables():
    global RS_TBL
    if RS_TBL:
        return RS_TBL

    alpha_to = [0] * (RS_N + 1)
    index_of = [0] * (RS_N + 1)
    index_of[0] = RS_A0
    alpha_to[RS_A0] = 0
    sr = 1
    for i in range(RS_N):
        index_of[sr] = i
        alpha_to[i] = sr
        sr <<= 1
        if sr & 0x100:
            sr ^= RS_GF_POLY
        sr &= RS_N

    # conventional to CCSDS dual basis and its inverse
    tal, tal1 = [0] * 256, [0] * 256
    for x in range(256):
        y = 0
        for j in range(8):
            for k in range(8):
                if x & (1 << k):
                    y ^= RS_TAL_BASIS[7-k] & (1 << j)
        tal[x] = y
        tal1[y] = x

    genpoly = [0] * (RS_NROOTS + 1)
    genpoly[0] = 1
    root = RS_FCR * RS_PRIM
    for i in range(RS_NROOTS):
        genpoly[i+1] = 1
        for j in range(i, 0, -1):
            if genpoly[j]:
                genpoly[j] = genpoly[j-1] ^ \
                    alpha_to[(index_of[genpoly[j]] + root) % RS_N]
            else:
                genpoly[j] = genpoly[j-1]
        genpoly[0] = alpha_to[(index_of[genpoly[0]] + root) % RS_N]
        root += RS_PRIM
    genpoly = [index_of[g] for g in genpoly]

    RS_TBL = (alpha_to, index_of, genpoly, tal, tal1)
    return RS_TBL

#-------------------------------------------------------------------------------
#  Encode Reed-Solomon RS(255,223) code.
#
#  args:
#      syms     (IO) Data symbols as uint8 ndarray (length = 255).
#                    syms[0:223] should be set by input data. syms[223:255] are
#                    set by RS parity before returning the function.
#
#  returns:
#      None
#
def encode_rs(syms):
    if len(syms) < 255 or syms.dtype != 'uint8':
        print('encode_rs: data length or type error')
        return

    alpha_to, index_of, genpoly, tal, tal1 = rs_tables()
    parity = [0] * RS_NROOTS

    # encode RS-CCSDS
    for i in range(RS_K):
        feedback = index_of[tal1[syms[i]] ^ parity[0]]
        if feedback != RS_A0:
            for j in range(1, RS_NROOTS):
                parity[j] ^= alpha_to[(feedback + genpoly[RS_NROOTS-j]) % RS_N]
        parity = parity[1:] + [0 if feedback == RS_A0 else
            alpha_to[(feedback + genpoly[0]) % RS_N]]

    syms[223:] = [tal[p] for p in parity]

#-------------------------------------------------------------------------------
#  Decode Reed-Solomon RS(255,223) code.
#
#  args:
#      syms     (IO) RS-encoded data symbols as uint8 ndarray (length = 255).
#                    Symbol errors are corrected before returning the function.
#
#  returns:
#      nerr     Number of error bits corrected. (-1: too many erros)
#
def decode_rs(syms):
    if len(syms) < 255 or syms.dtype != 'uint8':
        print('decode_rs: data length or type error')
        return -1
    
    p = syms.ctypes.data_as(POINTER(c_uint8))

    # decode RS-CCSDS
    return libsdr.sdr_decode_rs(p)

