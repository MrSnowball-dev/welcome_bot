from peewee import *
from playhouse.shortcuts import ReconnectMixin
import datetime
import logging

from config import *


class ReconnectPostgresqlDatabase(ReconnectMixin, PostgresqlDatabase):
    reconnect_errors = (
        (OperationalError, 'SSL connection has been closed unexpectedly'),
        (OperationalError, 'server closed the connection unexpectedly'),
        (OperationalError, 'terminating connection'),
        (OperationalError, 'connection not open'),
        (OperationalError, 'could not connect to server'),
        (InterfaceError, 'connection already closed'),
    )

    def _reconnect(self, func, *args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            if self.in_transaction():
                raise exc
            exc_class = type(exc)
            if exc_class not in self._reconnect_errors:
                raise exc
            exc_repr = str(exc).lower()
            for err_fragment in self._reconnect_errors[exc_class]:
                if err_fragment in exc_repr:
                    break
            else:
                raise exc
            logging.warning(f'<db_reconnect> Connection lost ({exc}), reconnecting...')
            if not self.is_closed():
                self.close()
            self.connect()
            logging.warning('<db_reconnect> Reconnected successfully')
            return func(*args, **kwargs)


database = ReconnectPostgresqlDatabase(db_name, **{'host': db_host, 'port': db_port, 'user': db_user, 'password': db_password}, autoconnect=True, autocommit=True, autorollback=True)

class UnknownField(object):
    def __init__(self, *_, **__): pass

class BaseModel(Model):
    class Meta:
        database = database

class User(BaseModel):
    id = IdentityField()
    user_id = BigIntegerField(unique=True)
    name = CharField()
    state = CharField(default='unregistered')
    language = CharField(default='en')

    class Meta:
        table_name = 'user'
        schema = 'welcome'

class Chat(BaseModel):
    id = IdentityField()
    chat_id = BigIntegerField(unique=True)
    chat_title = CharField()
    chat_owner_user_id = ForeignKeyField(column_name='chat_owner_user_id', field='user_id', model=User)
    welcome_text = TextField(default='Hi there!')
    welcome_entities = BlobField()
    welcome_type = CharField(default='text')
    welcome_file_id = CharField()
    welcome_count = IntegerField(default=0)
    last_joined = DateTimeField()
    registered_on = DateTimeField(default=datetime.datetime.now)

    class Meta:
        table_name = 'chat'
        schema = 'welcome'

class ChatSettings(BaseModel):
    id = IdentityField()
    chat_id = ForeignKeyField(column_name='chat_id', field='chat_id', model=Chat)
    auto_delete = BooleanField(default=False)
    auto_delete_svc_msg = BooleanField(default=False)
    timeout = IntegerField(default=0)
    greet_by_name = BooleanField(default=False)
    join_notification = BooleanField(default=False)
    link_preview = BooleanField(default=True)

    class Meta:
        table_name = 'chat_settings'
        schema = 'welcome'

