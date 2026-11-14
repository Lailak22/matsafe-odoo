{
    'name': "MatSafe",
    'version': '19.0.1.0.0',
    'summary': "Contact tracing, check-in blocks, mat cleaning and membership pauses for martial arts gyms",
    'author': "MatSafe",
    'category': 'Sports',
    'depends': ['base', 'contacts', 'mail', 'website', 'project', 'stock', 'account'],
    'data': [
        # Order matters: groups first, then access rights, then data, then views/menus
        'security/matsafe_security.xml',
        'security/ir.model.access.csv',
        'data/cron.xml',
        'views/menus.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
