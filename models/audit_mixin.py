from odoo import api, models


class AfrEcmAuditMixin(models.AbstractModel):
    _name = "afr.ecm.audit.mixin"
    _description = "ECM Audit Mixin"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        Log = self.env["afr.ecm.audit.log"].sudo()
        for rec in records:
            Log.log("create", rec)
        return records

    def write(self, vals):
        res = super().write(vals)
        if self.env.context.get("audit_skip_write"):
            return res
        tracked = {k: v for k, v in vals.items() if not k.startswith("_")}
        Log = self.env["afr.ecm.audit.log"].sudo()
        for rec in self:
            Log.log("write", rec, details=str(tracked) if tracked else None)
        return res

    def unlink(self):
        Log = self.env["afr.ecm.audit.log"].sudo()
        snapshots = [(r._name, r.id, r.display_name) for r in self]
        res = super().unlink()
        for model, rid, name in snapshots:
            Log.sudo().create({
                "event_type": "unlink",
                "model": model,
                "res_id": rid,
                "res_name": name,
                "ip": Log._get_request_ip(),
            })
        return res
