{
    "name": "Little Rides ERP",
    "summary": "Odoo 19 Enterprise ERP for Little Rides India: moulding, Alipur assembly, dealer and "
               "marketplace sales, quality checks, complaints, barcode and maker-checker controls",
    "version": "19.0.1.0.0",
    "category": "Manufacturing",
    "author": "Ochre Digital",
    "website": "https://ochre.digital",
    "license": "OPL-1",
    "depends": [
        "littlerides_erp",
        "account_accountant",
        "account_followup",
        "mrp_workorder",
        "quality_control",
        "quality_mrp",
        "helpdesk",
        "stock_barcode",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": True,
}
