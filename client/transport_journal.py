"""Plugin-owned channel and settings intent; independent concurrent operation leases.

No network/credentials/desktop effects occur here. Callers hold an operation
through every request/stream and release it only after local network work exits.
Deletion is transport evidence; it never resolves an uncertain settings write.
"""
import contextlib
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

try:
    from .instance import INSTANCE
    from .support import Failure,StateStore,origin,check_session_expiry,loads
except ImportError:
    from instance import INSTANCE
    from support import Failure,StateStore,origin,check_session_expiry,loads

LIMIT=4*1024*1024
MAX_CHANNELS=2048
MAX_OWNERS=8192
MAX_SESSIONS=2048
ID=re.compile(r'[0-9a-f]{32}\Z')
HASH=re.compile(r'[0-9a-f]{64}\Z')
SHIP=re.compile(r'~[a-z-]{1,100}\Z')
KEY=re.compile(r'[a-z][a-z0-9-]{0,127}\Z')
BOOT=re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')
NAMESPACE=re.compile(r'pid:\[[0-9]+\]\Z')


def require(value,message='Saved channel ownership is invalid; it was preserved.'):
    if not value:raise Failure('transport-state',message)
def exact(value,keys):require(type(value) is dict and set(value)==set(keys))
def token(value,pattern=ID):require(type(value) is str and pattern.fullmatch(value))
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()

def session_ref(session,expiry=False):
    require(type(session) is dict and set(session) in ({'url','ship','cookieName','cookieValue'},{'url','ship','cookieName','cookieValue','expires'}))
    require(type(session['url']) is str and origin(session['url'])==session['url']);token(session['ship'],SHIP)
    require(session['cookieName']=='urbauth-'+session['ship'] and type(session['cookieValue']) is str and re.fullmatch(r'[A-Za-z0-9._~-]{1,4096}',session['cookieValue']))
    if expiry:check_session_expiry(session)
    return digest({k:session[k] for k in ('url','ship','cookieName','cookieValue')})

def intent(desk,bucket,entry,value):
    for key in (desk,bucket,entry):token(key,KEY)
    return {'desk':desk,'bucket':bucket,'entry':entry,'value':digest(value)}

def process_identity():
    fields=Path('/proc/self/stat').read_text().rsplit(') ',1)[1].split()
    return {'pid':os.getpid(),'start':fields[19],'boot':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'namespace':os.readlink('/proc/self/ns/pid')}

def valid_owner(value):
    exact(value,('id','pid','start','boot','namespace'));token(value['id']);token(value['boot'],BOOT);token(value['namespace'],NAMESPACE)
    require(type(value['pid']) is int and value['pid']>0 and type(value['start']) is str and re.fullmatch(r'[0-9]{1,24}',value['start']))

def owner_status(value):
    valid_owner(value)
    try:
        now=process_identity()
        if value['boot']!=now['boot']:return 'dead'
        if value['namespace']!=now['namespace']:return 'unknown'
        try:fields=(Path('/proc')/str(value['pid'])/'stat').read_text().rsplit(') ',1)[1].split()
        except (FileNotFoundError,ProcessLookupError):return 'dead'
        return 'dead' if fields[19]!=value['start'] or fields[0]=='Z' else 'alive'
    except (OSError,ValueError,IndexError):return 'unknown'

class Lease:
    def __init__(self,journal):
        self.journal=journal;self.owner={'id':uuid.uuid4().hex,**process_identity()};self.live=True

class TransportJournal:
    def __init__(self,root=None,instance=INSTANCE):
        self.base=StateStore(root,instance=instance).root;self.root=self.base/'transport';self.path=self.root/'channels.json';self.instance=instance
    @staticmethod
    def directory(path):
        info=path.lstat();require(stat.S_ISDIR(info.st_mode) and info.st_uid==os.getuid() and stat.S_IMODE(info.st_mode)==0o700,'Transport directories must be private and owned.')
        require(path.absolute()==path.resolve(),'Transport paths must not contain symlinks.')
    @staticmethod
    def open(path,flags):
        fd=os.open(path,flags|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,0o600);s=os.fstat(fd)
        if not(stat.S_ISREG(s.st_mode) and s.st_uid==os.getuid() and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600 and s.st_size<=LIMIT):
            os.close(fd);raise Failure('transport-state','Transport files must be bounded, private and owned.')
        return fd
    @staticmethod
    def sync(path):
        fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:os.fsync(fd)
        finally:os.close(fd)
    @contextlib.contextmanager
    def transaction(self,create=False):
        if create:
            self.base.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.directory(self.base)
        fd=self.open(self.base/'transport.lock',os.O_RDWR|os.O_CREAT)
        try:
            deadline=time.monotonic()+2
            while True:
                try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:
                    if time.monotonic()>=deadline:raise Failure('busy','Another transport journal operation is running.',True) from None
                    time.sleep(.01)
            if create:
                try:self.root.mkdir(mode=0o700)
                except FileExistsError:pass
                else:
                    (self.root/'owners').mkdir(mode=0o700);self.sync(self.base);self.sync(self.root)
                    self.write({'version':2,'plugin_id':self.instance.plugin_id,'profile':self.instance.profile,'channels':[],'sessions':[]})
            self.directory(self.root);self.directory(self.root/'owners')
            if create:self.upgrade()
            yield
        finally:os.close(fd)
    def validate(self,value):
        require(type(value) is dict and type(value.get('version')) is int and value['version'] in (1,2))
        exact(value,('version','plugin_id','profile','channels') if value['version']==1 else ('version','plugin_id','profile','channels','sessions'))
        require((value['plugin_id'],value['profile'])==(self.instance.plugin_id,self.instance.profile),'Transport state belongs to a different plugin instance.')
        require(type(value['channels']) is list and len(value['channels'])<=MAX_CHANNELS)
        ids=set();channels=set();requests=set()
        for row in value['channels']:
            exact(row,('id','channel','origin','ship','session_ref','owner','owner_released','kind','intent','outcome','phase','cleanup_request','cleanup_actor','cleanup_released','delete_status'))
            token(row['id']);token(row['channel']);token(row['session_ref'],HASH);valid_owner(row['owner'])
            require(row['id'] not in ids and row['channel'] not in channels);ids.add(row['id']);channels.add(row['channel'])
            require(type(row['origin']) is str and origin(row['origin'])==row['origin']);token(row['ship'],SHIP)
            require(type(row['owner_released']) is bool and type(row['cleanup_released']) is bool and row['kind'] in ('watch','poke'))
            if row['kind']=='watch':require(row['intent'] is None and row['outcome']=='none')
            else:
                exact(row['intent'],('desk','bucket','entry','value'))
                for k in ('desk','bucket','entry'):token(row['intent'][k],KEY)
                token(row['intent']['value'],HASH);require(row['outcome'] in ('uncertain','acknowledged','rejected','state-confirmed'))
            require(row['phase'] in ('open','closing','closed'))
            if row['phase']=='open':require(row['cleanup_request'] is None and row['cleanup_actor'] is None and row['cleanup_released'] is False and row['delete_status'] is None)
            else:
                token(row['cleanup_request']);valid_owner(row['cleanup_actor']);require(row['cleanup_request'] not in requests);requests.add(row['cleanup_request'])
                if row['phase']=='closing':require(row['delete_status'] is None)
                else:require(type(row['delete_status']) is int and row['delete_status'] in (200,204,303,401))
        if value['version']==2:
            require(type(value['sessions']) is list and len(value['sessions'])<=MAX_SESSIONS)
            refs=set()
            for row in value['sessions']:
                exact(row,('origin','ship','session_ref','retired'))
                require(type(row['origin']) is str and origin(row['origin'])==row['origin']);token(row['ship'],SHIP);token(row['session_ref'],HASH)
                require(type(row['retired']) is bool and row['session_ref'] not in refs);refs.add(row['session_ref'])
            for row in value['channels']:
                require(any(all(row[k]==session[k] for k in ('origin','ship','session_ref')) for session in value['sessions']))
        return value
    def upgrade(self):
        value=self.read()
        if value['version']==2:return
        require(all(self.recoverable(row) for row in value['channels']),
                'Stop earlier Theme clients before upgrading their transport journal.')
        value['version']=2;value['sessions']=[]
        for row in value['channels']:
            if not any(s['session_ref']==row['session_ref'] for s in value['sessions']):
                value['sessions'].append({**{k:row[k] for k in ('origin','ship','session_ref')},'retired':False})
        self.write(value)
    def register(self,value,session):
        ref=session_ref(session)
        row=next((r for r in value['sessions'] if r['session_ref']==ref),None)
        if row is None:
            require(len(value['sessions'])<MAX_SESSIONS,'Session history is full; no evidence was discarded.')
            row={'origin':session['url'],'ship':session['ship'],'session_ref':ref,'retired':False}
            value['sessions'].append(row)
        return row
    def assert_admitted(self,session):
        ref=session_ref(session)
        value=self.status()
        if value is not None:
            require(not any(r['session_ref']==ref and r['retired'] for r in value.get('sessions',[])),
                    'This connection is disconnecting. Its saved credential is reserved for cleanup; retry Disconnect.')
    def origin_lock(self,url):
        require(origin(url)==url)
        return self.open(self.base/('transport-origin-'+digest(url)+'.lock'),os.O_RDWR|os.O_CREAT)
    @contextlib.contextmanager
    def admitted(self,session):
        # The shared lock covers the whole channel or read, including its local
        # response teardown. Retiring callers fence admission before waiting for EX.
        fd=None
        try:
            with self.transaction(create=True):
                fd=self.origin_lock(session['url'])
                try:fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
                except BlockingIOError:raise Failure('busy','This connection is being disconnected.',True) from None
                value=self.read();row=self.register(value,session)
                require(not row['retired'],'This connection is disconnecting; retry Disconnect.')
                if self.read()!=value:self.write(value)
            yield
        finally:
            if fd is not None:os.close(fd)
    @contextlib.contextmanager
    def retiring(self,url,session=None,timeout=35):
        # Session fingerprints remain fenced after success and process loss.
        # Fresh login uses a new session; it never reopens a retired credential.
        if session is not None:require(session['url']==url)
        with self.transaction(create=True):
            value=self.read();before=copy.deepcopy(value)
            if session is not None:self.register(value,session)
            for row in value['sessions']:
                if row['origin']==url:row['retired']=True
            if value!=before:self.write(value)
            else:self.sync(self.root)
            fd=self.origin_lock(url)
        try:
            deadline=time.monotonic()+timeout
            while True:
                try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:
                    if time.monotonic()>=deadline:
                        raise Failure('transport-busy','Existing requests are still stopping. The account and credential were kept; retry Disconnect.',True) from None
                    time.sleep(.05)
            with self.transaction():
                value=self.read()
                require(all(self.recoverable(row) for row in value['channels'] if row['origin']==url),
                        'A channel owner is still active or unverified. The account and credential were kept; retry Disconnect.')
            yield
        finally:os.close(fd)
    def read(self):
        self.directory(self.root);self.directory(self.root/'owners')
        try:fd=self.open(self.path,os.O_RDONLY)
        except FileNotFoundError:raise Failure('transport-state','Transport journal is missing; prior ownership was not recreated.') from None
        with os.fdopen(fd,'rb') as f:raw=f.read(LIMIT+1)
        require(len(raw)<=LIMIT)
        return self.validate(loads(raw))
    def write(self,value):
        self.validate(value);raw=canonical(value)+b'\n';require(len(raw)<=LIMIT,'Transport history is full; no evidence was discarded.')
        temp=self.root/('.pending-'+uuid.uuid4().hex);fd=self.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL)
        with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        os.replace(temp,self.path);self.sync(self.root)
    def status(self):
        if not self.root.exists() and not self.root.is_symlink():return None
        return copy.deepcopy(self.read())
    def lease(self,lease):
        require(lease.journal is self and lease.live,'A live operation lease is required.')
        require({k:v for k,v in lease.owner.items() if k!='id'}==process_identity(),'The transport owner process changed.')
    @contextlib.contextmanager
    def operation(self):
        with self.transaction(create=True):
            self.read();require(sum(1 for _ in (self.root/'owners').iterdir())<MAX_OWNERS,'Transport owner history is full; preserve it before proceeding.');lease=Lease(self)
            fd=self.open(self.root/'owners'/lease.owner['id'],os.O_RDWR|os.O_CREAT|os.O_EXCL)
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);self.sync(self.root/'owners')
            except BaseException:os.close(fd);raise
        try:yield lease
        finally:
            try:
                # Caller must have exited every local request/stream before this
                # point. If persistence fails, live-owner uncertainty is retained.
                with self.transaction():
                    value=self.read();changed=False
                    for row in value['channels']:
                        for role in ('owner','cleanup_actor'):
                            key='owner_released' if role=='owner' else 'cleanup_released'
                            if row[role]==lease.owner and not row[key]:row[key]=True;changed=True
                    if changed:self.write(value)
            finally:lease.live=False;os.close(fd)
    def row(self,value,ident):
        token(ident);row=next((r for r in value['channels'] if r['id']==ident),None);require(row is not None,'Saved channel identity is missing.');return row
    def available(self,owner,released,lease=None):
        if lease is not None and owner==lease.owner:return True
        if not released and owner_status(owner)!='dead':return False
        # Released flags alone do not permit overlapping the still-exiting owner.
        fd=self.open(self.root/'owners'/owner['id'],os.O_RDWR)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);return True
            except BlockingIOError:return False
        finally:os.close(fd)
    def recoverable(self,row,lease=None):
        return self.available(row['owner'],row['owner_released'],lease) and (row['cleanup_actor'] is None or self.available(row['cleanup_actor'],row['cleanup_released'],lease))
    def active(self,row):
        if row['phase']!='open' or row['owner_released'] or owner_status(row['owner'])!='alive':return False
        fd=self.open(self.root/'owners'/row['owner']['id'],os.O_RDWR)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);return False
            except BlockingIOError:return True
        finally:os.close(fd)
    def pending(self,session):
        ref=session_ref(session)
        with self.transaction():
            rows=[]
            for row in self.read()['channels']:
                if row['phase']=='closed' or row['origin']!=session['url']:continue
                if self.recoverable(row):
                    require(row['session_ref']==ref,'Abandoned channels require their original saved session.')
                    rows.append(copy.deepcopy(row))
                else:
                    require(self.active(row),'A channel owner cannot be verified or cleanup is unfinished; recovery is paused.')
            return rows
    def begin(self,lease,session,kind,write_intent=None):
        self.lease(lease);ref=session_ref(session,expiry=True);require(kind in ('watch','poke'))
        if kind=='watch':require(write_intent is None)
        else:
            exact(write_intent,('desk','bucket','entry','value'))
            for k in ('desk','bucket','entry'):token(write_intent[k],KEY)
            token(write_intent['value'],HASH)
        with self.transaction():
            value=self.read();binding=self.register(value,session)
            require(not binding['retired'],'This connection is disconnecting; retry Disconnect.')
            for row in value['channels']:
                if row['origin']!=session['url']:continue
                if row['phase']!='closed':
                    require(not self.recoverable(row,lease),'Clean up the previous channel before opening another.')
                    require(self.active(row),'A channel owner cannot be verified or cleanup is unfinished.')
                if kind=='poke' and row['ship']==session['ship'] and row['kind']=='poke' and row['outcome']=='uncertain':
                    require(any(row['intent'][k]!=write_intent[k] for k in ('desk','bucket','entry')),'A settings update is unresolved; observe it before writing this entry again.')
            require(len(value['channels'])<MAX_CHANNELS,'Transport history is full; no evidence was discarded.')
            row={'id':uuid.uuid4().hex,'channel':uuid.uuid4().hex,'origin':session['url'],'ship':session['ship'],'session_ref':ref,'owner':copy.deepcopy(lease.owner),'owner_released':False,'kind':kind,'intent':copy.deepcopy(write_intent),'outcome':'none' if kind=='watch' else 'uncertain','phase':'open','cleanup_request':None,'cleanup_actor':None,'cleanup_released':False,'delete_status':None}
            value['channels'].append(row);self.write(value);return copy.deepcopy(row)
    def prepare_cleanup(self,lease,ident,session):
        self.lease(lease);ref=session_ref(session)
        with self.transaction():
            value=self.read();row=self.row(value,ident)
            require(row['session_ref']==ref,'Cleanup requires the exact saved session.')
            if row['phase']=='closed':self.sync(self.root);return copy.deepcopy(row)
            require(self.recoverable(row,lease),'The channel operation is active or its owner is unknown.')
            if row['phase']=='open':row['phase']='closing';row['cleanup_request']=uuid.uuid4().hex
            if row['cleanup_actor']!=lease.owner:row['cleanup_actor']=copy.deepcopy(lease.owner);row['cleanup_released']=False;self.write(value)
            else:self.sync(self.root)
            return copy.deepcopy(row)
    def deleted(self,lease,ident,request,status):
        require(type(status) is int and status in (200,204),'Exact successful deletion response is required.')
        return self.closed(lease,ident,request,status)
    def closed_after_logout(self,lease,ident,request,status):
        # 303/401 records session-only logout, NOT a successful channel DELETE.
        # Eyre +close-session discards the session's associated channels. Keep
        # original poke outcomes unchanged; logout proves no application write.
        require(type(status) is int and status in (303,401),'Exact session logout response is required.')
        return self.closed(lease,ident,request,status)
    def closed(self,lease,ident,request,status):
        self.lease(lease);token(request)
        with self.transaction():
            value=self.read();row=self.row(value,ident);require(row['cleanup_request']==request,'Cleanup request identity changed.')
            if row['phase']=='closed':self.sync(self.root);return copy.deepcopy(row)
            require(row['phase']=='closing' and row['cleanup_actor']==lease.owner,'Only the admitted cleaner may record its response.')
            row['phase']='closed';row['delete_status']=status;self.write(value);return copy.deepcopy(row)
    def receipt(self,lease,ident,outcome):
        self.lease(lease);require(outcome in ('acknowledged','rejected'))
        with self.transaction():
            value=self.read();row=self.row(value,ident);require(row['owner']==lease.owner and row['kind']=='poke' and row['phase']=='open')
            require(row['outcome'] in ('uncertain',outcome),'Application receipt changed.')
            if row['outcome']==outcome:self.sync(self.root);return copy.deepcopy(row)
            row['outcome']=outcome;self.write(value);return copy.deepcopy(row)
    def observe(self,lease,ident,session,write_intent):
        # ident must be captured BEFORE the authenticated read. Matching current
        # desired state is not a recovered ACK and does not authorize a resend.
        self.lease(lease);ref=session_ref(session)
        with self.transaction():
            value=self.read();row=self.row(value,ident)
            require(row['kind']=='poke' and row['session_ref']==ref and row['intent']==write_intent,'Settings observation does not match the saved intent.')
            require(row['phase']=='closed' and self.available(row['owner'],row['owner_released']),'The original write has not stopped.')
            require(row['outcome'] in ('uncertain','state-confirmed'))
            if row['outcome']=='state-confirmed':self.sync(self.root);return copy.deepcopy(row)
            row['outcome']='state-confirmed';self.write(value);return copy.deepcopy(row)
