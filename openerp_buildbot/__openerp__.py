{
    "name" : "Integration Server",
    "version" : "1.0",
    "depends" : [
                    "base",
                ],
     'description': """
    This module keeps track of all the branches to be tested.
""",
    "author" : "Tiny",
    'category': 'Generic Modules/Others',
    'website': 'http://test.openobject.com/',
    "init_xml" : [],
    "demo_xml" : [
                  'demo/buildbot_demo.xml',
                  'demo/buildbot_tests_demo.xml'],
    "update_xml" : [
                    'buildbot_view.xml',
                    'report/buildbot_report_view.xml'
                    ],
    "installable" : True,
    "active" : False,
}