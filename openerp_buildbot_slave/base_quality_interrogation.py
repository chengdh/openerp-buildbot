#!/usr/bin/python
# -*- coding: utf-8 -*-
##############################################################################
#    
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>).
#    Copyright (C) 2010 OpenERP SA. (http://www.openerp.com)
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

import xmlrpclib
# import ConfigParser
import optparse
import sys
import threading
import os
import time
import pickle
import base64
# import socket
import subprocess
import select
import re

admin_passwd = 'admin'

def to_decode(s):
    try:
        return s.encode('utf-8')
    except UnicodeError:
        try:
            return s.encode('latin')
        except UnicodeError:
            try:
                return s.decode('ascii')
            except UnicodeError:
                return s

class ClientException(Exception):
    """Define our own exception, to avoid traceback
    """
    pass

class ServerException(Exception):
    pass

# --- cut here
import logging
# import types

def xmlescape(sstr):
    return sstr.replace('<','&lt;').replace('>','&gt;').replace('&','&amp;')

class XMLStreamHandler(logging.FileHandler):
    """ An xml-like logging handler, writting stream into a file.
    
    Note that we don't use any xml dom class here, because we want
    our output to be immediately streamed into a file. Upon any 
    crash of the script, the partial xml will be useful.
    """
    def __init__(self, filename, encoding='UTF-8'):
        logging.FileHandler.__init__(self, filename, mode='w', 
                encoding=encoding, delay=False)
        # now, open the file and write xml prolog
        self.formatter = XMLFormatter()
        self.stream.write('<?xml version="1.0", encoding="%s" ?>\n<log>' % encoding)
        
    def close(self):
        # write xml epilogue
        self.stream.write('</log>\n')
        logging.FileHandler.close(self)

    # Note: we need not re-implement emit, because a special formatter
    # will be used

class XMLFormatter(logging.Formatter):
    """ A special formatter that will output all fields in an xml-like
    struct """
    
    def format(self, record):
        """ Put everything in xml format """
        
        s = '<rec name="%s" level="%s" time="%s" >' % \
            (record.name, record.levelno, record.created)

        if False and (record.filename or record.module or record.lineno):
            s += '<code filename="%s" module="%s" line="%s" />' % \
                    (record.filename, record.module, record.lineno)


        if record.exc_info and not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)

        if record.exc_text:
            s+= '<exception>%s</exception>' % xmlescape(record.exc_text)

        s+= xmlescape(record.getMessage())
        s+= '</rec>'

        return s.decode('utf-8')

# --- cut here

class MachineFormatter(logging.Formatter):
    """ Machine-parseable log output, in plain text stream.
    
    In order to have parsers analyze the output of the logs, have
    the following format:
        logger[|level]> msg...
        + msg after newline
        :@ First exception line
        :+ second exception line ...
    
    It should be simple and well defined for the other side.
    """
    
    def format(self, record):
        """ Format to stream """
        
        levelstr = ''
        if record.levelno != logging.INFO:
            levelstr = '|%d' % record.levelno

        try:
            msgtxt = record.getMessage().replace('\n','\n+ ')
        except TypeError:
            print "Message:", record.msg
            msgtxt = record.msg

        s = "%s%s> %s" % ( record.name, levelstr, msgtxt)

        if record.exc_info and not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)

        if record.exc_text:
            s+= '\n:@ %s' % record.exc_text.replace('\n','\n:+ ')

        # return s.decode('utf-8')
        return s

class server_thread(threading.Thread):
    
    def regparser(self, section, regex, funct):
        self.__parsers.setdefault(section, []).append( (regex, funct) )

    def setRunning(self, section, level, line):
        self.log.info("Server is ready!")
        self.is_ready = True
        
    def setListening(self, section, level, mobj):
        self.log.info("Server listens %s at %s:%s" % mobj.group(1, 2, 3))
        self._lports[mobj.group(1)] = mobj.group(3)

    def clear_context(self):
        if self.state_dict.get('context', False) != False:
            self.log_state.info("clear context")
            self.state_dict['context'] = False

    def _set_log_context(self, ctx):
        if ctx != self.state_dict.get('context', False):
            self.log_state.info("set context %s", ctx)
            self.state_dict['context'] = ctx

    def setModuleLoading(self, section, level, mobj):
        self.state_dict['module'] = mobj.group(1)
        self.state_dict['module-phase'] = 'init'
        self._set_log_context("%s.%s" % (mobj.group(1),
                            self.state_dict['module-mode']))
        self.state_dict['module-file'] = None
    
    def setModuleLoading2(self, section, level, mobj):
        self.state_dict['module'] = mobj.group(1)
        
        # By the 'registering objects' message we just know that the
        # module is present in the server.
        # So, reset state, mark module as present
        self.state_dict['module-phase'] = 'reg'
        self.state_dict['module-file'] = None
        self.state_dict.setdefault('regd-modules',[]).append(mobj.group(1))
        
        #self._set_log_context("%s.%s" % (mobj.group(1),
        #                    self.state_dict['module-mode']))
    
    def setModuleFile(self, section, level, mobj):
        if mobj.group(2) == 'objects':
            return
        self.state_dict['module'] = mobj.group(1)
        self.state_dict['module-phase'] = 'file'
        self._set_log_context("%s.%s" % (mobj.group(1),
                            self.state_dict['module-mode']))
        self.state_dict['module-file'] = mobj.group(2)
        self.log.debug("We are processing: %s/%s", self.state_dict['module'],
                self.state_dict['module-file'])
        
    def __init__(self, root_path, port, netport, addons_path, pyver=None, 
                srv_mode='v600', timed=False, debug=False):
        threading.Thread.__init__(self)
        self.root_path = root_path
        self.port = port
        # self.addons_path = addons_path
        self.args = [ 'python%s' %(pyver or ''), '%sopenerp-server.py' % root_path,]
        if addons_path:
            self.args += [ '--addons-path=%s' % addons_path ]
        if debug:
            self.args += [ '--log-level=debug' ]
        else:
            self.args += [ '--log-level=test' ]

        # TODO: secure transport, persistent ones.
        if srv_mode == 'v600':
            self.args.append('--xmlrpc-port=%s' % port )
            self.args.append('--no-xmlrpcs')
        elif srv_mode == 'pg84':
            self.args.append('--httpd-port=%s' % port )
            self.args.append('--no-httpds')
        else:
            raise RuntimeError("Invalid server mode %s" % srv_mode)

        if netport:
            self.args.append('--netrpc-port=%s' % netport)
        else:
            self.args.append('--no-netrpc')
        
        if timed:
            self.args.insert(0, 'time')
        self.proc = None
        self.is_running = False
        self.is_ready = False
        self._lports = {}
        # Will hold info about current op. of the server
        self.state_dict = {'module-mode': 'startup'}

        # self.is_terminating = False
        
        # Regular expressions:
        self.linere = re.compile(r'\[(.*)\] ([A-Z]+):([\w\.-]+):(.*)$')
        
        self.log = logging.getLogger('srv.thread')
        self.log_sout = logging.getLogger('server.stdout')
        self.log_serr = logging.getLogger('server.stderr')
        self.log_state = logging.getLogger('bqi.state') # will receive command-like messages

        self.__parsers = {}
        self.regparser('web-services', 
                'the server is running, waiting for connections...', 
                self.setRunning)
        self.regparser('web-services',
                re.compile(r'starting (.+) service at ([0-9\.]+) port ([0-9]+)'),
                self.setListening)
        self.regparser('init',re.compile(r'module (.+): creating or updating database tables'),
                self.setModuleLoading)
        self.regparser('init', re.compile(r'module (.+): registering objects$'),
                self.setModuleLoading2)
        self.regparser('init',re.compile(r'module (.+): loading (.+)$'),
                self.setModuleFile)
        

    def stop(self):
        if (not self.is_running) and (not self.proc):
            time.sleep(2)

        if not self.proc :
            self.log.error("Program has not started")
        elif self.proc.returncode is not None:
            self.log.warning("Program is not running")
        else:
            self.log.info("Terminating..")
            self.proc.terminate()
            self.log.info('Terminated.')
            
            # TODO: kill if not terminate right.
        
    def run(self):
        try:
            self.log.info("will run: %s", ' '.join(self.args))
            self.proc = subprocess.Popen(self.args, shell=False, cwd=None, 
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.is_running = True
            self.log.info("server running at pid: %d", self.proc.pid)
            pob = select.poll()
            pob.register(self.proc.stdout)
            pob.register(self.proc.stderr)
            fdd = { self.proc.stdout.fileno(): self.proc.stdout ,
                    self.proc.stderr.fileno(): self.proc.stderr }
        
            while True:
                self.proc.poll()
                if self.proc.returncode is not None:
                    break
                # Now, see if we have output:
                p = pob.poll(10000)
                for fd, event in p:
                    if event == select.POLLIN:
                        r = fdd[fd].readline()
                        if r.endswith("\n"):
                            r = r[:-1]
                        if not r:
                            continue
                        m = self.linere.match(r)
                        if m:
                            for regex, funct in self.__parsers.get(m.group(3),[]):
                                if isinstance(regex, basestring):
                                    if regex == m.group(4):
                                        if callable(funct):
                                            funct(m.group(3), m.group(2), m.group(4))
                                        elif isinstance(funct, tuple):
                                            logger = logging.getLogger('bqi.'+ funct[0])
                                            level = funct[1]
                                            logger.log(level, funct[2])
                                        else:
                                            self.log.info(funct)
                                else:  # elif isinstance(regex, re.RegexObject):
                                    mm = regex.match(m.group(4))
                                    if mm:
                                        if callable(funct):
                                            funct(m.group(3), m.group(3), mm)
                                        elif isinstance(funct, tuple):
                                            logger = logging.getLogger('bqi.'+ funct[0])
                                            level = funct[1]
                                            log_args = mm.groups('')
                                            logger.log(level, funct[2], *log_args)
                                        else:
                                            self.log.info(funct, *log_args)
                   
                        # now, print the line at stdout
                        if fdd[fd] is self.proc.stdout:
                            olog = self.log_sout
                        else:
                            olog = self.log_serr
                        olog.info(r)

            self.is_ready = False
            self.log.info("Finished server with: %d", self.proc.returncode)
        finally:
            self.is_running = False
        
    def start_full(self):
        """ start and wait until server is up, ready to serve
        """
        self.start()
        time.sleep(1)
        t = 0
        while not self.is_ready:
            if not self.is_running:
                raise ServerException("Server cannot start")
            if t > 120:
                self.stop()
                raise ServerException("Server took too long to start")
            time.sleep(1)
            t += 1
        if self._lports.get('HTTP') != str(self.port):
            self.log.warning("server does not listen HTTP at port %s" % self.port)
        return True

class client_worker(object):
    """ This object will connect to a server and perform the various tests.
    
        It holds some common options.
    """
    
    def __init__(self, uri, options):
        global server
        self.log = logging.getLogger('bqi.client')
        if not server.is_ready:
            self.log.error("Server not ready, cannot work client")
            raise RuntimeError()
        self.uri = uri
        self.user = options['login']
        self.pwd = options['pwd']
        self.dbname = options['dbname']
        self.super_passwd = 'admin' # options['super_passwd']
        self.series = options['server_series']

    def _execute(self, connector, method, *args):
        self.log.debug("Sending command '%s' to server", method)
        res = getattr(connector,method)(*args)
        self.log.debug("Command '%s' returned from server", method)
        return res

    def _login(self):
        conn = xmlrpclib.ServerProxy(self.uri + '/xmlrpc/common')
        uid = self._execute(conn, 'login', self.dbname, self.user, self.pwd)
        if not uid:
            self.log.error("Cannot login as %s@%s" %(self.user, self.pwd))
        return uid

    def import_translate(self, user, pwd, dbname, translate_in):
        uid = self._login()
        if not uid:
            return False
        conn = xmlrpclib.ServerProxy(self.uri + '/xmlrpc/wizard')
        server.state_dict['module-mode'] = 'translate'
        wiz_id = self._execute(conn, 'create',self.dbname, uid, self.pwd, 'module.lang.import')
        for trans_in in translate_in:
            lang,ext = os.path.splitext(trans_in.split('/')[-1])
            state = 'init'
            datas = {'form':{}}
            while state!='end':
                res = self._execute(conn,'execute',self.dbname, uid, self.pwd, wiz_id, datas, state, {})
                if 'datas' in res:
                    datas['form'].update( res['datas'].get('form',{}) )
                if res['type']=='form':
                    for field in res['fields'].keys():
                        datas['form'][field] = res['fields'][field].get('value', False)
                    state = res['state'][-1][0]
                    trans_obj = open(trans_in)
                    datas['form'].update({
                        'name': lang,
                        'code': lang,
                        'data' : base64.encodestring(trans_obj.read())
                    })
                    trans_obj.close()
                elif res['type']=='action':
                    state = res['state']
        return True

    def check_quality(self, modules, quality_logs):
        uid = self._login()
        quality_logs += 'quality-logs'
        if not uid:
            return False
        conn = xmlrpclib.ServerProxy(self.uri + '/xmlrpc/object')
        final = {}
        for module in modules:
            qualityresult = {}
            test_detail = {}
            server.state_dict['module-mode'] = 'quality'
            quality_result = self._execute(conn,'execute', self.dbname, 
                                uid, self.pwd,
                                'module.quality.check','check_quality',module)
            detail_html = ''
            html = '''<html><body><a name="TOP"></a>'''
            html +="<h1> Module: %s </h1>"%(quality_result['name'])
            html += "<h2> Final score: %s</h2>"%(quality_result['final_score'])
            html += "<div id='tabs'>"
            html += "<ul>"
            for x,y,detail in quality_result['check_detail_ids']:
                test = detail.get('name')
                msg = detail.get('message','')
                score = round(float(detail.get('score',0)),2)
                html += "<li><a href=\"#%s\">%s</a></li>"%(test.replace(' ','-'),test)
                detail_html +='''<div id=\"%s\"><h3>%s (Score : %s)</h3><font color=red><h5>%s</h5></font>%s</div>'''%(test.replace(' ', '-'), test, score, msg, detail.get('detail', ''))
                test_detail[test] = (score,msg,detail.get('detail',''))
            html += "</ul>"
            html += "%s"%(detail_html)
            html += "</div></body></html>"
            if not os.path.isdir(quality_logs):
                os.mkdir(quality_logs)
            fp = open('%s/%s.html'%(quality_logs,module),'wb')
            fp.write(to_decode(html))
            fp.close()
            #final[quality_result['name']] = (quality_result['final_score'],html,test_detail)

        #fp = open('quality_log.pck','wb')
        #pck_obj = pickle.dump(final,fp)
        #fp.close()
        #print "LOG PATH%s"%(os.path.realpath('quality_log.pck'))
        return True

    def get_ostimes(self, prev=None):
        if self.series not in ('pg84',):
            self.log.debug("Using client-side os.times()")
            return os.times()
        try:
            conn = xmlrpclib.ServerProxy(self.uri + '/xmlrpc/common')
            ost = self._execute(conn,'get_os_time', self.super_passwd)
            if prev is not None:
                for i in range(0,5):
                    ost[i] -= prev[i]
            return ost
        except Exception:
            self.log.exception("Get os times")
            return ( 0.0, 0.0, 0.0, 0.0, 0.0 )


    def wait(self, id):
        progress=0.0
        conn = xmlrpclib.ServerProxy(self.uri+'/xmlrpc/db')
        while not progress==1.0:
            time.sleep(2.0)
            progress,users = self._execute(conn,'get_progress',self.super_passwd, id)
            self.log.debug("Progress: %s", progress)
        return True


    def create_db(self, lang='en_US'):
        conn = xmlrpclib.ServerProxy(self.uri + '/xmlrpc/db')
        # obj_conn = xmlrpclib.ServerProxy(self.uri + '/xmlrpc/object')
        #wiz_conn = xmlrpclib.ServerProxy(self.uri + '/xmlrpc/wizard')
        #login_conn = xmlrpclib.ServerProxy(self.uri + '/xmlrpc/common')
        db_list = self._execute(conn, 'list')
        if self.dbname in db_list:
            raise ClientException("Database already exists, drop it first!")
        id = self._execute(conn,'create',self.super_passwd, self.dbname, True, lang)
        self.wait(id)
        if not self.install_module(['base_module_quality',]):
            self.log.warning("Could not install 'base_module_quality' module.")
            # but overall pass
        self.log.info("Successful create of db: %s", self.dbname)
        return True

    def drop_db(self):
        conn = xmlrpclib.ServerProxy(self.uri + '/xmlrpc/db')
        db_list = self._execute(conn,'list')
        if self.dbname in db_list:
            self.log.info("Going to drop db: %s", self.dbname)
            self._execute(conn, 'drop', self.super_passwd, self.dbname)
            self.log.info("Dropped db: %s", self.dbname)
            return True
        else:
            self.log.error("Not dropping db '%s' because it doesn't exist", self.dbname)
            return False

    def install_module(self, modules):
        uid = self._login()
        if not uid:
            return False
        
        # what buttons to press at each state:
        form_presses = { 'init': 'start', 'next': 'start', 'start': 'end' }
        server.state_dict['module-mode'] = 'install'
        obj_conn = xmlrpclib.ServerProxy(self.uri + '/xmlrpc/object')
        wizard_conn = xmlrpclib.ServerProxy(self.uri + '/xmlrpc/wizard')
        module_ids = self._execute(obj_conn, 'execute', self.dbname, uid, self.pwd, 
                        'ir.module.module', 'search', [('name','in',modules)])
        if not module_ids:
            self.log.error("Cannot find any of [%s] modules to install!",
                            ', '.join(modules))
            return False
        self._execute(obj_conn, 'execute', self.dbname, uid, self.pwd, 
                        'ir.module.module', 'button_install', module_ids)
        wiz_id = self._execute(wizard_conn, 'create', self.dbname, uid, self.pwd, 
                        'module.upgrade.simple')
        
        datas = {}
        return self.run_wizard(wizard_conn, uid, wiz_id, form_presses, datas)
        
    def run_wizard(self, wizard_conn, uid, wiz_id, form_presses, datas):
        """ Simple Execute of a wizard, press form_presses until end.
        
            This tries to go through a wizard, by trying the states found
            in form_presses. If form_presses = { 'start': 'foo', 'foo': 'end'}
            then the 'foo' button(=state) will be pressed at 'start', then
            the 'end' button at state 'foo'.
            If it sucessfully reaches the 'end', then the wizard will have
            passed.
        """
        
        state = 'init'
        log = logging.getLogger("bqi.wizard") #have a separate one.
        i = 0
        good_state = True
        while state!='end':
            res = self._execute(wizard_conn, 'execute', self.dbname, uid, self.pwd, 
                            wiz_id, datas, state, {})
            i += 1
            if i > 100:
                log.error("Wizard abort after %d steps", i)
                raise RuntimeError("Too many wizard steps")
            
            next_state = 'end'
            if res['type'] == 'form':
                if state in form_presses:
                    next_state = form_presses[state]
                pos_states = [ x[0] for x in res['state'] ]
                if next_state in pos_states:
                    log.debug("Pressing button for %s state", next_state)
                    state = next_state
                else:
                    log.warning("State %s not found in %s, forcing end", next_state, pos_states)
                    state = 'end'
                    good_state = False
            elif res['type'] == 'action':
                if state in form_presses:
                    next_state = form_presses[state]
                if res['state'] in pos_states:
                    log.debug("Pressing button for %s state", next_state)
                    state = next_state
                else:
                    log.warning("State %s not found in %s, forcing end", next_state, pos_states)
                    state = 'end'
                    good_state = False
            else:
                log.debug("State: %s, res: %r", state, res)
        log.info("Wizard ended in %d steps", i)
        return good_state

    def upgrade_module(self, modules):
        uid = self._login()
        if not uid:
            return False
        server.state_dict['module-mode'] = 'upgrade'
        obj_conn = xmlrpclib.ServerProxy(self.uri + '/xmlrpc/object')
        wizard_conn = xmlrpclib.ServerProxy(self.uri + '/xmlrpc/wizard')
        module_ids = self._execute(obj_conn, 'execute', self.dbname, uid, self.pwd, 
                            'ir.module.module', 'search', [('name','in',modules)])
        self._execute(obj_conn, 'execute', self.dbname, uid, self.pwd, 
                            'ir.module.module', 'button_upgrade', module_ids)
        wiz_id = self._execute(wizard_conn, 'create', self.dbname, uid, self.pwd, 
                            'module.upgrade.simple')
        datas = {}
        form_presses = { 'init': 'start', 'next': 'start',  'config': 'end',  'start': 'end'}
        
        return self.run_wizard(wizard_conn, uid, wiz_id, form_presses, datas)


usage = """%prog command [options]

Basic Commands:
    start-server         Start Server
    create-db            Create new database
    drop-db              Drop database
    install-module [<m> ...]   Install module
    upgrade-module [<m> ...]   Upgrade module
    install-translation        Install translation file
    check-quality  [<m> ...]    Calculate quality and dump quality result 
                                [ into quality_log.pck using pickle ]
    multi <cmd> [<cmd> ...]     Execute several of the above commands, at a 
                                single server instance.
"""
parser = optparse.OptionParser(usage)
parser.add_option("-m", "--modules", dest="modules", action="append",
                     help="specify modules to install or check quality")
parser.add_option("--addons-path", dest="addons_path", help="specify the addons path")
parser.add_option("--xml-log", dest="xml_log", help="A file to write xml-formatted log to")
parser.add_option("--txt-log", dest="txt_log", help="A file to write plain log to, or 'stderr'")
parser.add_option("--machine-log", dest="mach_log", help="A file to write machine log stream, or 'stderr'")
parser.add_option("--debug", dest="debug", action='store_true', default=False,
                    help="Enable debugging of both the script and the server")

parser.add_option("--quality-logs", dest="quality_logs", help="specify the path of quality logs files which has to stores")
parser.add_option("--root-path", dest="root_path", help="specify the root path")
parser.add_option("-p", "--port", dest="port", help="specify the TCP port", type="int")
parser.add_option("--net_port", dest="netport",help="specify the TCP port for netrpc")
parser.add_option("-d", "--database", dest="db_name", help="specify the database name")
parser.add_option("--login", dest="login", help="specify the User Login")
parser.add_option("--password", dest="pwd", help="specify the User Password")

parser.add_option("--translate-in", dest="translate_in",
                     help="specify .po files to import translation terms")
parser.add_option("--extra-addons", dest="extra_addons",
                     help="specify extra_addons and trunkCommunity modules path ")
parser.add_option("--server-series", help="Specify argument syntax and options of the server.\nExamples: 'v600', 'pg84'")

(opt, args) = parser.parse_args()

def die(cond, msg):
    if cond:
        print msg
        sys.exit(1)

options = {
    'addons-path' : opt.addons_path or False,
    'quality-logs' : opt.quality_logs or '',
    'root-path' : opt.root_path or '',
    'translate-in': [],
    'port' : opt.port or 8069,
    'netport':opt.netport or False,
    'dbname': opt.db_name ,
    'modules' : opt.modules,
    'login' : opt.login or 'admin',
    'pwd' : opt.pwd or 'admin',
    'extra-addons':opt.extra_addons or [],
    'server_series': opt.server_series or 'v600'
}

import logging
def init_log():
    global opt
    log = logging.getLogger()
    if opt.debug:
        log.setLevel(logging.DEBUG)
    else:
        log.setLevel(logging.INFO)
    has_stdout = has_stderr = False

    if not (opt.xml_log or opt.txt_log or opt.mach_log):
        # Default to a txt logger
        opt.txt_log = 'stderr'

    if opt.xml_log:
        hnd = XMLStreamHandler(opt.xml_log)
        log.addHandler(hnd)
        
    if opt.txt_log:
        if opt.txt_log == 'stderr':
            log.addHandler(logging.StreamHandler())
            has_stderr = True
        elif opt.txt_log == 'stdout':
            log.addHandler(logging.StreamHandler(sys.stdout))
            has_stdout = True
        else:
            log.addHandler(logging.FileHandler(opt.txt_log))
            #hnd2.setFormatter()

    if opt.mach_log:
        if opt.mach_log == 'stdout':
            if has_stdout:
                raise Exception("Cannot have two loggers at stdout!")
            hnd3 = logging.StreamHandler(sys.stdout)
            has_stdout = True
        else:
            hnd3 = logging.FileHandler(opt.mach_log)
        hnd3.setFormatter(MachineFormatter())
        log.addHandler(hnd3)

init_log()

logger = logging.getLogger('bqi')

def parse_cmdargs(args):
    """Parse the non-option arguments into an array of commands
    
    The array has entries like ('cmd', [args...])
    Multiple commands may be specified either with the 'multi'
    command or with the '--' separator from the last cmd.
    """
    global parser
    ret = []
    while len(args):
        command = args[0]
        if command == '--':
            args = args[1:]
            continue

        if command[0] in ('-', '+'): # TODO
            cmd2 = command[1:]
        else:
            cmd2 = command

        if cmd2 not in ('start-server','create-db','drop-db',
                    'install-module','upgrade-module','check-quality',
                    'install-translation', 'multi'):
            parser.error("incorrect command: %s" % command)
            return
        args = args[1:]
        if command == '--':
            continue
        elif cmd2 == 'multi':
            ret.extend([(x, []) for x in args])
            return ret
        elif cmd2 in ('install-module', 'upgrade-module', 'check-quality',
                        'install-translation'):
            # Commands that take args
            cmd_args = []
            while args and args[0] != '--':
                cmd_args.append(args[0])
                args = args[1:]
            ret.append((command, cmd_args))
        else:
            ret.append((command, []))
        
    return ret

cmdargs = parse_cmdargs(args)
if len(cmdargs) < 1:
    parser.error("You have to specify a command!")

#die(lmodules and (not opt.db_name),
#        "the modules option cannot be used without the database (-d) option")

die(opt.translate_in and (not opt.db_name),
        "the translate-in option cannot be used without the database (-d) option")

# Hint:i18n-import=purchase:ar_AR.po+sale:fr_FR.po,nl_BE.po
if opt.translate_in:
    translate = opt.translate_in
    for module_name,po_files in map(lambda x:tuple(x.split(':')),translate.split('+')):
        for po_file in po_files.split(','):
            if module_name == 'base':
                po_link = '%saddons/%s/i18n/%s'%(options['root-path'],module_name,po_file)
            else:
                po_link = '%s/%s/i18n/%s'%(options['addons-path'], module_name, po_file)
            options['translate-in'].append(po_link)

uri = 'http://localhost:' + str(options['port'])

server = server_thread(root_path=options['root-path'], port=options['port'],
                        netport=options['netport'], addons_path=options['addons-path'],
                        srv_mode=options['server_series'],
                        debug=opt.debug)

logger.info('start of script')
try:
    server.start_full()
    client = client_worker(uri, options)
    ost = client.get_ostimes()
    logger.info("Server started at: User: %.3f, Sys: %.3f" % (ost[0], ost[1]))

    ret = True
    mods = options['modules'] or []
    for cmd, args in cmdargs:
        try:
            if (not ret) and not cmd.startswith('+'):
                continue
            ign_result = cmd.startswith('-')
            if cmd[0] in ['-', '+']:
                cmd = cmd[1:]

            if cmd == 'create-db':
                ret = client.create_db()
            elif cmd == 'drop-db':
                ret = client.drop_db()
            elif cmd == 'install-module':
                ret = client.install_module(mods + args)
            elif cmd == 'upgrade-module':
                ret = client.upgrade_module(mods+args)
            elif cmd == 'check-quality':
                ret = client.check_quality(mods+args, options['quality-logs'])
            elif cmd == 'install-translation':
                ret = client.import_translate(options['translate-in'])
        except ClientException, e:
            logger.error("%s" % e)
            ret = False
        except xmlrpclib.Fault, e:
            logger.error('xmlrpc exception: %s', e.faultCode.strip())
            logger.error('xmlrpc +: %s', e.faultString.rstrip())
            ret = False
        except Exception, e:
            logger.exception('exc:')
            ret = False
        
        server.clear_context()
        
        if (not ret) and ign_result:
            # like make's commands, '-' means ignore result
            logger.info("Command %s failed, but will continue.", cmd)
            ret = True

        if not ret:
            logger.error("Command %s failed, stopping tests.", cmd)
        
        # end for

    ost = client.get_ostimes(ost)
    logger.info("Server ending at: User: %.3f, Sys: %.3f" % (ost[0], ost[1]))

    server.stop()
    server.join()
    if ret:
        sys.exit(0)
    else:
        sys.exit(3)
except ServerException, e:
    logger.error("%s" % e)
    server.stop()
    server.join()
    sys.exit(4)
except ClientException, e:
    logger.error("%s" % e)
    server.stop()
    server.join()
    sys.exit(5)
except xmlrpclib.Fault, e:
    logger.error('xmlrpc exception: %s', e.faultCode.strip())
    logger.error('xmlrpc +: %s', e.faultString.rstrip())
    server.stop()
    server.join()
    sys.exit(1)
except Exception, e:
    logger.exception('')
    server.stop()
    server.join()
    sys.exit(1)

logger.info('end of script')

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
