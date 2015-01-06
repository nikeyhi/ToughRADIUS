#!/usr/bin/env python
#coding=utf-8
from pyrad import tools
from twisted.internet.defer import Deferred
from pyrad.packet import AuthPacket
from pyrad.packet import AcctPacket
from pyrad.packet import AccessRequest
from pyrad.packet import AccessAccept
from pyrad.packet import AccountingRequest
from pyrad.packet import AccountingResponse
from twisted.python import log
from Crypto.Cipher import AES
from Crypto import Random
import binascii
import base64
import datetime
import hashlib
import six

md5_constructor = hashlib.md5

_key = 't_o_u_g_h_radius'


def ndebug():
    import pdb
    pdb.set_trace()

class AESCipher:

    def __init__(self, key): 
        self.bs = 32
        self.key = hashlib.sha256(key.encode()).digest()

    def encrypt(self, raw):
        raw = self._pad(raw)
        iv = Random.new().read(AES.block_size)
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        return base64.b64encode(iv + cipher.encrypt(raw))

    def decrypt(self, enc):
        enc = base64.b64decode(enc)
        iv = enc[:AES.block_size]
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        return self._unpad(cipher.decrypt(enc[AES.block_size:])).decode('utf-8')

    def _pad(self, s):
        return s + (self.bs - len(s) % self.bs) * chr(self.bs - len(s) % self.bs)

    @staticmethod
    def _unpad(s):
        return s[:-ord(s[len(s)-1:])]

_aes = AESCipher(_key)
encrypt = _aes.encrypt
decrypt = _aes.decrypt

def is_expire(dstr):
    if not dstr:
        return False
    expire_date = datetime.datetime.strptime("%s 23:59:59"%dstr,"%Y-%m-%d %H:%M:%S")
    now = datetime.datetime.now()
    return expire_date < now
    

AcctStatusTypeMap = {
    1 : 'AccessRequest',
    2 : 'AccessAccept',
    3 : 'AccessReject',
    4 : 'AccountingRequest',
    5 : 'AccountingResponse'
}

class Storage(dict):
    def __getattr__(self, key): 
        try:
            return self[key]
        except KeyError, k:
            raise AttributeError, k
    
    def __setattr__(self, key, value): 
        self[key] = value
    
    def __delattr__(self, key):
        try:
            del self[key]
        except KeyError, k:
            raise AttributeError, k
    
    def __repr__(self):     
        return '<Storage ' + dict.__repr__(self) + '>'

class AuthDelay():
    
    def __init__(self,reject_delay=0):
        self.reject_delay = reject_delay
        self.rosters = {}
        self.delay_cache = []

    def delay_len(self):
        return len(self.delay_cache)

    def add_roster(self,mac_addr):
        if not mac_addr:
            return
        if mac_addr not in  self.rosters:
            self.rosters.setdefault(mac_addr,1)
        else:
            self.rosters[mac_addr] += 1

    def del_roster(self,mac_addr):
        if mac_addr in self.rosters:
            del self.rosters[mac_addr]

    def over_reject(self,mac_addr):
        return self.reject_delay>0 and self.rosters.get(mac_addr,0)>6

    def add_delay_reject(self,reject):
        self.delay_cache.append(reject)

    def get_delay_reject(self,idx):
        return self.delay_cache[idx]

    def pop_delay_reject(self):
        try:
            return self.delay_cache.pop(0)
        except:
            return None



class AuthPacket2(AuthPacket):

    def __init__(self, code=AccessRequest, id=None, secret=six.b(''),
            authenticator=None, **attributes):
        AuthPacket.__init__(self, code, id, secret, authenticator, **attributes)
        self.deferred = Deferred()
        self.source_user = None
        self.vendor_id = 0
        self.vlanid = 0
        self.vlanid2 = 0
        self.client_macaddr = None
        self.created = datetime.datetime.now()

    def format_str(self):
        attr_keys = self.keys()
        _str = "\nRadius Packet::%s"%AcctStatusTypeMap[self.code]
        _str += "\nhost:%s:%s" % self.source
        _str += "\nid:%s" % self.id
        _str += "\ncode:%s" % self.code
        _str += "\nAttributes: "     
        for attr in attr_keys:
            if attr == 'User-Password' or attr == 'CHAP-Password':
                _str += "\n\t%s: ******" % (attr)  
            else:
                _str += "\n\t%s: %s" % (attr, self[attr][0])   
        return _str  

    def __str__(self):
        _str = AcctStatusTypeMap[self.code]
        _str += " host=%s:%s" % self.source
        _str += ",id=%s"%self.id
        if self.code == 1:
            _str += ",username=%s,mac_addr=%s" % (self.get_user_name(),self.get_mac_addr())
        if 'Reply-Message' in self:
            _str += ',Reply-Message="%s"' % self['Reply-Message'][0]
        return _str   

    def CreateReply(self, msg=None,**attributes):
        reply = AuthPacket2(AccessAccept, self.id,
            self.secret, self.authenticator, dict=self.dict,
            **attributes)
        if msg:
            reply.set_reply_msg(tools.EncodeString(msg))
        reply.source_user = self.get_user_name()
        return reply


    def set_reply_msg(self,msg):
        if msg:self.AddAttribute(18,msg)

    def set_framed_ip_addr(self,ipaddr):
        if ipaddr:self.AddAttribute(8,tools.EncodeAddress(ipaddr))

    def set_session_timeout(self,timeout):
        if timeout:self.AddAttribute(27,tools.EncodeInteger(timeout))
   
         

    def get_nas_addr(self):
        try:return tools.DecodeAddress(self.get(4)[0])
        except:return None
        
    def get_mac_addr(self):
        if self.client_macaddr:return self.client_macaddr
        try:return tools.DecodeString(self.get(31)[0]).replace("-",":")
        except:return None

    def get_user_name(self):
        try:
            user_name = tools.DecodeString(self.get(1)[0])
            if "@" in user_name:
                user_name = user_name[:user_name.index("@")]
            return user_name
        except:
            return None

    def get_domain(self):
        try:
            user_name = tools.DecodeString(self.get(1)[0])
            if "@" in user_name:
                return user_name[user_name.index("@")+1:]
        except:
            return None            
        
    def get_vlanids(self):
        return self.vlanid,self.vlanid2

    def get_passwd(self):
        try:return self.PwDecrypt(self.get(2)[0])
        except:
            import traceback
            traceback.print_exc()
            return None        

    def get_chappwd(self):
        try:return tools.DecodeOctets(self.get(3)[0])
        except:return None    

    def verifyChapEcrypt(self,userpwd):
        if isinstance(userpwd, six.text_type):
            userpwd = userpwd.strip().encode('utf-8')   

        _password = self.get_chappwd()
        if len(_password) != 17:
            return False

        chapid = _password[0]
        password = _password[1:]

        if not self.authenticator:
            self.authenticator = self.CreateAuthenticator()
        _pwd =  md5_constructor("%s%s%s"%(chapid,userpwd,self.authenticator)).digest()
        for i in range(16):
            if password[i] != _pwd[i]:
                return False
        return True      

    def is_valid_pwd(self,userpwd):
        if not self.get_chappwd():
            return userpwd == self.get_passwd()
        else:
            return self.verifyChapEcrypt(userpwd)

class AcctPacket2(AcctPacket):
    def __init__(self, code=AccountingRequest, id=None, secret=six.b(''),
            authenticator=None, **attributes):
        AcctPacket.__init__(self, code, id, secret, authenticator, **attributes)
        self.deferred = Deferred()
        self.source_user = None
        self.vendor_id = 0
        self.client_macaddr = None
        self.ticket = {}
        self.created = datetime.datetime.now()

    def format_str(self):
        attr_keys = self.keys()
        _str = "\nRadius Packet::%s"%AcctStatusTypeMap[self.code]
        _str += "\nhost:%s:%s" % self.source
        _str += "\nid:%s" % self.id
        _str += "\ncode:%s" % self.code
        _str += "\nAttributes: "     
        for attr in attr_keys:
            _str += "\n\t%s: %s" % (attr, self[attr][0])   
        return _str  

    def __str__(self):
        _str = AcctStatusTypeMap[self.code]
        _str += " host=%s:%s" % self.source
        _str += ",id=%s"%self.id
        if self.code == 4:
            _str += ",username=%s,mac_addr=%s" % (self.get_user_name(),self.get_mac_addr())
        return _str   

    def CreateReply(self,**attributes):
        reply = AcctPacket2(AccountingResponse, self.id,
            self.secret, self.authenticator, dict=self.dict,
            **attributes)
        reply.source_user = self.get_user_name()
        return reply        

    def get_user_name(self):
        try:
            user_name = tools.DecodeString(self.get(1)[0])
            if "@" in user_name:
                return user_name[:user_name.index("@")]
            else:
                return user_name
        except:
            return None
 

    def get_mac_addr(self):
        if self.client_macaddr:return self.client_macaddr
        try:return tools.DecodeString(self.get(31)[0]).replace("-",":")
        except:return None

    def get_nas_addr(self):
        try:return tools.DecodeAddress(self.get(4)[0])
        except:return None

    def get_nas_port(self):
        try:return tools.DecodeInteger(self.get(5)[0]) or 0
        except:return 0

    def get_service_type(self):
        try:return tools.DecodeInteger(self.get(0)[0]) or 0
        except:return 0
        
    def get_framed_ipaddr(self):
        try:return tools.DecodeAddress(self.get(8)[0])
        except:return None

    def get_framed_netmask(self):
        try:return tools.DecodeAddress(self.get(9)[0])
        except:return None

    def get_nas_class(self):
        try:return tools.DecodeString(self.get(25)[0])
        except:return None   

    def get_session_timeout(self):
        try:return tools.DecodeInteger(self.get(27)[0]) or 0
        except:return 0

    def get_calling_stationid(self):
        try:return tools.DecodeString(self.get(31)[0])
        except:return None   

    def get_acct_status_type(self):
        try:return tools.DecodeInteger(self.get(40)[0])
        except:return None

    def get_acct_input_octets(self):
        try:return tools.DecodeInteger(self.get(42)[0]) or 0
        except:return 0

    def get_acct_output_octets(self):
        try:return tools.DecodeInteger(self.get(43)[0]) or 0
        except:return 0

    def get_acct_sessionid(self):
        try:return tools.DecodeString(self.get(44)[0])
        except:return None                                                         

    def get_acct_sessiontime(self):
        try:return tools.DecodeInteger(self.get(46)[0]) or 0
        except:return 0                                                             

    def get_acct_input_packets(self):
        try:return tools.DecodeInteger(self.get(47)[0]) or 0
        except:return 0                                                       

    def get_acct_output_packets(self):
        try:return tools.DecodeInteger(self.get(48)[0]) or 0
        except:return 0           

    def get_acct_terminate_cause(self):
        try:return tools.DecodeInteger(self.get(49)[0]) or 0
        except:return 0           

    def get_acct_input_gigawords(self):
        try:return tools.DecodeInteger(self.get(52)[0]) or 0
        except:return 0       

    def get_acct_output_gigawords(self):
        try:return tools.DecodeInteger(self.get(53)[0]) or 0
        except:return 0                                                         

    def get_event_timestamp(self,timetype=0):
        try:
            _time = tools.DecodeDate(self.get(55)[0])
            if timetype == 0:
                return datetime.datetime.fromtimestamp(_time).strftime("%Y-%m-%d %H:%M:%S")
            else:
                return datetime.datetime.fromtimestamp(_time-(8*3600)).strftime("%Y-%m-%d %H:%M:%S")
        except:
            return None

    def get_nas_port_type(self):
        try:return tools.DecodeInteger(self.get(61)[0]) or 0
        except:return 0   

    def get_nas_portid(self):
        try:return tools.DecodeString(self.get(87)[0])
        except:return None        

    def get_ticket(self):
        if self.ticket:return self.ticket
        self.ticket = Storage(
            account_number = self.get_user_name(),
            mac_addr = self.get_mac_addr(),
            nas_addr = self.get_nas_addr(),
            nas_port = self.get_nas_port(),
            service_type = self.get_service_type(),
            framed_ipaddr = self.get_framed_ipaddr(),
            framed_netmask = self.get_framed_netmask(),
            nas_class = self.get_nas_class(),
            session_timeout = self.get_session_timeout(),
            calling_stationid = self.get_calling_stationid(),
            acct_status_type = self.get_acct_status_type(),
            acct_input_octets = self.get_acct_input_octets(),
            acct_output_octets = self.get_acct_output_octets(),
            acct_session_id = self.get_acct_sessionid(),
            acct_session_time = self.get_acct_sessiontime(),
            acct_input_packets = self.get_acct_input_packets(),
            acct_output_packets = self.get_acct_output_packets(),
            acct_terminate_cause = self.get_acct_terminate_cause(),
            acct_input_gigawords = self.get_acct_input_gigawords(),
            acct_output_gigawords = self.get_acct_output_gigawords(),
            event_timestamp = self.get_event_timestamp(),
            nas_port_type=self.get_nas_port_type(),
            nas_port_id=self.get_nas_portid()
        )
        return self.ticket


if __name__ == '__main__':
    print AES.block_size
    a = encrypt('888888')
    print a 
    print decrypt(a)