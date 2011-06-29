# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2010 OpenERP SA. (http://www.openerp.com)
#    Copyright (C) 2011 P. Christeas <xrg@hellug.gr>
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

# from tools.translate import _
from osv import fields, osv
from properties import propertyMix

class software_buildbot(osv.osv):
    _name = 'software_dev.buildbot'
    _inherits = { 'software_dev.builder': 'builder_id' }

    _columns = {
        'builder_id': fields.many2one('software_dev.builder', 'Builder', required=True, readonly=True, ondelete='cascade'),
        'tech_code': fields.char('Code', size=64, required=True, select=1),
        'attribute_ids': fields.one2many('software_dev.battr', 'bbot_id', 'Attributes'),
        'http_port': fields.integer('Http port', help="Port to run the buildbot status server at"),
        'http_url': fields.char('Http URL', size=128, help="Base url of our buildbot server, for formatting the full link to results."),
        'slave_ids': fields.one2many('software_dev.bbslave', 'bbot_id', 'Test Steps',
                help="The test steps to perform."),

    }

    _sql_constraints = [ ('code_uniq', 'UNIQUE(tech_code)', 'The tech code must be unique.'), ]

    def get_polled_branches(self, cr, uid, ids, context=None):
        """Helper for the buildbot, list all the repos+branches it needs to poll.

        Since it is difficult to write RPC calls for browse(), we'd better return
        a very easy dictionary of values for the buildbot, that may configure
        its pollers.
        @return A list of dicts, with branch (or repo) information
        """

        ctx = context or {}

        ret = []
        found_branches = []   # ids of branches we have made so far

        builder_ids = []
        for bbot_id in self.browse(cr, uid, ids, context=ctx):
            builder_ids.append(bbot_id.builder_id.id)

        builder_ids = list(set(builder_ids))

        bseries_obj = self.pool.get('software_dev.buildseries')
        series_ids = bseries_obj.search(cr, uid, [('builder_id','in',builder_ids)], context=ctx)  # :(

        def _fmt_branch(branch_bro, fixed_commit=False):
            """Format the branch info into a dictionary
            """
            dret = {}
            dret['rtype'] = branch_bro.repo_id.rtype
            dret['branch_path'] = branch_bro.tech_code or \
                    (branch_bro.sub_url.replace('/','_'))
            dret['fetch_url'] = branch_bro.fetch_url
            dret['poll_interval'] = branch_bro.poll_interval

            if branch_bro.repo_id.proxy_location:
                dret['mirrored'] = True
                dret['repo_base'] = branch_bro.repo_id.proxy_location

            return dret

        for bser in bseries_obj.browse(cr, uid, series_ids, context=ctx):
            if bser.branch_id.id not in found_branches:
                ret.append(_fmt_branch(bser.branch_id))
                found_branches.append(bser.branch_id.id)

            for comp in bser.package_id.component_ids:
                if comp.update_rev and comp.branch_id.id not in found_branches:
                    ret.append(_fmt_branch(comp.branch_id, fixed_commit = comp.commit_id))
                    found_branches.append(comp.branch_id.id)

        return ret

    def get_builders(self, cr, uid, ids, context=None):
        """ Return a complete dict with the builders for this bot

        Sample:
           name: name
           slavename
           build_dir
           branch_url
           tstimer
           steps [ (name, { props}) ]
        """
        ret = []
        bs_obj = self.pool.get('software_dev.buildseries')
        bids = bs_obj.search(cr, uid, [('builder_id', 'in', ids), ('is_build','=',True), ('is_template', '=', False)], context=context)
        for bldr in bs_obj.browse(cr, uid, bids, context=context):
            dir_name = ''
            if bldr.group_id:
                dir_name += bldr.group_id.name + '_'
            if bldr.name:
                dir_name += bldr.name
            dir_name = dir_name.replace(' ', '_').replace('/','_')
            db_name = dir_name.replace('-','_') # FIXME unused

            bret = { 'name': bldr.buildername,
                    'slavename': bldr.builder_id.slave_ids[0].tech_code,
                    'builddir': dir_name,
                    'steps': [],
                    'branch_url': bldr.branch_url,
                    'branch_name': bldr.name,
                    'properties': { 'sequence': bldr.sequence, }
                    #'tstimer': None, # means one build per change
                    }

            if bldr.group_id:
                bret['properties'].update( {'group': bldr.group_id.name,
                                            'group_seq': bldr.group_id.sequence,
                                            'group_public': bldr.group_id.public,})
            # Now, build the steps:
            for bdep in bldr.dep_branch_ids:
                bret['steps'].append( ('OpenObjectBzr', {
                        'repourl': bdep.branch_url, 'mode':'update',
                        'workdir': bdep.target_path,
                        'alwaysUseLatest': True,
                        }) )

            bret['steps'].append( ('OpenObjectBzr', {
                        'repourl': bldr.branch_url, 'mode':'update',
                        'workdir': bldr.target_path,
                        'alwaysUseLatest': False }) )

            # Set a couple of builder-wide properties
            bret['properties'].update( { 'orm_id': bldr.id, 'repo_mode': bldr.target_path })

            for tstep in bldr.test_ids:
                rname = tstep.name
                rattr = {}
                for tattr in tstep.attribute_ids:
                    rattr[tattr['name']] = tattr['value'] #strings only, so far
                bret['steps'].append((rname, rattr))

            ret.append(bret)
        return ret

    def get_conf_timestamp(self, cr, uid, ids, context=None):
        """ Retrieve the max timestamp of configuration changes.

        This should be the maximum of all data that is present on
        a buildbot configuration, so that we know if we need to reload
        the buildbot
        """
        bs_obj = self.pool.get('software_dev.buildseries')
        qry = 'SELECT MAX(GREATEST(write_date, create_date)) FROM "%s" WHERE builder_id = ANY(%%s);' %\
                    (bs_obj._table)
        cr.execute(qry, (ids,))
        res = cr.fetchone()
        # TODO: look at atrributes, properties, buildbot data etc.
        if not res:
            return False
        return res

software_buildbot()

class software_battr(osv.osv):
    """ Build bot attribute

        Raw name-value pairs that are fed to the buildbot
    """
    _name = 'software_dev.battr'
    _columns = {
        'bbot_id': fields.many2one('software_dev.buildbot', 'BuildBot', required=True, select=1),
        'name': fields.char('Name', size=64, required=True, select=1),
        'value': fields.char('Value', size=256),
        }

software_battr()

class software_bbot_slave(osv.osv):
    """ A buildbot slave
    """
    _name = 'software_dev.bbslave'

    _columns = {
        'bbot_id': fields.many2one('software_dev.buildbot', 'Master bot', required=True),
        'name': fields.char('Name', size=64, required=True, select=1),
        'tech_code': fields.char('Code', size=64, required=True, select=1),
        'password': fields.char('Secret', size=128, required=True,
                    help="The secret code used by the slave to connect to the master"),
        #'property_ids': fields.one2many('software_dev.bsattr', 'bslave_id', 'Properties'),
    }

    _sql_constraints = [ ('code_uniq', 'UNIQUE(tech_code)', 'The tech code must be unique.'), ]

software_bbot_slave()

class software_buildscheduler(propertyMix, osv.osv):
    _name = 'software_dev.buildscheduler'
    _description = 'Build Scheduler'
    _columns = {
        'name': fields.char('Name', required=True, size=256, select=1),
        'class_name': fields.char('Class name', size=256, required=True),
        'state_dic': fields.text('State'),
        'change_ids': fields.one2many('software_dev.sched_change', 'sched_id', 'Changes'),
    }

    _sql_constraints = [( 'name_class_uniq', 'UNIQUE(class_name, name)', 'Cannot reuse name at the same scheduler class.'), ]

software_buildscheduler()

class software_schedchange(osv.osv):
    """ Connect a commit to a scheduler and if the sched is interested in it
    """
    _name = 'software_dev.sched_change'
    _description = 'Commit at Scheduler'
    _columns = {
        'commit_id': fields.many2one('software_dev.commit','Commit', required=True),
        'sched_id': fields.many2one('software_dev.buildscheduler','Scheduler', required=True, select=1),
        'important': fields.boolean('Is important', required=True,
                    help="If true, this change will trigger a build, else just recorded."),
    }
    _defaults = {
        'important': True,
    }

    _sql_constraints = [( 'commit_sched_uniq', 'UNIQUE(commit_id, sched_id)', 'A commit can only be at a scheduler once'), ]

software_schedchange()

#eof
