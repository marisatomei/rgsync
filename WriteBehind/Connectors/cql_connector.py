from WriteBehind.common import WriteBehindLog, WriteBehindDebug
from redisgears import getMyHashTag as hashtag
import json

def CompareIds(id1, id2):
    id1_time, id1_num = [int(a) for a in id1.split('-')]
    id2_time, id2_num = [int(a) for a in id2.split('-')]
    if(id1_time > id2_time):
        return 1
    if(id1_time < id2_time):
        return -1

    if(id1_num > id2_num):
        return 1
    if(id1_num < id2_num):
        return -1

    return 0

class CqlConnection():
    def __init__(self, user, password, db, keyspace):
        self.user = user
        self.password = password
        self.db = db
        self.keyspace = keyspace

    def _getConnectionStr(self):
        return json.dumps({'user': self.user, 'password': self.password, 'db': self.db, 'keyspace': self.keyspace})

    def Connect(self):
        from cassandra.cluster import Cluster
        from cassandra.auth import PlainTextAuthProvider

        ConnectionStr = self._getConnectionStr()

        WriteBehindLog('Connect: connecting ConnectionStr=%s' % (ConnectionStr))
        auth_provider = PlainTextAuthProvider(username=self.user, password=self.password)
        cluster = Cluster(self.db.split(), auth_provider=auth_provider)
        if self.keyspace != '':
            session = cluster.connect(self.keyspace)
        else:
            session = cluster.connect()
        WriteBehindLog('Connect: Connected')
        return session


class CqlConnector:
    def __init__(self, connection, tableName, pk, exactlyOnceTableName=None):
        self.connection = connection
        self.tableName = tableName
        self.pk = pk
        self.exactlyOnceTableName = exactlyOnceTableName
        self.exactlyOnceLastId = None
        self.shouldCompareId = True if self.exactlyOnceTableName is not None else False
        self.session = None

    def PrepereQueries(self, mappings):
        def GetUpdateQuery(tableName, mappings, pk):
            query = 'update %s set ' % tableName
            fields = ['%s=?' % (val) for kk, val in mappings.items() if not kk.startswith('_')]
            query += ','.join(fields)
            query += ' where %s=?' % (self.pk)
            return query
        self.addQuery = GetUpdateQuery(self.tableName, mappings, self.pk)
        self.delQuery = 'delete from %s where %s=?' % (self.tableName, self.pk)
        if self.exactlyOnceTableName is not None:
            self.exactlyOnceQuery = GetUpdateQuery(self.exactlyOnceTableName, {'val', 'val'}, 'id')

    def TableName(self):
        return self.tableName

    def PrimaryKey(self):
        return self.pk

    def WriteData(self, data):
        if len(data) == 0:
            WriteBehindLog('Warning, got an empty batch')
            return
        query = None

        try:
            if not self.session:
                self.session = self.connection.Connect()
                if self.exactlyOnceTableName is not None:
                    shardId = 'shard-%s' % hashtag()
                    result = self.session.execute('select val from %s where id=?' % self.exactlyOnceTableName, shardId)
                    res = result.first()
                    if res is not None:
                        self.exactlyOnceLastId = str(res['val'])
                    else:
                        self.shouldCompareId = False
        except Exception as e:
            self.session = None # next time we will reconnect to the database
            self.exactlyOnceLastId = None
            self.shouldCompareId = True if self.exactlyOnceTableName is not None else False
            msg = 'Failed connecting to Cassandra database, error="%s"' % str(e)
            WriteBehindLog(msg)
            raise Exception(msg) from None

        idsToAck = []

        try:
            from cassandra.cluster import BatchStatement
            batch = BatchStatement()
            # we have only key name, original_key, streamId, it means that the key was deleted
            isAddBatch = True if len(data[0].keys()) > 3 else False
            query = self.addQuery if isAddBatch else self.delQuery
            stmt = self.session.prepare(query)
            lastStreamId = None
            for x in data:
                lastStreamId = x.pop('streamId', None) # pop the stream id out of the record, we do not need it
                if self.shouldCompareId and CompareIds(self.exactlyOnceLastId, lastStreamId) >= 0:
                    WriteBehindLog('Skip %s as it was already writen to the backend' % lastStreamId)
                    continue
                self.shouldCompareId = False
                if len(x.keys()) == 1: # we have only key name, it means that the key was deleted
                    if isAddBatch:
                        self.session.execute(batch)
                        batch = BatchStatement()
                        isAddBatch = False
                        query = self.delQuery
                else:
                    if not isAddBatch:
                        self.session.execute(batch)
                        batch = BatchStatement()
                        isAddBatch = True
                        query = self.addQuery
                stmt = self.session.prepare(query)
                batch.add(stmt.bind(x))
            if len(batch) > 0:
                self.session.execute(batch)
                if self.exactlyOnceTableName is not None:
                    stmt = self.session.prepare(self.exactlyOnceQuery)
                    self.session.execute(stmt, {'id':shardId, 'val':lastStreamId})
        except Exception as e:
            self.session = None # next time we will reconnect to the database
            self.exactlyOnceLastId = None
            self.shouldCompareId = True if self.exactlyOnceTableName is not None else False
            msg = 'Got exception when writing to DB, query="%s", error="%s".' % ((query if query else 'None'), str(e))
            WriteBehindLog(msg)
            raise Exception(msg) from None
