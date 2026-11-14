{
    'name': "My Module",                 # TODO: rename
    'version': '1.0',
    'summary': "Short one-line description",
    'category': 'Uncategorized',
    'depends': ['base'],                 # add e.g. 'mail', 'product', 'maintenance'
    'data': [
        # Order matters: security first, then views (actions), then menus
        'security/ir.model.access.csv',
        'views/my_model_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
