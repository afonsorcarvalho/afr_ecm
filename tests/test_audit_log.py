from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "afr_ecm")
class TestAuditLog(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Log = self.env["afr.ecm.audit.log"]
        self.Location = self.env["afr.ecm.physical.location"]

    def _count_logs(self, model, res_id, event):
        return self.Log.search_count([
            ("model", "=", model),
            ("res_id", "=", res_id),
            ("event_type", "=", event),
        ])

    def test_create_logs_event(self):
        loc = self.Location.create({"name": "Sala Teste", "location_type": "room"})
        self.assertEqual(self._count_logs(loc._name, loc.id, "create"), 1)

    def test_write_logs_event(self):
        loc = self.Location.create({"name": "Sala A", "location_type": "room"})
        loc.write({"name": "Sala A renomeada"})
        self.assertGreaterEqual(self._count_logs(loc._name, loc.id, "write"), 1)

    def test_unlink_logs_event(self):
        loc = self.Location.create({"name": "Sala B", "location_type": "room"})
        rid = loc.id
        loc.unlink()
        self.assertEqual(self._count_logs("afr.ecm.physical.location", rid, "unlink"), 1)

    def test_log_helper(self):
        loc = self.Location.create({"name": "Sala C", "location_type": "room"})
        self.Log.log("view", loc)
        self.assertEqual(self._count_logs(loc._name, loc.id, "view"), 1)
