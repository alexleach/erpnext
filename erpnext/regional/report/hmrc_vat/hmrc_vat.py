# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from functools import cached_property

import frappe
from frappe import _
from frappe.query_builder.custom import ConstantColumn
from frappe.query_builder.utils import DocType
from frappe.types import DF
from frappe.utils import formatdate, get_link_to_form


def execute(filters: dict | None = None):
	"""Return columns and data for the report.

	This is the main entry point for the report. It accepts the filters as a
	dictionary and should return columns and data. It is called by the framework
	every time the report is refreshed or a filter is updated.
	"""
	vat_report = UKVatReport(filters)
	return vat_report.run()


class UKVatReport:
	def __init__(self, filters=None):
		self.company = filters.get("company")
		self.from_date = filters.get("from_date")
		self.to_date = filters.get("to_date")

	def run(self):
		columns = get_columns()
		data = self.get_data()
		return columns, data

	def get_data(self) -> list[list]:
		"""Return data for the report.

		The report data is a list of rows, with each row being a list of cell values.
		Grouped by VAT Box as required by HMRC Making Tax Digital API.
		"""
		data = []
		vat_accounts = self.get_vat_accounts()
		vat_account_names = [vat_accounts[acc]["name"] for acc in vat_accounts]
		
		# Get all invoice data
		sales_data = self._get_invoice_data("Sales Invoice", "Customer", vat_account_names)
		purchase_data = self._get_invoice_data("Purchase Invoice", "Supplier", vat_account_names)
		
		# Group by VAT Box - show detailed invoices for VAT boxes (1 and 4)
		# and summary for net amount boxes (6 and 7)
		data.extend(self._format_box_section("Box 1", "VAT on Sales and All Other Outputs", sales_data, show_details=True))
		data.extend(self._format_box_section("Box 4", "VAT on Purchases", purchase_data, show_details=True))
		data.extend(self._format_box_summary("Box 6", "Total Value of Sales Excluding VAT", sales_data, amount_field="net_amount"))
		data.extend(self._format_box_summary("Box 7", "Total Value of Purchases Excluding VAT", purchase_data, amount_field="net_amount"))
		
		return data

	def _get_invoice_data(self, doctype, party, vat_account_names):
		"""Get invoice data grouped by tax rate."""
		invoices = self.get_invoices(doctype, party)
		invoice_items = self.get_invoice_items(doctype, invoices)
		grouped_invoice_items = self.get_items_based_on_tax_rate(doctype, invoices, vat_account_names)

		consolidated_data = self.get_consolidated_data(
			doctype, invoices, invoice_items, grouped_invoice_items
		)

		return consolidated_data
	
	def _format_box_section(self, box_number, box_description, data_by_rate, show_details=True):
		"""Format a VAT box section with its invoices grouped by rate."""
		section_data = []
		
		# Add box header
		box_header = {"invoice": frappe.bold(f"{box_number}: {_(box_description)}")}
		section_data.append(box_header)
		
		# Calculate totals across all rates
		box_total_tax = 0
		box_total_net = 0
		box_total_gross = 0
		
		# Add data for each rate
		for rate, details in sorted(data_by_rate.items()):
			# Add rate subsection header
			rate_label = frappe.bold(f"  Rate: {rate}%")
			section_data.append({"invoice": rate_label})
			
			# Calculate rate totals and optionally show invoice details
			rate_total_tax, rate_total_net, rate_total_gross = self._accumulate_amounts(
				details, section_data if show_details else None
			)
			
			# Add rate subtotal
			rate_subtotal = {
				"invoice": frappe.bold(f"    {_('Subtotal for Rate')} {rate}%"),
				"tax_amount": rate_total_tax,
				"net_amount": rate_total_net,
				"gross_amount": rate_total_gross,
			}
			section_data.append(rate_subtotal)
			box_total_tax += rate_total_tax
			box_total_net += rate_total_net
			box_total_gross += rate_total_gross
		
		# Add box total
		box_total_row = {
			"invoice": frappe.bold(f"{_('Total for')} {box_number}"),
			"tax_amount": box_total_tax,
			"net_amount": box_total_net,
			"gross_amount": box_total_gross,
		}
		section_data.append(box_total_row)
		section_data.append({})  # Empty row for spacing
		
		return section_data
	
	def _accumulate_amounts(self, details, output_list=None):
		"""Accumulate tax, net, and gross amounts from invoice details.
		
		Args:
			details: List of invoice detail rows
			output_list: Optional list to append detail rows to (if showing details)
		
		Returns:
			Tuple of (total_tax, total_net, total_gross)
		"""
		total_tax = 0
		total_net = 0
		total_gross = 0
		
		for row in details:
			if output_list is not None:
				output_list.append(row)
			total_tax += row.get("tax_amount", 0)
			total_net += row.get("net_amount", 0)
			total_gross += row.get("gross_amount", 0)
		
		return total_tax, total_net, total_gross
	
	def _format_box_summary(self, box_number, box_description, data_by_rate, amount_field="net_amount"):
		"""Format a summary VAT box section showing only totals by rate."""
		section_data = []
		
		# Add box header
		box_header = {"invoice": frappe.bold(f"{box_number}: {_(box_description)}")}
		section_data.append(box_header)
		
		# Calculate totals across all rates
		box_total = 0
		
		# Add totals for each rate (without invoice details)
		for rate, details in sorted(data_by_rate.items()):
			rate_total = 0
			for row in details:
				rate_total += row.get(amount_field, 0)
			
			# Add rate summary
			rate_summary = {
				"invoice": f"  Rate: {rate}%",
				amount_field: rate_total,
			}
			section_data.append(rate_summary)
			box_total += rate_total
		
		# Add box total
		box_total_row = {
			"invoice": frappe.bold(f"{_('Total for')} {box_number}"),
			amount_field: box_total,
		}
		section_data.append(box_total_row)
		section_data.append({})  # Empty row for spacing
		
		return section_data

	def get_consolidated_data(self, doctype, invoices, invoice_items, items_based_on_tax_rate):
		consolidated_data_map = {}
		for inv_data in invoices:
			inv = inv_data.get("invoice")
			rate_details = items_based_on_tax_rate.get(inv, {})
			if not rate_details:
				continue

			for rate, item_details in rate_details.items():
				row = {
					"tax_amount": 0.0,
					"gross_amount": 0.0,
					"net_amount": 0.0,
				}

				row["account"] = inv_data.get("account")
				row["posting_date"] = formatdate(inv_data.get("posting_date"), "dd-mm-yyyy")
				row["invoice_type"] = doctype
				row["invoice"] = inv
				row["party_type"] = "Customer" if doctype == "Sales Invoice" else "Supplier"
				row["party"] = inv_data.get("party")
				row["remarks"] = inv_data.get("remarks")
				row["gross_amount"] += item_details.get("gross_amount")
				row["tax_amount"] += item_details.get("tax_amount")
				row["net_amount"] += item_details.get("net_amount")

				consolidated_data_map.setdefault(rate, [])
				consolidated_data_map[rate].append(row)

		return consolidated_data_map

	def get_items_based_on_tax_rate(self, doctype, invoices, tax_accounts):
		from erpnext.accounts.report.item_wise_sales_register.item_wise_sales_register import (
			get_tax_details_query,
		)

		tax_doctype = (
			"Purchase Taxes and Charges" if doctype == "Purchase Invoice" else "Sales Taxes and Charges"
		)
		invoice_names = [_.invoice for _ in invoices]
		if not invoice_names:
			return

		item_wise_tax = frappe.qb.DocType("Item Wise Tax Detail")
		taxes_and_charges = frappe.qb.DocType(tax_doctype)

		tax_details = (
			get_tax_details_query(doctype, tax_doctype)
			.where(item_wise_tax.parent.isin(invoice_names))
			.where(taxes_and_charges.account_head.isin(tax_accounts))
			.run(as_dict=True)
		)

		items_based_on_tax_rate = frappe._dict()
		for row in tax_details:
			parent = row.parent
			items_based_on_tax_rate.setdefault(parent, {}).setdefault(
				row.rate,
				{
					"gross_amount": 0.0,
					"tax_amount": 0.0,
					"net_amount": 0.0,
				},
			)
			items_based_on_tax_rate[parent][row.rate]["tax_amount"] += row.amount
			items_based_on_tax_rate[parent][row.rate]["net_amount"] += row.taxable_amount
			items_based_on_tax_rate[parent][row.rate]["gross_amount"] += row.amount + row.taxable_amount
		return items_based_on_tax_rate

	def get_invoices(
		self,
		invoice_type: DF.Literal["Sales Invoice", "Purchase Invoice"],
		party_type: DF.Literal["Customer", "Supplier"],
	) -> list[dict]:
		dt = DocType(invoice_type)

		invoice_query = (
			frappe.qb.from_(dt)
			.select(
				ConstantColumn(invoice_type).as_("invoice_type"),
				ConstantColumn(party_type).as_("party_type"),
				dt.name.as_("invoice"),
				getattr(dt, party_type.lower()).as_("party"),
				dt.posting_date.as_("posting_date"),
				dt.grand_total.as_("net_amount"),
				dt.total_taxes_and_charges.as_("tax_amount"),
			)
			.where(dt.docstatus == 1)
			.where(dt.company == self.company)
		)

		if self.from_date or self.to_date:
			from_date = self.from_date or formatdate("0001-01-01")
			to_date = self.to_date or formatdate("9999-12-31")
			date_filter = dt.posting_date[from_date:to_date]
			invoice_query = invoice_query.where(date_filter)
		invoices = invoice_query.run(as_dict=True)
		return invoices

	def get_invoice_items(
		self, invoice_type: DF.Literal["Sales Invoice", "Purchase Invoice"], invoices: list[dict]
	):
		Item = DocType(invoice_type + " Item")
		invoices = [_.invoice for _ in invoices]
		if not invoices:
			return []
		q = (
			frappe.qb.from_(Item)
			.select(
				Item.item_code,
				Item.parent.as_("invoice"),
				Item.base_net_amount.as_("item_amount"),
				Item.item_tax_template.as_("item_tax_template"),
			)
			.where(Item.parent.isin(invoices))
		)
		print(q)
		invoice_items = q.run(as_dict=True)
		return invoice_items

	def get_vat_accounts(self):
		vat_accounts = frappe.get_list(
			"Account",
			fields=["name", "account_type", "tax_type", "root_type"],
			filters=[
				["account_type", "Tax"],
				["is_group", 0],
				["company", self.company],
				["name", "like", "%VAT%"],
			],
		)

		accounts = {}
		for acc in vat_accounts:
			acc_type = acc.pop("root_type")
			accounts[acc_type] = acc.copy()

		if (
			not vat_accounts
			and not frappe.in_test
			and not frappe.flags.in_migrate
			or (not accounts.get("Asset", None) or not accounts.get("Liability", None))
		):
			link_to_company = get_link_to_form("Company", self.company, label="Company Settings")
			frappe.throw(
				_(
					"Please select Manage -> Create Tax Template"
					" (to make one Asset and one Liability Tax Account, for VAT), in {0}"
				).format(link_to_company)
			)
		return accounts


def get_columns() -> list[dict]:
	"""Return columns for the report.

	One field definition per column, just like a DocType field definition.
	"""
	return [
		{
			"label": _("Invoice Type"),
			"fieldname": "invoice_type",
			"fieldtype": "Link",
			"options": "DocType",
			"hidden": True,
		},
		{
			"label": _("Party Type"),
			"fieldname": "party_type",
			"fieldtype": "Link",
			"options": "DocType",
			"hidden": True,
		},
		{
			"label": _("Invoice"),
			"fieldname": "invoice",
			"fieldtype": "Dynamic Link",
			"options": "invoice_type",
		},
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 120},
		{
			"label": _("Party"),
			"fieldname": "party",
			"fieldtype": "Dynamic Link",
			"options": "party_type",
			"width": 120,
		},
		{"fieldname": "net_amount", "label": "Net Amount", "fieldtype": "Currency", "width": 130},
		{"fieldname": "tax_amount", "label": "Tax Amount", "fieldtype": "Currency", "width": 130},
		{"fieldname": "gross_amount", "label": "Gross Amount", "fieldtype": "Currency", "width": 130},
	]
