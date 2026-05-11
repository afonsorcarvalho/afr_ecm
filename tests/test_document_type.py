from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "afr_ecm")
class TestDocumentType(TransactionCase):
    def test_seed_types_exist(self):
        contract = self.env.ref("afr_ecm.doc_type_contract", raise_if_not_found=False)
        self.assertTrue(contract)
        self.assertEqual(contract.default_confidentiality, "restricted")

    def test_metadata_field(self):
        DocType = self.env["afr.ecm.document.type"]
        MField = self.env["afr.ecm.metadata.field"]
        dt = DocType.create({"name": "Tipo Teste", "code": "test_type"})
        f = MField.create({
            "document_type_id": dt.id,
            "name": "vencimento",
            "label": "Vencimento",
            "field_type": "date",
            "required": True,
        })
        self.assertEqual(f.document_type_id, dt)

    def test_code_uniqueness(self):
        DocType = self.env["afr.ecm.document.type"]
        DocType.create({"name": "X1", "code": "uniq_test"})
        with self.assertRaises(Exception):
            DocType.create({"name": "X2", "code": "uniq_test"})
            self.env.cr.flush()
