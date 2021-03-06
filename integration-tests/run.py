#!/usr/bin/env python

import os
import sys
import re
import termcolor
import glob
import json
import logging
import requests
from pexpect import spawn
from os.path import abspath, basename, exists, expanduser, join
from contextlib import contextmanager
from subprocess import Popen, PIPE, CalledProcessError

TOPDIR = abspath(join(os.getcwd(), '..'))
PREFIX = expanduser('~/opt/local')
SRCDIR = '/tmp/src'
INSTALLDIR = '/tmp/haiwen'
THIRDPARTDIR = expanduser('~/thirdpart')

USERNAME = 'test@seafiletest.com'
PASSWORD = 'testtest'
ADMIN_USERNAME = 'admin@seafiletest.com'
ADMIN_PASSWORD = 'adminadmin'

logger = logging.getLogger(__file__)
seafile_version = ''

TRAVIS_BRANCH = os.environ.get('TRAVIS_BRANCH', 'master')


def _color(s, color):
    return s if not os.isatty(sys.stdout.fileno()) \
        else termcolor.colored(str(s), color)


def green(s):
    return _color(s, 'green')


def red(s):
    return _color(s, 'red')


def debug(fmt, *a):
    logger.debug(green(fmt), *a)


def info(fmt, *a):
    logger.info(green(fmt), *a)


def warning(fmt, *a):
    logger.warn(red(fmt), *a)


def shell(cmd, inputdata=None, **kw):
    info('calling "%s" in %s', cmd, kw.get('cwd', os.getcwd()))
    kw['shell'] = not isinstance(cmd, list)
    kw['stdin'] = PIPE if inputdata else None
    p = Popen(cmd, **kw)
    if inputdata:
        p.communicate(inputdata)
    p.wait()
    if p.returncode:
        raise CalledProcessError(p.returncode, cmd)


@contextmanager
def cd(path):
    olddir = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(olddir)


def chdir(func):
    def wrapped(self, *w, **kw):
        with cd(self.projectdir):
            return func(self, *w, **kw)

    return wrapped


def make_build_env():
    env = dict(os.environ)
    libsearpc_dir = abspath(join(TOPDIR, 'libsearpc'))
    ccnet_dir = abspath(join(TOPDIR, 'ccnet'))

    def _env_add(*a, **kw):
        kw['env'] = env
        return prepend_env_value(*a, **kw)

    _env_add('CPPFLAGS', '-I%s' % join(PREFIX, 'include'), seperator=' ')

    _env_add('LDFLAGS', '-L%s' % os.path.join(PREFIX, 'lib'), seperator=' ')

    _env_add('LDFLAGS', '-L%s' % os.path.join(PREFIX, 'lib64'), seperator=' ')

    _env_add('PATH', os.path.join(PREFIX, 'bin'))
    _env_add('PATH', THIRDPARTDIR)
    _env_add('PKG_CONFIG_PATH', os.path.join(PREFIX, 'lib', 'pkgconfig'))
    _env_add('PKG_CONFIG_PATH', os.path.join(PREFIX, 'lib64', 'pkgconfig'))
    _env_add('PKG_CONFIG_PATH', libsearpc_dir)
    _env_add('PKG_CONFIG_PATH', ccnet_dir)

    for key in ('PATH', 'PKG_CONFIG_PATH', 'CPPFLAGS', 'LDFLAGS',
                'PYTHONPATH'):
        info('%s: %s', key, env.get(key, ''))
    return env


def prepend_env_value(name, value, seperator=':', env=None):
    '''append a new value to a list'''
    env = env or os.environ
    current_value = env.get(name, '')
    new_value = value
    if current_value:
        new_value += seperator + current_value

    env[name] = new_value
    return env


def get_project_branch(project, default_branch='master'):
    if project.name == 'seafile':
        return TRAVIS_BRANCH
    conf = json.loads(requests.get(
        'https://raw.githubusercontent.com/haiwen/seafile-test-deploy/master/branches.json').text)
    return conf.get(TRAVIS_BRANCH, conf.get('master', {})).get(project.name,
                                                               default_branch)



class Project(object):
    configure_cmd = './configure'

    def __init__(self, name):
        self.name = name
        self.version = ''

    @property
    def url(self):
        return 'https://www.github.com/haiwen/{}.git'.format(self.name)

    @property
    def projectdir(self):
        return join(TOPDIR, self.name)

    @property
    def branch(self):
        return get_project_branch(self)

    def clone(self):
        shell('git clone --depth=1 --branch {} {}'.format(self.branch,
                                                          self.url))

    @chdir
    def make_dist(self):
        info('making tarball for %s', self.name)
        if exists('./autogen.sh'):
            shell('./autogen.sh')
            shell(self.configure_cmd, env=make_build_env())
        shell('make dist')

    @chdir
    def copy_dist(self):
        self.make_dist()
        tarball = glob.glob('*.tar.gz')[0]
        info('copying %s to %s', tarball, SRCDIR)
        shell('cp {} {}'.format(tarball, SRCDIR))
        m = re.match('{}-(.*).tar.gz'.format(self.name), basename(tarball))
        if m:
            self.version = m.group(1)

    @chdir
    def use_branch(self, branch):
        shell('git checkout {}'.format(branch))


class Ccnet(Project):
    def __init__(self):
        super(Ccnet, self).__init__('ccnet')


class Seafile(Project):
    configure_cmd = './configure --enable-client --enable-server'

    def __init__(self):
        super(Seafile, self).__init__('seafile')

    @chdir
    def copy_dist(self):
        super(Seafile, self).copy_dist()
        global seafile_version
        seafile_version = self.version


class Seahub(Project):
    def __init__(self):
        super(Seahub, self).__init__('seahub')

    @chdir
    def make_dist(self):
        cmds = [
            # 'git add -f media/css/*.css',
            # 'git commit -a -m "%s"' % msg,
            './tools/gen-tarball.py --version={} --branch=HEAD >/dev/null'
            .format(seafile_version),
        ]
        for cmd in cmds:
            shell(cmd, env=make_build_env())


class SeafDAV(Project):
    def __init__(self):
        super(SeafDAV, self).__init__('seafdav')

    @chdir
    def make_dist(self):
        shell('make')


class SeafObj(Project):
    def __init__(self):
        super(SeafObj, self).__init__('seafobj')

    @chdir
    def make_dist(self):
        shell('make dist')


def build_server(libsearpc, ccnet, seafile):
    cmd = [
        'python',
        join(TOPDIR, 'seafile/scripts/build/build-server.py'),
        '--yes',
        '--version=%s' % seafile.version,
        '--libsearpc_version=%s' % libsearpc.version,
        '--ccnet_version=%s' % ccnet.version,
        '--seafile_version=%s' % seafile.version,
        '--thirdpartdir=%s' % THIRDPARTDIR,
        '--srcdir=%s' % SRCDIR,
    ]
    shell(cmd, shell=False, env=make_build_env())


def fetch_and_build():
    libsearpc = Project('libsearpc')
    ccnet = Ccnet()
    seafile = Seafile()
    seahub = Seahub()
    seafobj = SeafObj()
    seafdav = SeafDAV()

    for project in (libsearpc, ccnet, seafile, seahub, seafdav, seafobj):
        if project.name != 'seafile':
            project.clone()
        # TODO: switch to proper branch based on current seafile branch being
        # built
        project.copy_dist()

    build_server(libsearpc, ccnet, seafile)


def setup_server(db):
    '''Setup seafile server with the setup-seafile.sh script. We use pexpect to
    interactive with the setup process of the script.
    '''
    info('uncompressing server tarball')
    shell('tar xf seafile-server_{}_x86-64.tar.gz -C {}'
          .format(seafile_version, INSTALLDIR))
    if db == 'mysql':
        autosetup_mysql()
    else:
        autosetup_sqlite3()
    with open(join(INSTALLDIR, 'conf/seahub_settings.py'), 'a') as fp:
        fp.write('\n')
        fp.write('DEBUG = True')
        fp.write('\n')
        fp.write('''\
REST_FRAMEWORK = {
    'DEFAULT_THROTTLE_RATES': {
        'ping': '600/minute',
        'anon': '1000/minute',
        'user': '1000/minute',
    },
}''')
        fp.write('\n')


def autosetup_sqlite3():
    setup_script = get_script('setup-seafile.sh')
    info('setting up seafile server with pexepct, script %s', setup_script)
    answers = [
        ('ENTER', ''),
        # server name
        ('server name', 'my-seafile'),
        # ip or domain
        ('ip or domain', '127.0.0.1'),
        # seafile data dir
        ('seafile-data', ''),
        # fileserver port
        ('seafile fileserver', ''),
        ('ENTER', ''),
        ('ENTER', ''),
    ]
    _answer_questions(setup_script, answers)


def createdbs():
    sql = '''\
create database `ccnet-existing` character set = 'utf8';
create database `seafile-existing` character set = 'utf8';
create database `seahub-existing` character set = 'utf8';

create user 'seafile'@'localhost' identified by 'seafile';

GRANT ALL PRIVILEGES ON `ccnet-existing`.* to `seafile`@localhost;
GRANT ALL PRIVILEGES ON `seafile-existing`.* to `seafile`@localhost;
GRANT ALL PRIVILEGES ON `seahub-existing`.* to `seafile`@localhost;
    '''

    shell('mysql -u root', inputdata=sql)


def autosetup_mysql():
    createdbs()
    setup_script = get_script('setup-seafile-mysql.sh')
    info('setting up seafile server with pexepct, script %s', setup_script)
    if not exists(setup_script):
        print 'please specify seafile script path'
    answers = [
        ('ENTER', ''),
        # server name
        ('server name', 'my-seafile'),
        # ip or domain
        ('ip or domain', '127.0.0.1'),
        # seafile data dir
        ('seafile-data', ''),
        # fileserver port
        ('seafile fileserver', ''),
        # use existing
        ('choose a way to initialize seafile databases', '2'),
        ('host of mysql server', ''),
        ('port of mysql server', ''),
        ('Which mysql user', 'seafile'),
        ('password for mysql user', 'seafile'),
        ('ccnet database', 'ccnet-existing'),
        ('seafile database', 'seafile-existing'),
        ('seahub database', 'seahub-existing'),
        ('ENTER', ''),
    ]
    _answer_questions(abspath(setup_script), answers)


def _server_dir():
    return join(INSTALLDIR, 'seafile-server-{}'.format(seafile_version))


def get_script(path):
    return join(_server_dir(), path)


def _answer_questions(cmd, answers):
    info('expect: spawing %s', cmd)
    child = spawn(cmd)
    child.logfile = sys.stdout

    def autofill(pattern, line):
        child.expect(pattern)
        child.sendline(line)

    for k, v in answers:
        autofill(k, v)
    child.sendline('')
    child.logfile = None
    child.interact()


def start_server():
    with cd(INSTALLDIR):
        shell('find . -maxdepth 2 | sort | xargs ls -lhd')
    seafile_sh = get_script('seafile.sh')
    shell('{} start'.format(seafile_sh))

    info('starting seahub')
    seahub_sh = get_script('seahub.sh')
    answers = [
        # admin email/pass
        ('admin email', ADMIN_USERNAME),
        ('admin password', ADMIN_PASSWORD),
        ('admin password again', ADMIN_PASSWORD),
    ]
    _answer_questions('{} start'.format(abspath(seahub_sh)), answers)
    with cd(INSTALLDIR):
        shell('find . -maxdepth 2 | sort | xargs ls -lhd')
    # shell('sqlite3 ccnet/PeerMgr/usermgr.db "select * from EmailUser"', cwd=INSTALLDIR)
    shell('http -v localhost:8000/api2/server-info/ || true')
    # shell('http -v -f POST localhost:8000/api2/auth-token/ username=admin@seafiletest.com password=adminadmin || true')
    shell('netstat -nltp')


def apiurl(path):
    path = path.lstrip('/')
    return 'http://127.0.0.1:8000/api2/' + path


def create_test_user():
    data = {'username': ADMIN_USERNAME, 'password': ADMIN_PASSWORD, }
    res = requests.post(apiurl('/auth-token/'), data=data)
    debug('%s %s', res.status_code, res.text)
    token = res.json()['token']
    data = {'password': PASSWORD, }
    headers = {'Authorization': 'Token ' + token}
    res = requests.put(
        apiurl('/accounts/{}/'.format(USERNAME)),
        data=data,
        headers=headers)
    assert res.status_code == 201


def run_tests():
    run_python_seafile_tests()
    run_seafdav_tests()
    # must stop seafile server before running seaf-gc
    shell('{} stop'.format(get_script('seafile.sh')))
    shell('{} stop'.format(get_script('seahub.sh')))
    shell('{} --verbose --rm-deleted'.format(get_script('seaf-gc.sh')))


def run_python_seafile_tests():
    python_seafile = Project('python-seafile')
    if not exists(python_seafile.projectdir):
        python_seafile.clone()
        shell('pip install -r {}/requirements.txt'.format(
            python_seafile.projectdir))

    with cd(python_seafile.projectdir):
        # install python-seafile because seafdav tests needs it
        shell('python setup.py install')
        shell('py.test')


def _seafdav_env():
    env = dict(os.environ)
    env['CCNET_CONF_DIR'] = join(INSTALLDIR, 'ccnet')
    env['SEAFILE_CONF_DIR'] = join(INSTALLDIR, 'seafile-data')
    env['SEAFILE_CENTRAL_CONF_DIR'] = join(INSTALLDIR, 'conf')
    for path in glob.glob(join(_server_dir(),
                               'seafile/lib*/python*/*-packages')):
        prepend_env_value('PYTHONPATH', path, env=env)
    return env


def run_seafdav_tests():
    seafdav = SeafDAV()
    shell('pip install -r {}/test-requirements.txt'.format(seafdav.projectdir))
    with cd(seafdav.projectdir):
        shell('nosetests -v -s', env=_seafdav_env())


def setup_logging():
    kw = {
        'format': '[%(asctime)s][%(module)s]: %(message)s',
        'datefmt': '%m/%d/%Y %H:%M:%S',
        'level': logging.DEBUG,
        'stream': sys.stdout,
    }

    logging.basicConfig(**kw)
    logging.getLogger('requests.packages.urllib3.connectionpool').setLevel(
        logging.WARNING)


def _mkdirs(*paths):
    for path in paths:
        if not exists(path):
            os.mkdir(path)


def main():
    _mkdirs(SRCDIR, INSTALLDIR)
    setup_logging()
    fetch_and_build()
    for db in ('sqlite3', 'mysql'):
        shell('rm -rf {}/*'.format(INSTALLDIR))
        setup_and_test(db)


def setup_and_test(db):
    info('Setting up seafile server with %s database', db)
    setup_server(db)
    # enable webdav, we're going to seafdav tests later
    shell('''sed -i -e "s/enabled = false/enabled = true/g" {}'''
          .format(join(INSTALLDIR, 'conf/seafdav.conf')))
    try:
        start_server()
        create_test_user()
        info('Testing seafile server with %s database', db)
        run_tests()
    except:
        for logfile in glob.glob('{}/logs/*.log'.format(INSTALLDIR)):
            shell('echo {0}; cat {0}'.format(logfile))
        for logfile in glob.glob('{}/seafile-server-{}/runtime/*.log'.format(
                INSTALLDIR, seafile_version)):
            shell('echo {0}; cat {0}'.format(logfile))
        raise


if __name__ == '__main__':
    os.chdir(TOPDIR)
    main()
