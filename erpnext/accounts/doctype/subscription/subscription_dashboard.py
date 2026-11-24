from frappe import _


def get_data():
	return {
		"fieldname": "subscription",
		"transactions": [
			{"label": _("Buying"), "items": ["Purchase Order", "Purchase Invoice"]},
			{"label": _("Selling"), "items": ["Sales Order", "Sales Invoice"]},
		],
	}
