"""Профили кодирования нисходящих линий КА по открытому коду SatDump (GPL-3): конвейеры resources/pipelines/*.json.
Строится из tablicy/satdump/konveiery.json (skripty/satdump_konv.py). Одна запись на семейство КА (файл конвейеров).
Значения параметров — из конвейера; смысл параметров — из модулей src-core/pipeline/modules/ccsds/*.cpp (умолчания там же).
Семейства, для которых есть первоисточник или своя запись (NOAA, MetOp, Meteor, FengYun-3, GOES, GK2A, JPSS, Inmarsat, Orbcomm),
описаны в e_meteo.py / c_mss.py — здесь пропущены."""
import json, os

KOR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROPUSK = {'Test.json', 'DVB_Test.json', 'Analog.json', 'Work-In-Progress.json', 'NOAA.json', 'MetOp.json', 'Meteor-M.json', 'FengYun-3.json',
           'GOES.json', 'GK2A.json', 'JPSS.json', 'Inmarsat.json', 'Orbcomm.json', 'Seawifs.json'}
MOD = 'kod/SatDump/src-core/pipeline/modules/ccsds/'


def opis(m, p):
    """Параметры модуля → строка по-русски (умолчания — как в модуле)."""
    if m == 'ccsds_conv_concat_decoder':
        s = ['свёрточный K=7 r=%s (многочлены CCSDS 79/109 = 117₈/155₈ в записи SatDump, т. е. 171/133 зеркально)' % p.get('conv_rate', '1/2')]
    elif m == 'ccsds_simple_psk_decoder':
        s = ['без свёрточного кода']
    elif m == 'ccsds_turbo_decoder':
        k = {223: 1784, 446: 3568, 892: 7136, 1115: 8920}.get(p.get('turbo_base'), '?')
        r = p.get('turbo_rate', '?')
        # ASM по скорости — как в module_ccsds_turbo_decoder.cpp и CCSDS 131.0-B-6 п. 9 (исправлено при сверке: ранее для 1/4 ошибочно стоял 96-битный ASM скорости 1/3)
        asm = {'1/2': '034776C7272895B0', '1/3': '25D5C0CE8990F6C9461BF79C', '1/4': '034776C7272895B0FCB88938D8D76A4F',
               '1/6': '25D5C0CE8990F6C9461BF79CDA2A3F31766F0936B9E40863'}.get(r, '?')
        return 'турбокод CCSDS 131.0 k=%s бит, r=%s, ASM %s' % (k, r, asm)
    elif m == 'ccsds_ldpc_decoder':
        r = p.get('ldpc_rate')
        if r == '7/8':
            s = ['LDPC C2 (8160,7136) CCSDS, ASM 1ACFFC1D']
        else:
            s = ['LDPC AR4JA r=%s k=%s, ASM 034776C7272895B0' % (r, p.get('ldpc_block_size', '?'))]
        if p.get('internal_stream'):
            s.append('внутри — поток CADU %s бит, ASM %s' % (p.get('internal_cadu_size'), p.get('internal_asm', '1ACFFC1D')))
        s.append('модуляция %s' % p.get('constellation'))
        return '; '.join(s)
    elif m in ('ax25_decoder',):
        return 'AX.25 (HDLC): NRZI=%s, скремблер G3RUH=%s' % (p.get('nrzi'), p.get('g3ruh'))
    else:
        return 'собственный декодер %s (параметры %s)' % (m, json.dumps(p, ensure_ascii=False))
    s.append('модуляция %s' % p.get('constellation'))
    if p.get('nrzm'):
        s.append('NRZ-M (дифференциальное)')
    ri = p.get('rs_i', 0)
    if ri:
        s.append('РС(255,%s) I=%d%s%s' % ('239' if p.get('rs_type') == 'rs239' else '223', ri, ', двойной базис' if p.get('rs_dualbasis', True) else ', обычный базис',
                                         (', укорочение %d байт' % p['rs_fill_bytes']) if p.get('rs_fill_bytes', -1) > 0 else ''))
    else:
        s.append('без РС')
    s.append('рандомизатор CCSDS 255: %s' % ('да' if p.get('derandomize', True) else 'нет'))
    s.append('CADU %s бит, ASM %s' % (p.get('cadu_size'), p.get('asm', '0x1ACFFC1D')))
    if p.get('ccsds') is False:
        s.append('кадр не CCSDS')
    return '; '.join(s)


def vid_po(mods):
    if 'ccsds_turbo_decoder' in mods:
        return 'турбо PCCC'
    if 'ccsds_ldpc_decoder' in mods:
        return 'LDPC'
    if 'ccsds_conv_concat_decoder' in mods:
        return 'каскадный'
    return 'РС'


def zapisi(S, Z):
    d = json.load(open(os.path.join(KOR, 'tablicy', 'satdump', 'konveiery.json')))
    sem = {}
    for k in d:
        f = k['файл'].split('/')[-1]
        if f in PROPUSK:
            continue
        sem.setdefault(f, []).append(k)
    z = []
    for f, konv in sorted(sem.items()):
        par, ist, mods = {}, [], []
        for k in konv:
            dek = [(st, m) for st, m in k['этапы'].items() if st not in ('baseband', 'soft', 'products') and 'instrument' not in m['модуль']]
            dem = k['этапы'].get('soft', {})
            if not dek:
                continue
            sym = dem.get('параметры', {}).get('symbolrate')
            stroka = []
            if dem:
                stroka.append('демодулятор %s%s' % (dem['модуль'], (', %g симв/с' % sym) if sym else ''))
            for st, m in dek:
                stroka.append(opis(m['модуль'], m['параметры']))
                mods.append(m['модуль'])
            if k['частоты_МГц']:
                stroka.append('частоты: ' + ', '.join('%s %.3f МГц' % (a, b) for a, b in k['частоты_МГц']))
            par['%s (%s)' % (k['конвейер'], k['название'])] = '; '.join(stroka)
            ist.append(S('kod/SatDump/' + k['файл'], r'"%s"' % k['конвейер']))
        if not par:
            continue
        for mm in sorted(set(mods)):
            if mm.startswith('ccsds_'):
                ist.append(S(MOD + 'module_%s.cpp' % mm, r'parameters\['))
        semeistvo = f[:-5]
        z.append(Z('Профиль кодирования нисходящей линии: %s (по SatDump)' % semeistvo, 'КА: %s' % semeistvo, vid_po(mods),
                   dict(par, **{'таблица': 'tablicy/satdump/konveiery.json'}),
                   'нисходящие линии КА семейства %s (телеметрия/целевая информация)' % semeistvo, ist,
                   'открытый код SatDump (GPL-3) — рабочие конвейеры приёма; первоисточник (ICD КА) не публикуется или не найден; коды — стандартные CCSDS 131.0 (сверены в a_ccsds)',
                   'ЧАСТИЧНО: CCSDS свёрточный/РС/рандомизатор/ASM в проекте есть (ccsds.py, svyortka.py, rs_bch.py); турбо CCSDS и LDPC AR4JA/C2 — см. записи CCSDS 131.0-B'))
    return z
