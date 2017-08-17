from common.interface.phedexdbsssb import PhEDExDBSSSB
from common.interface.localdbsssb import localDBSSSB
from common.interface.mysqlstore import MySQLStore
from common.interface.dbs import DBS
from common.interface.popdb import PopDB
from common.interface.localaccess import LocalAccess
from common.interface.globalqueue import GlobalQueue
from common.interface.mysqlhistory import MySQLHistory
from common.interface.localhistory import LocalHistory
from common.interface.weblock import WebReplicaLock

class Generator(object):
    """
    Generator of various objects with a storage for singleton objects.
    """

    _singletons = {}

    def __init__(self, cls):
        self._cls = cls

    def __call__(self):
        try:
            obj = Generator._singletons[self._cls]
        except KeyError:
            obj = self._cls()
            Generator._singletons[self._cls] = obj

        return obj


class DummyInterface(object):
    def __init__(self):
        pass
            

default_interface = {
    'dataset_source': Generator(localDBSSSB),
    'site_source': Generator(localDBSSSB),
    'replica_source': Generator(localDBSSSB),
    'copy': Generator(localDBSSSB),
    'deletion': Generator(localDBSSSB),
    'store': Generator(MySQLStore),
    'history': Generator(LocalHistory)
}

demand_plugins = {
    'replica_locks': Generator(WebReplicaLock),
    'replica_access': Generator(PopDB),
    'replica_demands': Generator(LocalAccess),
    'dataset_request': Generator(GlobalQueue)
}
