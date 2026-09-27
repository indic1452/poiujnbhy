"""RANAP (3GPP TS 25.413) поверх SCCP: PDU в APER (X.691) — вид, процедура, важность, IE; NAS-PDU (IE 16) —
сообщение 24.008 из тестов GSM."""

import unittest

import _bootstrap  # noqa: F401
from reportgen.setevoy.protokoly import mobilnye, oks7, ranap
from reportgen.setevoy.razbor import ДОП_УРОВНИ
from test_setevoy_abis import LU
from test_setevoy_bssap import по_cr, по_dt1, по_udt
from test_setevoy_mobilnye import aper_pdu, aper_длина

INITIAL_UE = aper_pdu(0, 19, 1, [(3, 0, b"\x00"), (16, 1, aper_длина(len(LU)) + LU)])


class ТестRANAP(unittest.TestCase):
    def test_initial_ue_в_cr(self):
        п = по_cr(INITIAL_UE, ssn=142)
        self.assertEqual(п.стек[:3], ["SCCP", "RANAP", "GSM MM"])
        у = п.уровни[1]
        self.assertEqual(у.итог, "InitialUE-Message (initiatingMessage)")
        self.assertTrue(п.инфо.startswith("RANAP InitialUE-Message (initiatingMessage); NAS: Location Updating Request"),
                        п.инфо)
        мм = [x for x in п.уровни if x.протокол == "GSM MM"][0]
        self.assertEqual(п.данные[мм.смещение:мм.смещение + len(LU)], LU)

    def test_direct_transfer_в_dt1_и_reset_в_udt(self):
        п = по_dt1(aper_pdu(0, 20, 1, [(16, 1, aper_длина(len(LU)) + LU)]))
        self.assertEqual(п.стек[:3], ["SCCP", "RANAP", "GSM MM"])
        self.assertEqual(п.уровни[1].итог, "DirectTransfer (initiatingMessage)")
        п = по_udt(aper_pdu(0, 9, 0, [(4, 0, b"\x00")]), ssn=142)
        self.assertEqual(п.стек, ["SCCP", "RANAP"])
        self.assertEqual(п.инфо, "RANAP Reset (initiatingMessage)")

    def test_вариант_outcome(self):
        п = по_dt1(aper_pdu(3, 0, 1, [(4, 0, b"\x00")]))
        self.assertEqual(п.уровни[1].итог, "RAB-Assignment (outcome)")
        # Чужой SSN в CR — RANAP не пробуется (подсистема задана адресом).
        self.assertNotIn("RANAP", по_cr(INITIAL_UE).стек)

    def test_nas_pdu_чужой(self):
        pdu = aper_pdu(0, 20, 1, [(16, 1, aper_длина(len(LU) + 1) + LU)])
        п = по_dt1(pdu)
        self.assertEqual(п.стек, ["SCCP", "RANAP"])
        pdu = aper_pdu(0, 20, 1, [(16, 1, aper_длина(3) + b"\x05\xff\x00")])
        п = по_dt1(pdu)
        self.assertEqual(п.стек, ["SCCP", "RANAP"])
        self.assertEqual(п.инфо, "RANAP DirectTransfer (initiatingMessage)")
        self.assertIsNone(ranap._nas_pdu(None, 3, 3))

    def test_регистрация(self):
        self.assertIs(oks7.SCCP_ПОДСИСТЕМЫ[142], ranap.ranap)
        self.assertIn(ranap.ranap, oks7.SCCP_СОЕДИНЕНИЯ)
        self.assertIn(("RANAP", 16), mobilnye.APER_IE_РАЗБОР)
        self.assertEqual(ДОП_УРОВНИ["RANAP"], "прикладной")
        self.assertEqual(len(ranap.RANAP_ПРОЦЕДУРЫ), 50)


if __name__ == "__main__":
    unittest.main()


class ТестВариантов(unittest.TestCase):
    def test_вариант_5_не_ranap(self):
        # APER CHOICE RANAP-PDU: 4 варианта (0–3); индекс 4 — не RANAP.
        self.assertNotIn("RANAP", по_dt1(aper_pdu(4, 0, 1, [(4, 0, b"\x00")])).стек)
        self.assertIn("RANAP", по_dt1(aper_pdu(2, 0, 1, [(4, 0, b"\x00")])).стек)
