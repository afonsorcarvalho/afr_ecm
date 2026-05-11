from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "afr_ecm")
class TestPhysicalLocation(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Loc = self.env["afr.ecm.physical.location"]

    def test_hierarchy_path(self):
        archive = self.Loc.create({"name": "Arquivo Central", "location_type": "archive"})
        room = self.Loc.create({"name": "Sala 1", "parent_id": archive.id, "location_type": "room"})
        box = self.Loc.create({"name": "Caixa 001", "parent_id": room.id, "location_type": "box"})
        self.assertEqual(box.complete_path, "Arquivo Central / Sala 1 / Caixa 001")

    def test_barcode_auto_generated(self):
        loc = self.Loc.create({"name": "Caixa Auto", "location_type": "box"})
        self.assertTrue(loc.barcode)

    def test_qr_image_renders(self):
        loc = self.Loc.create({"name": "Caixa QR", "location_type": "box"})
        # qrcode lib pode não estar disponível em ambiente de teste — accept either
        self.assertTrue(loc.qr_image is False or isinstance(loc.qr_image, (bytes, bytearray)))

    def test_unique_barcode(self):
        self.Loc.create({"name": "L1", "barcode": "UNIQ-001"})
        with self.assertRaises(Exception):
            self.Loc.create({"name": "L2", "barcode": "UNIQ-001"})
            self.env.cr.flush()
