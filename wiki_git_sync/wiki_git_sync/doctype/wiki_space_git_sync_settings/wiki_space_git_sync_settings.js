// Copyright (c) 2025, Tanmoy and contributors
// For license information, please see license.txt

frappe.ui.form.on("Wiki Space Git Sync Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Export to Git"), function () {
			frm.call("export_docs");
		});

		frm.add_custom_button(__("Pull from Git"), function () {
			frm.call("pull_changes");
		});
	},
});
