# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################
from osv import fields
from osv import osv

class buildbot_lp_group(osv.osv):
    _name = "buildbot.lp.group"
    _columns = {
                'name': fields.char('Team Name', size=64, required=True, help="Name of the Lp group"),
                'sequence': fields.integer('Sequence', help="Sequence"),
                'url': fields.char('Team URL', size=128, required=True, help="Team Url link"),
                }
buildbot_lp_group()

class buildbot_lp_user(osv.osv):
    _name = "buildbot.lp.user"
    _columns = {
                'name': fields.char('LP User Name', size=64, required=True, help="Name of the Lp User"),
                'url': fields.char('User Url', size=128, help="User Url link"),
                'lp_email': fields.char('LP email', size=128, help="Email of the Lp user"),
                'user_id': fields.many2one('res.users', 'User',help="User Lp ID"),
                'lp_group_ids': fields.many2many('buildbot.lp.group', 'buildbot_lp_users_groups_rel', 'lp_user_id', 'lp_group_id', 'Buildbot Groups', help="Buildbot Groups")
                }
buildbot_lp_user()

class buildbot_lp_branch(osv.osv):
    _name = "buildbot.lp.branch"

    def _get_name(self, cr, uid, ids, name, args, context=None):
        res = {}
        for branch in self.browse(cr, uid, ids):
            res[branch.id] = {'build_directory':'','dbname':''}
            dir_name = ''
            if branch.lp_group_id and branch.lp_group_id.name:
                dir_name += branch.lp_group_id.name + '_'
            if branch.lp_project_id and branch.lp_project_id.name:
                dir_name += branch.lp_project_id.name + '_'
            if branch.name:
                dir_name += branch.name
            res[branch.id]['build_directory'] = dir_name
            res[branch.id]['dbname'] = branch.name.replace('-','_')
        return res

    _columns = {
                'name': fields.char('LP Branch', size=128, required=True, help="Launchpad Branch Name"),
                'lp_group_id': fields.many2one('buildbot.lp.group', 'LP Group', help="Launchpad Group"),
                'lp_user_id': fields.many2one('buildbot.lp.user', 'LP User',help="Launchpad User"),
                'url': fields.char('Source Url', size=128, required=True, help="Source Url"),
                'latest_rev_id': fields.char('Revision Id', size=128, help="Latest Revision ID Tested"),
                'latest_rev_no': fields.integer('Revision Number', help="Latest Revision No Tested"),
                'active': fields.boolean('Active', help="Branch Active/Inactive"),
                "is_test_branch": fields.boolean("Test Branch", help="Is this branch a test branch"),
                "is_root_branch": fields.boolean("Root Branch",help="Is this branch a root branch"),
                'treestabletimer': fields.integer('Tree Stable Timer',help="Timer for the branch"),
                'build_directory': fields.function(_get_name, multi='name', method=True, type='char', string='Build Directoy', size=128, help="The Directory in which this branch will be built"),
                'dbname': fields.function(_get_name, method=True, multi='name', type='char', size=128, string='Database Name', help="The Name of the Database which will be created for testing this branch"),
                'port':fields.integer('port', help="Port for the openerp-server to start"),
                'netport':fields.integer('net-port', help="net-port for the openerp-server to start"),
                'merge_addons': fields.boolean('Merge with Addons', help="Whether you want the branch to be merged with Trunk Addons"),
                'merge_server': fields.boolean('Merge with Server', help="Whether you want the branch to be merged with Trunk Server"),
                'merge_extra_addons': fields.boolean('Merge with Extra Addons', help="Whether you want the branch to be merged with Trunk Extra-Addons"),
                'merge_community_addons': fields.boolean('Merge with Community Addons',help="Whether you want the branch to be merged with Community Addons"),
                'addons_include': fields.text('Include addons', help="Space-separated list of addons to always test"),
                'addons_exclude': fields.text('Exclude addons', help="Never test these addons, blacklist. Space-separated"),
                }
    _defaults = {
        'active': lambda *a: 1,
        }
    _sql_constraints = [
        # ('dbname_build_dir_uniq', 'unique (dbname, build_directory)', 'The database name and build directory must be unique !')
    ]
buildbot_lp_branch()

class buildbot_lp_project(osv.osv):
    _name = "buildbot.lp.project"
    _columns = {
                'name': fields.char('Project Name', size=64, required=True, help="Name of Launchpad Project"),
                'url': fields.char('Project URL', size=128, required=True, help="Url of Launchpad Project"),
                'tester_addons_branch_id': fields.many2one('buildbot.lp.branch', 'Tester Addons Branch', required=True, help="Tester Branch for Addons"),
                'tester_server_branch_id': fields.many2one('buildbot.lp.branch', 'Tester Server Branch', required=True,  help="Tester Branch for Server"),
                'root_branch_id': fields.many2one('buildbot.lp.branch', 'Root Branch', required=True),
                'branch_ids':fields.one2many('buildbot.lp.branch','lp_project_id','Branches', help="Launchpad Branches"),
                'addons_include': fields.text('Include addons', help="Space-separated list of addons to always test"),
                'addons_exclude': fields.text('Exclude addons', help="Never test these addons, blacklist. Space-separated"),
                }
buildbot_lp_project()

class buildbot_lp_branch(osv.osv):
    _name = "buildbot.lp.branch"
    _inherit = "buildbot.lp.branch"
    _columns = {
                'lp_project_id': fields.many2one('buildbot.lp.project', 'LP Project', help="Launchpad Project"),
                # Needed for our buildbot client, that will want to fetch just with a read()
                'lp_project_name': fields.related('lp_project_id', 'name', type='char', relation='buildbot.lp.project', string='Project name'),
                'test_server_branch_name': fields.related('lp_project_id','tester_server_branch_id','name',type='char', relation='buildbot.lp.project', string='Tester Server Branch'),
                'test_server_branch_url' : fields.related('lp_project_id','tester_server_branch_id','url',type='char', relation='buildbot.lp.project', string='Tester Server Url'),
                'test_addons_branch_name': fields.related('lp_project_id','tester_addons_branch_id','name',type='char', relation='buildbot.lp.project', string='Tester Addons Branch'),
                'test_addons_branch_url' : fields.related('lp_project_id','tester_addons_branch_id','url',type='char', relation='buildbot.lp.project', string='Tester Addons Url'),
                
                'project_addons_include': fields.related('lp_project_id', 'addons_include', type='text', relation='buildbot.lp.project', string='Project include', readonly=True),
                'project_addons_exclude': fields.related('lp_project_id', 'addons_exclude', type='text', relation='buildbot.lp.project', string='Project exclude', readonly=True),
                }
buildbot_lp_branch()

class buildbot_test(osv.osv):
    _name = "buildbot.test"
    _order = 'test_date desc'

    def _get_test_result(self, cr, uid, ids, name, args, context=None):
        res = {}
        for test in self.browse(cr, uid, ids):
            res[test.id] = 'unknown'
            for step in test.test_step_ids:
                if step.state == 'fail':
                    res[test.id] = 'fail'
                    break
                elif step.state == 'skip':
                    res[test.id] = 'skip'
                    break
                elif step.state == 'pass' and res[test.id] == 'unknown':
                    res[test.id] = 'pass'
        return res

    def _get_test_ids(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        result = {}
        for step in self.pool.get('buildbot.test.step').browse(cr, uid, ids, context=context):
            result[step.test_id.id] = True
        return result.keys()

    _columns = {
              'name': fields.char('Test Name', size=500, help="Test Name"),
              'test_date': fields.datetime('Date of Test', required=True, help="Date on which the test was conducted"),
              'branch_id': fields.many2one('buildbot.lp.branch', 'Branch', required=True, help="Name of the Launchpad Branch Tested"),
              'environment': fields.text('Test Environment',help="Environment on which test was conducted"),
              'commiter_id': fields.many2one('buildbot.lp.user', 'Branch Committer',required=True,help="Commiter of the revision"),
              'commit_date': fields.datetime('Date Of Commit', required=True, help="Date of commit"),
              'commit_comment': fields.text('Comment On Commit',help="Cooment on commit"),
              'commit_rev_id': fields.char('Revision Id', size=128, help="Revision ID of the commit"),
              'commit_rev_no': fields.integer('Revision No.',help="Revision No of the commit"),
              'new_files': fields.text('Files Added', help="New Files added in the Commit"),
              'update_files': fields.text('Files Updated',help="Files Updated in the Commit"),
              'remove_files': fields.text('Files Removed',help="Files Removed in the Commit"),
              'rename_files': fields.text('Files Renamed',help="Files Renamed in the Commit"),
              'patch_attached':fields.boolean('Patch Attached', readonly=True,help="Patch Attached in the Commit"),
              'state': fields.function(_get_test_result, method=True, type='selection', string="Test Result",
                                        selection=[('unknown','Unknown'), ('fail', 'Failed'), ('pass', 'Passed'),('skip', 'Skipped')],
                                        store={'buildbot.test.step':(_get_test_ids,['test_id'], 10)},
                                        help="Final State of the Test"),
              'test_step_ids':fields.one2many('buildbot.test.step', 'test_id', 'Test Steps'),
              'failure_reason':fields.text('Failure Reason',help="Reason for the failure of the test")
              }
    _defaults = {
                 'state':'unknown'
                 }
buildbot_test()

class buildbot_test_step(osv.osv):
    _name = "buildbot.test.step"
    _columns = {
                'name': fields.char('Name of Step', size=128, help="Name of the Test step"),
                'test_id': fields.many2one('buildbot.test', 'Test', ondelete='cascade', help="Name of the Test"),
                'blame_log': fields.text("Summary", help="Quick blame info of thing(s) that failed"),
                'log': fields.binary('General Log',help="Log File"),
                'quality_log':fields.binary("Module's Quality Log",help="Module's Quality Log File"),
               # 'warning_log': fields.text('Warning Log',help="Warning Log"),
                #'error_log': fields.text('Error Log',help="Error Log"),
                #'critical_log': fields.text('Critical Log',help="Critical Log"),
                #'info_log': fields.text('Info Log',help="Information Log"),
                #'yml_log': fields.text('YML-Test Log',help="YML Log"),
                #'traceback_detail': fields.text('Traceback',help="Traceback Detail"),
                'state': fields.selection([('unknown','Unknown'), ('fail', 'Failed'), 
                                            ('pass', 'Passed'),('skip', 'Skipped'),
                                            ('debug','Debug')], 
                                            "Test Result", readonly=True, required=True,
                                            help="Final State of the Test Step"),
        }
    _defaults = {
                 'state':'unknown'
                }
    _order = 'id'
buildbot_test_step()
