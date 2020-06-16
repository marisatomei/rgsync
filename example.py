from rgsync import RGWriteBehind, RGWriteThrough
from rgsync.Connectors import MySqlConnector, MySqlConnection

host_redis = '192.168.31.60:8086'
user = '12'
password = '12'
db = 'events'

connection = InfluxDbConnection(user, password, host_redis, db)

'''
Create MySQL measures connector
measures - MySQL table to put the data
measure_id - primary key
'''
measuresConnector = InfluxDbConnector(connection, 'measures', 'measure_id')

measuresMappings = {
	'test':'test',
	'value':'value'
}

RGWriteBehind(GB,  keysPrefix='measure', mappings=measuresMappings, connector=measuresConnector, name='MeasuresWriteBehind',  version='99.99.99')