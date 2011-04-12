# -*- encoding: utf-8 -*-

""" This is where all the configuration of the buildbot takes place

    The master keeper is one object, that is attached to the buildbot
    configuration and also connected to the database. Upon start, it
    reads the db for the configuration, and sets the buildbot 
    accordingly.
    But, eventually, the master keeper should also poll the OpenERP
    db for changes (eg. new branches etc.) and reconfigure the bbot
    on the fly, without any reload or so.
"""

import logging
from buildbot.buildslave import BuildSlave
from buildbot.process import factory
from buildbot.schedulers.filter import ChangeFilter
from buildbot import manhole
from openobject.scheduler import OpenObjectScheduler, OpenObjectAnyBranchScheduler
from openobject.buildstep import OpenObjectBzr, OpenObjectSVN, BzrMerge, BzrRevert, \
        OpenERPTest, LintTest, BzrStatTest, BzrCommitStats, BzrTagFailure, \
        ProposeMerge, BzrPerformMerge, BzrCommit, BzrSyncUp, MergeToLP
from openobject.poller import BzrPoller
from openobject.status import web, mail, logs
import twisted.internet.task
import rpc
import os
import signal

from twisted.python import log, reflect
from twisted.python import components
from buildbot import util

logging.basicConfig(level=logging.DEBUG)

def str2bool(sstr):
    if sstr and sstr.lower() in ('true', 't', '1', 'on'):
        return True
    return False

class ChangeFilter_debug(ChangeFilter):
    def filter_change(self, change):
        print "Trying to filter %r with %r" % (change, self)
        return ChangeFilter.filter_change(self, change)

class Keeper(object):

    def __init__(self, db_props, bmconfig):
        """
            @param db_props a dict with info how to connect to db
            @param c the BuildmasterConfig dict
        """
        log.msg("Keeper config")
        self.bmconfig = bmconfig
        self.poll_interval = 560.0 #seconds
        self.in_reset = False
        self.bbot_tstamp = None
        c = bmconfig
        # some necessary definitions in the dict:
        c['projectName'] = "OpenERP-Test"
        c['buildbotURL'] = "http://test.openobject.com/"
        c['db_url'] = 'openerp://' # it prevents the db_schema from going SQL
        c['slavePortnum'] = 'tcp:8999:interface=127.0.0.1'

        c['slaves'] = []

        c['schedulers'] = []
        c['builders'] = []
        c['change_source']=[]
        c['status'] = []
        
        r = rpc.session.login(db_props)
        if r != 0:
            raise Exception("Could not login!")
        
        bbot_obj = rpc.RpcProxy('software_dev.buildbot')
        bbot_id = bbot_obj.search([('tech_code','=',db_props.get('code','buildbot'))])
        assert bbot_id, "No buildbot for %r exists!" % db_props.get('code','buildbot')
        self.bbot_id = bbot_id[0]
        self.loop = twisted.internet.task.LoopingCall(self.poll_config)
        from twisted.internet import reactor
        reactor.suggestThreadPoolSize(30)

        self.loop.start(self.poll_interval)
        self.ms_scan = None
        os.umask(int('0027',8))
        try:
            import lp_poller
            lp_poller.MS_Service.startInstance()
            self.ms_scan = lp_poller.MS_Scanner()
            self.ms_scan.startService()
        except ImportError:
            log.err("Could not import the Launchpad scanner, please check your installation!")

    def poll_config(self):
        bbot_obj = rpc.RpcProxy('software_dev.buildbot')
        try:
            new_tstamp = bbot_obj.get_conf_timestamp([self.bbot_id,])
            # print "Got conf timestamp:", self.bbot_tstamp
        except Exception, e:
            print "Could not get timestamp: %s" % e
            return
        if new_tstamp != self.bbot_tstamp:
            try:
                print "Got new timestamp: %s, must reconfig" % new_tstamp
                
                # Zope makes it so difficult to locate the BuildMaster instance,
                # so...
                if self.bbot_tstamp is not None:
                    # Since this will spawn a new Keeper object, we have to stop the
                    # previous one:
                    self.loop.stop()
                    self.ms_scan.stopService()
                    os.kill(os.getpid(), signal.SIGHUP)
                self.bbot_tstamp = new_tstamp
            except Exception:
                print "Could not reset"

    def reset(self):
        """ Reload the configuration
        """
        print "Keeper reset"
        if self.in_reset:
            return
        self.in_reset = True
        c = self.bmconfig
        c['slaves'] = []
        c['schedulers'] = []
        c['builders'] = []
        c['change_source']=[]
        
        c_mail = {}
        poller_kwargs = {}
        proxied_bzrs = {} # map the remote branches to local ones.
        slave_proxy_url = None
        bzr_local_run = None

        bbot_obj = rpc.RpcProxy('software_dev.buildbot')
        bbot_data = bbot_obj.read(self.bbot_id)
        if bbot_data['http_url']:
            c['buildbotURL'] = bbot_data['http_url']

        bbot_attr_obj = rpc.RpcProxy('software_dev.battr')
        bids = bbot_attr_obj.search([('bbot_id','=', self.bbot_id)])
        if bids:
            for attr in bbot_attr_obj.read(bids):
                if attr['name'].startswith('mail_'):
                    c_mail[attr['name']] = attr['value']
                elif attr['name'] == 'proxy_location':
                    poller_kwargs[attr['name']] = attr['value']
                elif attr['name'] == 'slave_proxy_url':
                    slave_proxy_url = attr['value']
                elif attr['name'] == 'bzr_local_run':
                    bzr_local_run = True
                elif attr['name'] == 'manhole':
                    try:
                        mtype, margs = attr['value'].split('|', 1)
                        margs = margs.split('|')
                        klass = getattr(manhole, mtype + 'Manhole')
                        c['manhole'] = klass(*margs)
                    except Exception, e:
                        print "Cannot configure manhole:", e
                else:
                    c[attr['name']] = attr['value']

        # Then, try to setup the slaves:
        bbot_slave_obj = rpc.RpcProxy('software_dev.bbslave')
        bsids = bbot_slave_obj.search([('bbot_id','=', self.bbot_id)])
        if bsids:
            for slav in bbot_slave_obj.read(bsids,['tech_code', 'password']):
                print "Adding slave: %s" % slav['tech_code']
                c['slaves'].append(BuildSlave(slav['tech_code'], slav['password'], max_builds=2))
        
        # Get the repositories we have to poll and maintain
        polled_brs = bbot_obj.get_polled_branches([self.bbot_id])
        print "Got %d polled branches" % (len(polled_brs))
        
        for pbr in polled_brs:
            pmode = pbr.get('mode','branch')
            if pmode == 'branch':
                # Maintain a branch 
                if pbr['rtype'] == 'bzr':
                    fetch_url = pbr['fetch_url']
                    p_interval = int(pbr.get('poll_interval', 600))
                    kwargs = poller_kwargs.copy()
                    category = ''
                    if 'group' in pbr:
                        category = pbr['group'].replace('/','_').replace('\\','_') # etc.
                        kwargs['category'] = pbr['group']
                    if 'proxy_location' in kwargs:
                        if not kwargs['proxy_location'].endswith(os.sep):
                            kwargs['proxy_location'] += os.sep
                        if category:
                            kwargs['proxy_location'] += category + '_'
                        kwargs['proxy_location'] += pbr.get('branch_name', 'branch-%d' % pbr['branch_id'])
    
                    if p_interval > 0:
                        c['change_source'].append(BzrPoller(fetch_url,
                            poll_interval = p_interval,
                            branch_name=pbr.get('branch_name', None),
                            branch_id=pbr['branch_id'], keeper=self,
                            **kwargs))
                    if bzr_local_run:
                        c['change_source'][-1].local_run = bzr_local_run
                    if slave_proxy_url and kwargs.get('proxy_location'):
                        tbname = pbr.get('branch_name', 'branch-%d' % pbr['branch_id'])
                        if category:
                            tbname = category + '_' + tbname
                        tbname = tbname.replace(' ','%20').replace('/','%2F')
                        proxied_bzrs[fetch_url] = slave_proxy_url + '/' + tbname
                else:
                    raise NotImplementedError("No support for %s repos yet" % pbr['rtype'])

        # Get the tests that have to be performed:
        builders = bbot_obj.get_builders([self.bbot_id])
        
        dic_steps = { 'OpenERP-Test': OpenERPTest,
                'OpenObjectBzr': OpenObjectBzr,
                'BzrRevert': BzrRevert,
                'BzrStatTest': BzrStatTest,
                'BzrCommitStats': BzrCommitStats,
                'LintTest': LintTest,
                'BzrMerge': BzrMerge,
                'BzrTagFailure': BzrTagFailure,
                'ProposeMerge': ProposeMerge,
                'BzrPerformMerge': BzrPerformMerge,
                'BzrCommit': BzrCommit,
                'BzrSyncUp': BzrSyncUp,
                'MergeToLP': MergeToLP,
                }

        for bld in builders:
            fact = factory.BuildFactory()
            props = bld.get('properties', {})
           
            for bstep in bld['steps']:
                assert bstep[0] in dic_steps, "Unknown step %s" % bstep[0]
                kwargs = bstep[1].copy()
                # TODO manipulate some of them
                if 'locks' in kwargs:
                   pass # TODO
                if 'keeper' in kwargs:
                    kwargs['keeper'] = self

                
                klass = dic_steps[bstep[0]]
                if bstep[0] in ('OpenObjectBzr') and kwargs['repourl'] in proxied_bzrs:
                    kwargs['proxy_url'] = proxied_bzrs[kwargs['repourl']]
                if bstep[0] in ('BzrPerformMerge', 'BzrSyncUp'):
                    # Pass all of them to buildstep, so that it can resolve
                    # all the changes it will be receiving.
                    kwargs['proxied_bzrs'] = proxied_bzrs
                print "Adding step %s(%r)" % (bstep[0], kwargs)
                fact.addStep(klass(**kwargs))
            
            c['builders'].append({
                'name' : bld['name'],
                'slavename' : bld['slavename'],
                'builddir': bld['builddir'],
                'factory': fact,
                'properties': props,
                'mergeRequests': False,
                'category': props.get('group', None),
            })

            cfilt = ChangeFilter(branch=bld['branch_name'])
            # FIXME
            c['schedulers'].append(
                OpenObjectScheduler(name = "Scheduler for %s" %(bld['name']),
                                    builderNames = [bld['name'], ],
                                    change_filter=cfilt,
                                    treeStableTimer= bld.get('tstimer',None),
                                    properties={},
                                    keeper=self)
                                )

        if bbot_data['http_port']:
            print "We will have a http server at %s" % bbot_data['http_port']
            c['status'].append(web.OpenObjectWebStatus(http_port=bbot_data['http_port']))

        if c_mail.get('mail_smtp_host', False):
            mail_kwargs= {
                'projectURL': c['buildbotURL'],
                'extraRecipients'   : c_mail.get('mail_notify_cc', 'hmo@tinyerp.com').split(','),
                'html_body': str2bool(c_mail.get('mail_want_html','false')), # True value will send mail in HTML
                'smtpUser':  c_mail.get('mail_smtp_username',''),
                'smtpPassword':  c_mail.get('mail_smtp_passwd',''),
                'smtpPort': c_mail.get('mail_smtp_port', 2525),
                'subject': c_mail.get('mail_subject', '[%(projectName)s-buildbot] build of %(builder)s ended in %(result)s'),
                'fromaddr':  c_mail.get('mail_sender_email', '<noreply@openerp.com>'),
                'reply_to':  c_mail.get('mail_reply_to', 'support@tinyerp.com'),
                'relayhost': c_mail.get('mail_smtp_host'),
                'useTls':       str2bool(c_mail.get('mail_email_tls','t')),
                'mode':      c_mail.get('mail_notify_mode', 'failing'),
                                                # 'all':sends mail when step is either success/failure or had problem.
                                                # 'problem':sends mail when step had problem.
                                                # 'failing':sends mail when step fails.

                }
                
            c['status'].append(mail.OpenObjectMailNotifier( **mail_kwargs))

        # We should be ok by now..
        self.in_reset = False

    def __del__(self):
        log.msg("Here is where the keeper sleeps..")
        self.loop.stop()
        try:
            import lp_poller
            lp_poller.MS_Service.stopInstance()
            self.ms_scan.stopService()
            rpc.session.logout()
        except Exception: pass

from buildbot.db import connector as bbot_connector
import connector

bbot_connector.db_connector = connector.OERPConnector

#eof
