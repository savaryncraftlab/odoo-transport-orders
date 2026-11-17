{
    "name": "Transport Orders",
    "version": "19.0.1.0.0",
    "summary": "Freight forwarding orders: client, carrier, route stops, margin, carrier order PDF",
    "description": """
Transport orders for a small freight forwarding office
======================================================
* One order per client shipment, carried by an external carrier
* Route as an ordered list of loading and unloading stops
* Margin = client price - carrier price, with a warning when it is negative
* Status flow with explicit allowed transitions (draft -> confirmed -> in transit -> delivered)
* Delivered and cancelled orders are locked against edits
* Carrier order PDF that never shows the client price or the margin
* Two access levels: Dispatcher and Manager
""",
    "author": "Yurii Savaryn",
    "license": "LGPL-3",
    "category": "Supply Chain/Transport",
    "depends": ["base", "mail"],
    "data": [
        "security/transport_security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence_data.xml",
        "report/carrier_order_report.xml",
        "views/transport_order_views.xml",
        "views/res_partner_views.xml",
        "views/transport_menus.xml",
    ],
    "demo": [
        "demo/transport_demo.xml",
    ],
    "application": True,
    "installable": True,
}
