from peewee import *
import datetime

from config import *

database = PostgresqlDatabase(db_name, **{'host': db_host, 'port': db_port, 'user': db_user, 'password': db_password}, autoconnect=True, autocommit=True, autorollback=True)

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

